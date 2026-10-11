"""
可信存证核心服务

设计要点：
1. 哈希在后端生成（前端不可信）—— 前端只能提交业务数据，哈希由服务端按统一规则计算。
2. 规范化拼接：把关键字段按固定顺序、固定分隔符拼接，保证同一数据每次计算结果一致。
3. 存证留痕：写入 EvidenceRecord，并尝试上链到 FISCO BCOS（chain.py）。
   链不可用时自动降级为模拟存证（tx_hash 为本地随机值、block_height=0），
   业务不中断，便于无链环境下开发演示。
4. 验真比对：重新读取当前字段重算哈希，与存证记录比对，识别是否被篡改。
"""
import hashlib
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction

from . import chain
from .models import Certificate, EvidenceRecord


def _fmt(value):
    """
    把字段值统一格式化为字符串，保证「同一语义的数据」在任何时刻拼出的原文完全一致：
      None -> 空串
      日期时间 -> 固定格式字符串
      数值(int/float/Decimal) -> 统一两位小数（避免 5000 与 5000.00 拼接结果不同）
      其余 -> 去空格字符串
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if hasattr(value, "strftime"):  # date
        return value.strftime("%Y-%m-%d")
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float, Decimal)):
        try:
            return str(Decimal(str(value)).quantize(Decimal("0.01")))
        except (InvalidOperation, ValueError):
            return str(value)
    return str(value).strip()


def _encode_field(value) -> str:
    """
    ★ 单字段编码（安全修复：审计 Medium「哈希原文用 | 拼接可致歧义碰撞」）。

    原实现把各字段直接用 '|' 拼接，字段内容**未做转义**：
      ("张三|李四", "柑橘")  与  ("张三", "李四|柑橘")
    拼出来的原文完全相同 —— 字段边界被语义迁移，但摘要不变。
    这违反「一物一摘要」的存证前提。

    修法：改用「长度前缀」编码 —— 每个字段写成
        <字符数>:<内容>
    分隔符只作为可读性辅助，**判定边界靠长度**，因此字段内出现任何
    字符（含 '|'、换行）都不会造成歧义。这是 netstring 的经典做法。
    """
    s = _fmt(value)
    return f"{len(s)}:{s}"


# ★ 存证原文格式版本。修改编码方式时必须递增，便于历史数据区分与迁移。
CERT_PAYLOAD_VERSION = "v2"
BATCH_PAYLOAD_VERSION = "v2"
REPORT_PAYLOAD_VERSION = "v2"


def build_certificate_payload(cert: Certificate) -> str:
    """
    构造合格证的规范化原文（参与哈希计算的字段集合）。

    ★ 安全修复（审计 High/Medium）：
      1. 关联数据纳入摘要 —— 原来只存「批次编号、报告编号」这类**引用值**，
         批次产地/品种、报告结论/日期/机构被改了，证书摘要不变、验真照样绿。
         现在把关联对象的**关键业务字段**一并编码进原文：这些字段一变，
         证书摘要必然变，公众页展示值与「可信」结论不再脱节。
      2. 拼接改为长度前缀编码，消除 '|' 歧义（见 _encode_field）。

    字段顺序固定，保证同一份数据每次算出同一摘要。
    末尾带上版本号，便于后续演进与历史数据识别。
    """
    batch = cert.batch if cert.batch_id else None
    report = cert.report if cert.report_id else None

    fields = [
        # ---- 合格证自身 ----
        cert.cert_no,
        cert.producer_name,
        cert.product_name,
        cert.quantity_kg,
        _fmt(cert.issue_date),
        _fmt(cert.valid_until),
        # ---- 关联批次（★ 新增：产地/品种等会被公众页展示的关键字段）----
        batch.batch_no if batch else "",
        batch.product_name if batch else "",
        batch.variety if batch else "",
        batch.origin if batch else "",
        batch.weight_kg if batch else "",
        # ---- 关联检测报告（★ 新增：结论/日期/机构 —— 决定能否流通的事实依据）----
        report.report_no if report else "",
        report.conclusion if report else "",
        report.test_org if report else "",
        _fmt(report.test_date) if report else "",
        # ---- 格式版本 ----
        CERT_PAYLOAD_VERSION,
    ]
    return "|".join(_encode_field(f) for f in fields)


def _salt() -> str:
    """取存证盐值（加盐哈希用）。缺失时回退空串，保证本地无配置也能跑。"""
    return getattr(settings, "EVIDENCE_SALT", "") or ""


def sha256_hex(text: str) -> str:
    """加盐 SHA-256 摘要。

    哈希 = sha256(salt + text)。盐由后端私有参数（EVIDENCE_SALT）提供，
    抵御彩虹表/字典逆向 —— 即使拿到规范化原文与摘要，也无法离线枚举敏感字段。
    注意：salt 只参与哈希，不写入 payload（payload 仍存明码原文供追溯展示）。
    """
    return hashlib.sha256((_salt() + text).encode("utf-8")).hexdigest()


def compute_certificate_hash(cert: Certificate) -> str:
    return sha256_hex(build_certificate_payload(cert))


def _anchor(digest: str, payload: str, biz_type: str, biz_id: int, biz_no: str) -> dict:
    """
    统一上链入口：把摘要写入联盟链，返回 chain.send_evidence 的结果。
    关闭链功能（FISCO_BCOS.ENABLE_CHAIN=False）时直接走模拟。

    说明：`payload`（存证原文）当前**不参与上链**（链上只锚定摘要，原文存本地库，
    这是隐私与成本考量）；保留该参数是为了调用处语义清晰与将来扩展
    （如需链上存原文可直接取用）。`biz_id` 同理，本层未用到，仅供上层语义完整。
    """
    if not getattr(settings, "FISCO_BCOS", {}).get("ENABLE_CHAIN", True):
        return {"ok": False, "tx_hash": chain._mock_tx_hash(), "block_height": 0,
                "mode": "mock", "message": "链功能已关闭，使用模拟存证"}
    return chain.send_evidence(data_hash=digest, biz_no=biz_no, biz_type=biz_type)


def _save_record(biz_type: str, biz_id: int, biz_no: str, digest: str,
                 payload: str, result: dict) -> EvidenceRecord:
    """
    把上链结果写入存证记录（含链上交易哈希与区块高度）。

    ★ 追加式版本（B3 修复：替代早期的「冻结原摘要」语义）：

    背景 —— 合约是**单值槽**（`records[bizType][bizNo]` 重复上链会覆盖），
    所以链上永远只有「最新一次」的摘要。早期「摘要分歧就冻结本地 hash」的做法
    会与链上分叉：本地存旧、链上存新 → 公众验真页每张重存证过的证都变红，
    且 rehash_all（加盐迁移的唯一用途）整条路被堵死。

    现在改为**追加式版本**：
      - 首次存证：version=1, is_current=True；
      - 摘要相同：在原行上补齐链上信息（如 mock → 真上链）；
      - 摘要不同（重存证 / 加盐迁移）：把原行 is_current=False 归档，
        **追加**一条 version+1, is_current=True 的新行。
    这样：① 历史版本全部留痕（可展示「摘要演进：旧→新」）；
          ② 当前比对以 is_current 为准，与链上单槽天然一致；
          ③ 防篡改与「合法重存证/换盐迁移」不再互斥。
    """
    chain_ok = "success" if result.get("ok") else result.get("mode", "mock")

    # ★ R1：三个分支各自的本地写全部包在 transaction.atomic() 中。
    #   原因：分支 3「归档旧行 + 新建新行」若两步之间进程被杀/超时，
    #   会留下「无任何 is_current 版本」的半态 → 验真查不到记录、链上却锚着摘要。
    #   注意：事务**只含本地写，不含 RPC**（RPC 已在外层调用完毕），
    #   避免重犯 M4 的「事务里按住 40s 网络 IO」。
    with transaction.atomic():
        current = EvidenceRecord.objects.filter(
            biz_type=biz_type, biz_id=biz_id, is_current=True).first()

        # 1) 首次存证
        if current is None:
            # 兼容历史数据：若存在但都被标为非 current，取最大版本续号
            last_ver = (EvidenceRecord.objects
                        .filter(biz_type=biz_type, biz_id=biz_id)
                        .order_by("-version")
                        .values_list("version", flat=True)
                        .first()) or 0
            return EvidenceRecord.objects.create(
                biz_type=biz_type, biz_id=biz_id, biz_no=biz_no,
                data_hash=digest, payload=payload,
                version=last_ver + 1, is_current=True,
                tx_hash=result.get("tx_hash", ""),
                block_height=result.get("block_height", 0),
                chain_status=chain_ok,
            )

        # 2) 摘要一致：在原行补齐链上信息即可（不新增版本）
        if current.data_hash == digest:
            current.biz_no = biz_no or current.biz_no
            current.payload = payload
            if result.get("ok"):
                current.tx_hash = result.get("tx_hash", current.tx_hash)
                current.block_height = result.get("block_height",
                                                  current.block_height)
                current.chain_status = chain_ok
            current.save()
            return current

        # 3) 摘要不同：归档旧版本 + 追加新版本（同事务，不存在「无 current」中间态）
        current.is_current = False
        current.save(update_fields=["is_current", "updated_at"])
        return EvidenceRecord.objects.create(
            biz_type=biz_type, biz_id=biz_id, biz_no=biz_no,
            data_hash=digest, payload=payload,
            version=current.version + 1, is_current=True,
            tx_hash=result.get("tx_hash", ""),
            block_height=result.get("block_height", 0),
            chain_status=chain_ok,
        )


def anchor_certificate(cert: Certificate) -> EvidenceRecord:
    """
    对合格证执行存证：
      1. 计算哈希并写回合格证
      2. 上链（真实链优先，失败降级模拟）
      3. 写入存证记录

    ★ 写入顺序（原 M4 修复）：先只写 cert_hash（本地重算即可得，廉价），
    待上链 + 落存证记录**之后**，再把 is_anchored/anchored_at 标为已完成。
    这样中间若上链抛异常，不会留下「is_anchored=True 但无存证记录」的半态
    —— 半态会让验真页展示「已存证」却查不到记录，自相矛盾。
    （注意：本函数不做 transaction.atomic —— 40s 的 RPC 会把 DB 事务按住，
      故采用「先落字段、后标完成」的补偿式顺序，而非包大事务。）
    """
    payload = build_certificate_payload(cert)
    digest = sha256_hex(payload)
    # 第一步：只固化哈希（不置 is_anchored）
    cert.cert_hash = digest
    cert.save(update_fields=["cert_hash", "updated_at"])

    # 第二步：上链 + 落存证记录
    result = _anchor(digest, payload, EvidenceRecord.BizType.CERTIFICATE,
                     cert.id, cert.cert_no)
    record = _save_record(EvidenceRecord.BizType.CERTIFICATE, cert.id,
                          cert.cert_no, digest, payload, result)

    # 第三步：确认有存证记录后，再标记「已存证」
    cert.is_anchored = True
    cert.anchored_at = datetime.now()
    cert.save(update_fields=["is_anchored", "anchored_at", "updated_at"])
    return record


def anchor_merkle_root(cert: Certificate) -> EvidenceRecord:
    """
    ★ 把一张合格证的「证据树」压缩成单个 Merkle 根上链（新增亮点，不侵入现有验真）。

    时机：在 anchor_certificate 之后调用，因此批/报告/证三者的存证记录都已落库，
    build_certificate_merkle_root 才能取到它们的 data_hash 参与汇聚。

    做了三件事：
      1. 计算 Merkle 根（批次->报告->证->检查，顺序固定）；
      2. 写回 Certificate.merkle_root（证面/详情可直接展示）；
      3. 以独立 bizType=merkle 上链（bizNo 用 cert_no，与 certificate 记录分属
         不同 bizType，不互相覆盖），链上能核验「这张证只能压缩成这一个根」。

    ★ 为什么不破坏现有验真：verify_certificate 只按 CERTIFICATE 类型做双层核验，
    这条 merkle 记录是额外的、平行的证据压缩层，任何一层缺失都不影响另一层结论。
    """
    from . import merkle

    root = merkle.build_certificate_merkle_root(cert)
    if not root:   # 突发情况（如证据哈希全空）不该上链一条空根
        return None

    cert.merkle_root = root
    cert.save(update_fields=["merkle_root", "updated_at"])

    # ★ cert_no 兜底（原 M2）：payload 已兜底但 biz_no 没兜底，会写 None 进
    #   NOT NULL 列。这里统一用兜底串，payload 与 biz_no 保持一致。
    cert_no = cert.cert_no or f"UNISSUED-{cert.id}"
    payload = "|".join([cert_no, "MERKLE", root])
    result = _anchor(root, payload, EvidenceRecord.BizType.MERKLE,
                     cert.id, cert_no)
    return _save_record(EvidenceRecord.BizType.MERKLE, cert.id,
                        cert_no, root, payload, result)


def build_batch_payload(batch) -> str:
    """
    生产批次的规范化原文。

    ★ 与原实现等价（同样的字段、同样的顺序），但改用长度前缀编码消除 '|' 歧义。
    抽成独立函数是为了让「存证」与「核验」共用同一实现 —— 否则两处各写一份
    拼接逻辑，一改一漏就会出现「存的时候用 A 格式、验的时候用 B 格式」，
    导致验真永远失败。
    """
    fields = [
        batch.batch_no,
        batch.producer.name if batch.producer_id else "",
        batch.product_name,
        batch.harvest_date,
        batch.weight_kg,
        BATCH_PAYLOAD_VERSION,
    ]
    return "|".join(_encode_field(f) for f in fields)


def build_report_payload(report) -> str:
    """
    检测报告的规范化原文。

    ★ 安全审计相关：报告的**结论（conclusion）**是决定农产品能否流通的事实依据，
    必须参与哈希 —— 否则「先按合格存证，事后偷偷改成不合格」不会被发现。
    原实现已含 conclusion，这里保持并统一编码方式。
    """
    fields = [
        report.report_no,
        report.batch.batch_no if report.batch_id else "",
        report.conclusion,
        report.test_date,
        REPORT_PAYLOAD_VERSION,
    ]
    return "|".join(_encode_field(f) for f in fields)


def anchor_batch(batch) -> EvidenceRecord:
    payload = build_batch_payload(batch)
    digest = sha256_hex(payload)
    result = _anchor(digest, payload, EvidenceRecord.BizType.BATCH,
                     batch.id, batch.batch_no)
    return _save_record(EvidenceRecord.BizType.BATCH, batch.id,
                        batch.batch_no, digest, payload, result)


def anchor_report(report) -> EvidenceRecord:
    """
    对检测报告存证。

    ★ R4 更正：`report.report_hash` 每次存证**无条件覆盖**为「当前字段的哈希」
    （它代表「现在这份报告内容是什么」的快照，供前端一键核验用）。
    真正的**防篡改锚点**在存证表：摘要若变化，会追加一条新的 is_current 版本、
    旧版本归档保留（见 _save_record）。二者语义不同、并不冲突：
      - report_hash          = 当前内容哈希（可变，随内容更新）
      - EvidenceRecord 历史版本 = 各时点的锚定摘要（不可变，可回溯）
    """
    payload = build_report_payload(report)
    digest = sha256_hex(payload)
    report.report_hash = digest
    report.save(update_fields=["report_hash", "updated_at"])
    result = _anchor(digest, payload, EvidenceRecord.BizType.REPORT,
                     report.id, report.report_no)
    return _save_record(EvidenceRecord.BizType.REPORT, report.id,
                        report.report_no, digest, payload, result)


def anchor_revoke(cert: Certificate, reason: str = "") -> EvidenceRecord:
    """
    合格证作废存证（作废双向上链）。

    作废必须留痕，且必须是「链上可核验的撤销」，而非仅本地加一条记录：
      1. 链上：调 revokeEvidence，对原 certificate 记录的 bizNo 打 revoked 标记
         —— 公众验真时链上能查到「该证已被撤销」，与「被篡改」「不存在」区分；
      2. 本地：存一条 REVOKE 类型 EvidenceRecord，作为可追溯的审计留痕
         （记录原因 + 时间，供看板/时间轴展示）。

    注意：REVOKE 与 CERTIFICATE 是**不同的 biz_type**，条件唯一约束是
    (biz_type, biz_id)，因此同 id 也不会冲突 —— 早期注释说「用负数偏移」
    与实现不符（实现从未做偏移），此处更正。
    """
    from datetime import datetime as _dt
    # ★ cert_no 兜底（原 M2）：草稿证 cert_no 为 None，直接参与拼接会得到
    #   "None"，写进 biz_no（NOT NULL）虽不炸但语义错误；统一转成稳定占位串。
    cert_no = cert.cert_no or f"UNISSUED-{cert.id}"
    payload = "|".join([
        cert_no, "REVOKED", reason.strip(), _fmt(_dt.now()),
    ])

    # 1. 链上打撤销标记（对原 certificate bizNo；失败降级不中断业务）
    revoke_result = _revoke_on_chain(cert_no, reason)

    # 2. 链上写一条 REVOKE 类型的存证记录作为审计留痕（bizType=revoke 与 certificate 不冲突）
    digest = sha256_hex(payload)
    result = _anchor(digest, payload, EvidenceRecord.BizType.REVOKE,
                     cert.id, cert_no)
    # 若 revokeEvidence 已成功上链，则用其 tx_hash 作为这条审计记录的链上锚点
    if revoke_result.get("ok") and revoke_result.get("tx_hash"):
        result = dict(result, tx_hash=revoke_result["tx_hash"],
                      block_height=revoke_result.get("block_height", 0),
                      mode=revoke_result.get("mode", "mock"))

    return _save_record(EvidenceRecord.BizType.REVOKE, cert.id,
                        cert_no, digest, payload, result)


def _revoke_on_chain(biz_no: str, reason: str = "") -> dict:
    """
    调链上 revokeEvidence 对原存证记录打撤销标记。
    链不可用/未启用时降级（返回 mode=mock），绝不抛异常。
    """
    if not getattr(settings, "FISCO_BCOS", {}).get("ENABLE_CHAIN", True):
        return {"ok": False, "tx_hash": chain._mock_tx_hash(), "block_height": 0,
                "mode": "mock", "message": "链功能已关闭，作废未上链"}
    return chain.revoke_evidence(biz_type=EvidenceRecord.BizType.CERTIFICATE,
                                 biz_no=biz_no, reason=reason)


def build_inspection_payload(record) -> str:
    """
    构造监督检查记录的规范化原文。

    参与哈希的字段选择原则：只放**结论性、不可抵赖**的事实
    （检查结果、评分、罚款、法律依据、整改期限），
    不放「整改说明」这类后续会补充的描述性文字 —— 否则整改一填写哈希就变，
    存证就失去了「固化当时判定」的意义。
    """
    return "|".join([
        str(record.id),
        record.certificate.cert_no if record.certificate_id else "",
        record.get_check_type_display(),
        record.get_check_result_display(),
        _fmt(record.check_score),
        record.inspector_name or "",
        _fmt(record.check_time),
        _fmt(record.rectify_deadline),
        _fmt(record.penalty_amount),
        (record.legal_basis or "").strip(),
    ])


def inspection_biz_no(record) -> str:
    """
    监督检查的链上业务编号。

    ★ 必须带检查记录自身 id —— 合约是 `records[bizType][bizNo]` 单值映射，
    同一 bizNo 后写会覆盖先写。若直接用 cert_no，同一张合格证上的多次检查
    只有最后一次能在链上核验，先前记录会被永久覆盖（已实测踩坑）。
    """
    return f"INS-{record.id}"


def anchor_inspection(record) -> EvidenceRecord:
    """
    监督检查结论存证上链。

    ★ 这是本系统「监管 + 可信存证」的结合点：
    检查结论一旦上链，事后任何一方都无法单方面否认「当时判了不合格」。
    对监管方是履职留痕，对被检方是程序正义保障。
    """
    biz_no = inspection_biz_no(record)
    payload = build_inspection_payload(record)
    digest = sha256_hex(payload)
    result = _anchor(digest, payload, EvidenceRecord.BizType.INSPECTION,
                     record.id, biz_no)
    record.record_hash = digest
    record.is_anchored = True
    record.anchored_at = datetime.now()
    record.save(update_fields=["record_hash", "is_anchored", "anchored_at",
                               "updated_at"])
    return _save_record(EvidenceRecord.BizType.INSPECTION, record.id,
                        biz_no, digest, payload, result)


def _verify_related_evidence(cert: Certificate) -> list:
    """
    核验合格证的关联证据（批次、检测报告）是否与各自存证摘要一致。

    ★ 安全修复（审计 High）：原实现的验真只核对**证书自身摘要**，而证书摘要
    里只含批次的 batch_no、报告的 report_no 这类「引用值」。结果是：
      批次产地/品种、报告结论/日期/机构被改了 → 公众页展示值变了，
      但证书摘要不变、验真仍显示「可信」—— 展示与结论脱节。

    现在两重保障：
      1. 这些字段已编码进证书摘要（改了就 current != stored）；
      2. 本函数再对关联对象**各自的存证记录**做独立比对，用于**定位**是哪一项变了。

    返回 [{kind, label, biz_no, trusted, checked, current_hash, stored_hash, message}, ...]

    语义约定（重要）：
      - checked=False 表示该关联对象**尚无存证记录**，无法独立核验。
        ★ 这种情况**不算失败**（不参与 trusted 判定），只如实标注。
        理由：防篡改的**主保障是「关联字段已编码进证书摘要」**——
        字段被改，证书摘要必然变。独立核验只是「定位到具体哪一项变了」的辅助，
        不应因为「对方还没存证」就把整张证判死。
      - checked=True 且 trusted=False：该关联对象有存证但与当前内容不符 →
        确定为篡改，会让整证判为不可信。
    """
    out = []

    def _check(biz_type, obj, kind, label):
        if obj is None:
            return
        rec = EvidenceRecord.objects.filter(
            biz_type=biz_type, biz_id=obj.id, is_current=True).first()
        if rec is None or not rec.data_hash:
            out.append({
                "kind": kind, "label": label, "biz_no": "",
                "checked": False, "trusted": True,
                "current_hash": "", "stored_hash": "",
                "message": f"{label}未单独存证，已通过合格证摘要间接校验",
            })
            return
        # 用关联对象当前的规范化原文重算，与存证摘要比对
        current = sha256_hex(_payload_for(biz_type, obj))
        ok = current == rec.data_hash
        out.append({
            "kind": kind, "label": label, "biz_no": rec.biz_no,
            "checked": True, "trusted": ok,
            "current_hash": current, "stored_hash": rec.data_hash,
            "message": (f"{label}与存证摘要一致" if ok
                        else f"{label}与存证摘要不一致，可能已被篡改"),
        })

    _check(EvidenceRecord.BizType.BATCH, cert.batch if cert.batch_id else None,
           "batch", "生产批次")
    _check(EvidenceRecord.BizType.REPORT, cert.report if cert.report_id else None,
           "report", "检测报告")
    return out


def _payload_for(biz_type: str, obj) -> str:
    """按 biz_type 选择对应的规范化原文构造函数（供关联证据核验复用）。"""
    if biz_type == EvidenceRecord.BizType.BATCH:
        return build_batch_payload(obj)
    if biz_type == EvidenceRecord.BizType.REPORT:
        return build_report_payload(obj)
    if biz_type == EvidenceRecord.BizType.CERTIFICATE:
        return build_certificate_payload(obj)
    if biz_type == EvidenceRecord.BizType.INSPECTION:
        return build_inspection_payload(obj)
    return ""


def verify_certificate(cert: Certificate, check_chain: bool = False) -> dict:
    """
    验真：重算当前合格证哈希，与存证记录中的摘要比对；可选做链上二次核验。

    双层校验逻辑（答辩亮点）：
      第一层「本地比对」：当前数据重算哈希 == 存证表里的摘要
      第二层「链上核验」：存证表里的摘要 == 链上交易 data 中记录的摘要
    两层都过，才能证明「数据没被改，且存证摘要没被库内篡改」。

    返回 {trusted, current_hash, stored_hash, message, chain_verified, chain_detail}
    """
    current = compute_certificate_hash(cert)
    # ★ 取「当前版本」记录（B3 追加式版本）：历史版本 is_current=False，不参与比对
    record = EvidenceRecord.objects.filter(
        biz_type=EvidenceRecord.BizType.CERTIFICATE, biz_id=cert.id,
        is_current=True
    ).first()
    stored = record.data_hash if record else ""
    local_trusted = bool(stored) and (current == stored)

    # ★ 安全修复（审计 High）：关联证据单独核验。
    #   证书摘要现在已把批次/报告的关键字段编码进去（见 build_certificate_payload），
    #   所以「改了产地/结论」本身就会让上面的 current != stored。
    #   这里再对**关联批次、报告各自的存证记录**做一次独立比对，做到：
    #     - 能明确告诉公众「到底是证书本身变了，还是关联的批次/报告变了」
    #     - 若关联对象尚未存证，如实标注（不假装可信）
    related_issues = _verify_related_evidence(cert)

    # ★ 关联证据异常 → 整证判为不可信（展示值与可信结论不能脱节）
    #   只统计「已单独存证且比对失败」的项；未存证的关联对象不参与判定
    #   （其正确性已由证书摘要间接保障）。
    related_trusted = all(x["trusted"] for x in related_issues)

    if not record:
        message = "未找到存证记录，无法验真"
    elif local_trusted and related_trusted:
        message = "数据可信：当前数据与存证摘要一致"
    elif not local_trusted:
        message = "数据不可信：当前数据与存证摘要不一致，可能已被篡改"
    else:
        bad = [x["label"] for x in related_issues
               if x["checked"] and not x["trusted"]]
        message = ("数据不可信：关联证据（" + "、".join(bad)
                   + "）与存证摘要不一致，可能已被篡改")

    result = {
        "trusted": local_trusted and related_trusted,
        "current_hash": current,
        "stored_hash": stored,
        "message": message,
        "chain_verified": False,
        "chain_detail": "",
        "revoked": False,
        # ★ 关联证据核验明细（公众页可展示「批次 / 报告是否也被改过」）
        "related": related_issues,
    }

    # ---- 第二层：链上核验（调用合约 verifyEvidence / isRevoked）----
    # ★ 原 M5：只看 tx_hash 是否 0x 开头挡不住降级存证 —— mock 的 tx_hash
    #   本身就是 0x+64hex。真正的判据是 record.chain_status：
    #   只有真链上成功（"success"）的记录才值得做链上核验，否则离线/mock
    #   模式下每次扫码都要打 3 次 RPC（各 10s），公开接口可被拖垮。
    chain_evidence_ok = (
        record is not None
        and record.chain_status == "success"
        and record.tx_hash.startswith("0x")
    )
    if check_chain and chain_evidence_ok:
        chain_info = chain.verify_on_chain(
            EvidenceRecord.BizType.CERTIFICATE, cert.cert_no, stored)
        if chain_info.get("ok"):
            # ★ 链上已撤销（作废双向上链）：无论摘要是否一致，都判定为「已作废」
            if chain_info.get("revoked"):
                result["chain_verified"] = False
                result["revoked"] = True
                result["trusted"] = False
                result["chain_detail"] = chain_info["message"]
                result["message"] = "该合格证已在链上作废（撤销），不可再用于流通"
            else:
                result["chain_verified"] = chain_info["matched"]
                result["chain_detail"] = chain_info["message"]
                if chain_info["exists"] and not chain_info["matched"]:
                    result["trusted"] = False
                    result["message"] = "数据不可信：存证记录与链上数据不一致"
        else:
            result["chain_detail"] = f"链上查询失败：{chain_info.get('message', '未知原因')}"
    elif check_chain and record is not None:
        # 降级存证（mock）或链关闭：不发起 RPC，明确告知只过第一层
        result["chain_detail"] = "该存证为降级模式（未真实上链），仅完成本地比对"

    return result