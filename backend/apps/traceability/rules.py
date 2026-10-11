"""
业务规则集中定义（状态机 + 签发前置校验）

★ 为什么单独抽一个模块：
  原实现里「状态能不能转」「能不能签发」这些判断散落在 submit / issue /
  batch_issue / respond 各处，同一个规则被复制粘贴，很容易只改一处。
  典型后果就是安全审计发现的：
    - 单张 issue 拦了报告，batch_issue 忘了拦；
    - submit 允许 suspended 送审，issue 又没限制来源状态；
    - 意向单 respond 不校验状态转移。
  这里把「状态机」和「业务前置条件」收敛成唯一来源，视图只调用不重复实现。

用法（视图里）：
    from . import rules
    err = rules.check_cert_issue(cert)          # 返回 None 表示通过，否则返回错误文案
    if err:
        return Response({"code": 400, "message": err}, status=400)

    err = rules.check_intent_transition(intent, new_status, actor)

===============================================================
★★ 项目约定：否定性终态优先于一切外部证据
===============================================================
「不可逆的否定性事实」——作废（revoked）、封禁（disabled）、吊销等 ——
在本地一经确立，就是**终局判定**：

  1. **优先**：它优先于任何外部证据（链上查询结果、缓存、第三方回执）。
     外部证据**只能补充信息，不能翻案**。
     反例（第五轮审计 REVOKED-VS-CHAIN-VERIFIED-CONTRADICTION）：
       本地已作废、链上撤销因故未生效 → 链上仍返回 matched=True
       → 若照单全收，页面会同时出现「已作废」与「链上核验通过」两个矛盾结论。

  2. **贯彻到底**：由否定性终态**派生的所有展示字段都必须同步置否**。
     不能只改主结论（trusted / revoked）而漏掉派生结论
     （chain_verified、anchored，以及后续新增的任何「通过/可信」类字段）。
     ★ 新增派生字段时，务必回到 services.verify_certificate 末尾的
       「最终防线」块，确认它也在这里被置否。

  3. **实现方式**：在 `verify_certificate` 的最终防线集中处理（而不是散落在
     各分支），并配「链上返回相反结论时仍不可信」的回归测试。

本约定对合格证（suspended/revoked）、主体（REJECTED）、意向单（rejected/done）
等所有带终态的实体同样适用。
"""

# ===============================================================
# 一、合格证状态机
# ===============================================================
# 允许的状态转移：{来源状态: {可去往的状态集合}}
# 设计依据（与论文/答辩口径一致）：
#   draft    → pending / issued        ：提交送审 / 管理员快捷直接签发
#   pending  → issued / draft / revoked：签发 / 驳回退回草稿 / 直接作废
#   issued   → suspended / revoked     ：监督检查不合格暂停 / 作废
#   suspended→ revoked                 ：整改复查通过后原证作废（终态，不复原）
#   revoked  → （无）                   ：终态
#
# ★ 续审修复：`draft → issued` 必须存在 —— `CERT_ISSUABLE_FROM` 明确允许
#   「草稿直接签发」（管理员快捷通道，测试 `test_issue_assigns_cert_no_via_api`
#   即按此路径走）。原状态机表漏了这一条，导致接入状态机后该路径被自己拦住。
#   两张表必须一致：**能签发的来源状态 ⊆ 状态机能到达 issued 的来源状态**。
#
# ★ 关键点：suspended **不允许**回到 pending 或 issued。
#   暂停后必须走「整改复查」流程：复查通过 → 原证转 revoked，
#   生产方对新批次重新申领。若允许 suspended→pending，就会出现
#   「停用中的证自己又变回已签发」，与设计冲突。
CERT_TRANSITIONS = {
    "draft": {"pending", "issued"},
    "pending": {"issued", "draft", "revoked"},
    "issued": {"suspended", "revoked"},
    "suspended": {"revoked"},
    "revoked": set(),
}

# 允许执行「签发」的来源状态。
#   - draft / pending：正常签发
#   - issued：★ 幂等重签（保持原编号，不换号）—— 原设计即如此，
#             批量签发/网络重试场景需要，且不会造成状态跃迁或权限绕过。
# ★ 显式排除 suspended / revoked —— 防止暂停证被重新签发。
CERT_ISSUABLE_FROM = {"draft", "pending", "issued"}


def can_transition_cert(from_status: str, to_status: str) -> bool:
    """合格证状态转移是否合法（状态机唯一来源）。"""
    return to_status in CERT_TRANSITIONS.get(from_status, set())


def check_cert_transition(cert, to_status: str) -> str | None:
    """
    校验一次状态转移是否被状态机允许（视图各入口共用）。

    返回 None 表示允许；否则返回错误文案。

    ★ 续审修复：此前 `can_transition_cert` 只在测试里被用到，业务视图
    （submit / reject / revoke / 检查联动）仍各自硬编码判断 —— 规则虽集中
    定义了，但**并未成为唯一来源**，改一处漏一处的问题依旧存在。
    现在这些入口统一调用本函数，状态机真正生效。

    ⚠️ 例外：`issued` 允许「幂等重签」（源状态=目标状态），
    这是批量签发/网络重试的正常场景，不算非法跃迁。

    ⚠️ 并发提示：本函数是**基于内存对象的检查**，单独使用存在检查-写入
    竞态窗口（第三轮审计 High）。真正的原子写入请配合
    `atomic_cert_transition()` 使用（带原状态条件更新）。
    """
    cur = getattr(cert, "status", None)
    if cur == to_status:
        return None           # 幂等：同态转移一律放行
    if not can_transition_cert(cur, to_status):
        return (f"不允许的状态流转：{cur} → {to_status}"
                "（状态机见 apps/traceability/rules.py）")
    return None


def atomic_cert_transition(cert, to_status: str, extra_fields: dict | None = None) -> bool:
    """
    ★ 并发安全的状态转移（第三轮审计 High 修复）。

    问题：原实现是「先读取对象 → 检查状态 → 保存」，三步之间没有原子性。
    并发场景下，签发请求可能依据**旧状态**通过校验，再覆盖另一请求刚写入的
    `suspended` —— 出现「被暂停的证又被签发了」这类状态错乱。

    做法：**带原状态条件的 UPDATE**（乐观并发控制）：
        UPDATE certificate SET status=<to>[, ...] WHERE id=? AND status=<from>
      只有当数据库里仍是「我读到的那个状态」时才更新成功。
      - rowcount == 1 → 转移成功
      - rowcount == 0 → 状态已被并发修改，本次放弃（调用方应提示重试）

    ★ 为什么不用 select_for_update：
      项目默认 SQLite，而 SQLite 不支持行级 `SELECT ... FOR UPDATE`（会被忽略）。
      条件更新是**跨数据库都可靠**的方案（MySQL/SQLite 行为一致）。

    返回 True 表示本次转移成功并在数据库生效（同时同步内存对象）。
    """
    from .models import Certificate

    cur = getattr(cert, "status", None)
    fields = {"status": to_status, "updated_at": _now()}
    if extra_fields:
        fields.update(extra_fields)

    updated = (Certificate.objects
               .filter(id=cert.id, status=cur)
               .update(**fields))
    if updated:
        # 同步内存对象，避免调用方再读到旧值
        for k, v in fields.items():
            setattr(cert, k, v)
    return bool(updated)


def _now():
    from django.utils import timezone
    return timezone.now()


def check_cert_issue(cert) -> str | None:
    """
    签发前置校验（单张 issue 与批量 batch_issue **共用**）。

    返回 None 表示可以签发；否则返回给用户看的错误文案。

    校验项：
      1. 状态可签发（draft / pending，或 issued 的幂等重签），
         排除 suspended / revoked
      2. 必须关联检测报告
      3. 报告结论必须为「合格」
      4. 报告与合格证必须属于**同一批次**（防止张冠李戴）
      5. 批次必须关联生产主体（分配编号需要）
    """
    status = getattr(cert, "status", None)

    if status == "revoked":
        return "已作废的合格证不可签发"
    if status not in CERT_ISSUABLE_FROM:
        if status == "suspended":
            return ("该合格证处于「暂停使用」状态，不可直接签发。"
                    "请按整改流程处理：监督检查复查通过后原证将转作废，"
                    "届时对新批次重新申领。")
        return f"当前状态（{status}）不可签发"

    # 必须有报告
    report = getattr(cert, "report", None)
    if not getattr(cert, "report_id", None) or report is None:
        return "缺少关联的检测报告，不可签发"

    # ★ 报告结论必须合格（审计 High：原实现只查 report_id 是否存在）
    if report.conclusion != "qualified":
        return (f"检测报告 {report.report_no} 结论为「{report.get_conclusion_display()}」，"
                "不可用于签发合格证")

    # ★ 报告必须属于同一批次（审计 High：防止引用别批次报告）
    if cert.batch_id and report.batch_id and report.batch_id != cert.batch_id:
        return (f"检测报告 {report.report_no} 不属于本合格证的批次，不可签发")

    # 批次须有生产主体（分配 19 位编号时需要主体区域码）
    producer = None
    if cert.batch_id and getattr(cert, "batch", None) is not None:
        producer = cert.batch.producer
    if producer is None:
        return "合格证未关联生产主体，无法分配编号"

    return None


# ===============================================================
# 二、产销意向单状态机
# ===============================================================
#   pending  → accepted / rejected   （供需单发布方回应）
#   accepted → done                  （发起方确认完成）
#   rejected → （无）                 （终态）
#   done     → （无）                 （终态）
INTENT_TRANSITIONS = {
    "pending": {"accepted", "rejected"},
    "accepted": {"done"},
    "rejected": set(),
    "done": set(),
}


def can_transition_intent(from_status: str, to_status: str) -> bool:
    return to_status in INTENT_TRANSITIONS.get(from_status, set())


def check_intent_respond(intent, new_status: str, actor) -> str | None:
    """
    回应意向单的前置校验。

    返回 None 表示允许；否则返回错误文案。

    校验（对应审计 High：respond 未核验回应人、未限制状态流转）：
      1. new_status 必须是合法取值
      2. ★ 状态转移必须合法（不能把 rejected 改回 pending，也不能跳步）
      3. ★ 必须由「对端」操作：
         - 供需单发布者（supply.publisher）可以 接受/拒绝（pending → accepted/rejected）
         - 意向发起人（initiator）  可以 确认完成（accepted → done）
         - 发起人不能替对方接受/拒绝自己的意向
    """
    valid = {c[0] for c in intent.Status.choices}
    if new_status not in valid:
        return "状态非法"

    if not can_transition_intent(intent.status, new_status):
        cur = intent.get_status_display()
        nxt = dict(intent.Status.choices).get(new_status, new_status)
        return f"不允许的状态流转：{cur} → {nxt}"

    is_publisher = (intent.supply is not None
                    and intent.supply.publisher_id == actor.id)
    is_initiator = (intent.initiator_id == actor.id)
    is_operator = actor.role in ("admin", "regulator")

    if new_status in ("accepted", "rejected"):
        # 只能由供需单发布方回应；发起人不能替对方拍板
        if not (is_publisher or is_operator):
            return "只有供需单发布方可以接受或拒绝该意向"
    elif new_status == "done":
        # 完成确认：发起方或发布方都可（实操中双方确认后由任一方落终态）
        if not (is_initiator or is_publisher or is_operator):
            return "无权操作该意向单"

    return None
