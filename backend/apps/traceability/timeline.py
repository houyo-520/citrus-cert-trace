"""
追溯链时间轴聚合。

设计目标：把散落在 5 张表里的时间点，串成消费者看得懂的「一件柑橘的旅程」。

为什么值得单独做一个模块（而不是在视图里拼 dict）：
  时间轴节点的**排序、缺失、状态推导**都有业务规则，散在视图里会变成一大坨
  难以测试的 if-else。抽出来之后可以单独写单测断言「缺检测报告时节点怎么降级」，
  而且答辩时这段逻辑本身就是「数据主线如何可视化」的答案。

★ 核心设计：节点「按事实存在与否动态生成」，不做固定 5 步占位。
  如果某个证还没检测报告，就不该显示一个空的「检测」节点骗人 ——
  公众验真页的可信度建立在「显示的都是真的」之上。
  因此每个节点都有 `done` 标记，前端据此决定是实心还是空心，
  但**只有真实发生过的事实才产生节点**。

节点顺序（一件柑橘的旅程）：
  ① 主体备案      主体通过监管审核
  ② 批次建档      采收，形成可追溯批次
  ③ 农事记录      施肥用药等（有内容才出现）
  ④ 检测          第三方检测报告出具
  ⑤ 签发上链      合格证签发 + 哈希上链（核心节点）
  ⑥ 流通          进入产销供需
  ⑦ 监管检查      被检查过（有才出现，且是不合格时的重点）
"""
from datetime import datetime

from . import certno, services
from .models import (Certificate, EvidenceRecord, InspectionRecord,
                     MatchIntent, OperationLog, ProductBatch, SupplyDemand)


def _dt(value, fmt="%Y-%m-%d"):
    """日期/时间统一格式化，None 返回空串。"""
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime(fmt + " %H:%M")
    if hasattr(value, "strftime"):
        return value.strftime(fmt)
    return str(value)


def _node(key, title, icon, time_text, desc, detail=None, done=True,
          level="info", anchor=None):
    """
    构造一个时间轴节点。

    level 用于前端着色：
      info    常规节点（灰蓝）
      success 正常完成（绿）
      warning 需注意（橙）
      danger  异常/不合格（红）
    """
    return {
        "key": key,
        "title": title,
        "icon": icon,
        "time": time_text or "",
        "desc": desc or "",
        "detail": detail or [],
        "done": bool(done),
        "level": level,
        # 锚点信息：这个节点的数据在哪条链上存证（有则前端可展示 tx_hash）
        "anchor": anchor or None,
    }


def _anchor_of(biz_type: str, biz_id: int, cache=None):
    """
    取某业务对象的当前存证信息（用于在节点上显示链上交易）。

    ★ 性能（M11）：接受可选 cache（{(biz_type, biz_id): rec}）。
    公众验真接口是 AllowAny，每次扫码若对每个节点各查一次存证（4+ 次），
    再叠加检查/统计查询，单次 15-20 条 SQL。build_timeline 预先一次性把
    需要的存证取进 cache，这里命中就零查询。
    """
    rec = None
    if cache is not None:
        rec = cache.get((biz_type, biz_id))
    if rec is None:
        # ★ 只取当前版本（B3 追加式版本），避免取到历史归档版本
        rec = (EvidenceRecord.objects
               .filter(biz_type=biz_type, biz_id=biz_id, is_current=True)
               .order_by("-version").first())
    if not rec:
        return None
    return {
        "data_hash": rec.data_hash,
        "tx_hash": rec.tx_hash,
        "block_height": rec.block_height,
        "chain_status": rec.chain_status,
        "anchored": bool(rec.tx_hash and rec.tx_hash.startswith("0x")),
    }


def _prefetch_anchors(cert, batch, report):
    """
    一次性取回本证相关对象的当前存证，返回 {(biz_type, biz_id): rec}。

    ★ M11：公众验真接口是 AllowAny，逐节点查询会让单次扫码打出 15-20 条 SQL。
    这里合并成 1~2 条查询；失败时返回空 dict，_anchor_of 会退回逐条查询（不影响正确性）。
    """
    cache = {}
    try:
        conds = []
        if batch:
            conds.append((EvidenceRecord.BizType.BATCH, batch.id))
        if report:
            conds.append((EvidenceRecord.BizType.REPORT, report.id))
        conds.append((EvidenceRecord.BizType.CERTIFICATE, cert.id))
        # 用 (biz_type, biz_id) 的 OR 组合，单条查询取回
        from django.db.models import Q
        q = Q()
        for bt, bid in conds:
            q |= Q(biz_type=bt, biz_id=bid)
        # 该证相关检查记录的存证（可能多条）
        insp_ids = list(cert.inspections.values_list("id", flat=True)[:20])
        if insp_ids:
            q |= Q(biz_type=EvidenceRecord.BizType.INSPECTION,
                   biz_id__in=insp_ids)
        for rec in EvidenceRecord.objects.filter(q, is_current=True):
            cache[(rec.biz_type, rec.biz_id)] = rec
    except Exception:  # noqa: BLE001 预取失败不应影响主流程
        return {}
    return cache


def build_timeline(cert: Certificate) -> dict:
    """
    为一张合格证构造完整追溯时间轴。

    返回 {nodes: [...], summary: {...}, verify: {...}}
    """
    nodes = []
    batch = cert.batch if cert.batch_id else None
    producer = batch.producer if (batch and batch.producer_id) else None
    report = cert.report if cert.report_id else None

    # ★ 一次性预取本证相关的当前存证（M11：消除逐节点的查询）。
    anchor_cache = _prefetch_anchors(cert, batch, report)

    # ---- ① 主体备案 ----
    if producer:
        approved = producer.audit_status == producer.AuditStatus.APPROVED
        nodes.append(_node(
            "producer", "生产主体备案", "OfficeBuilding",
            _dt(producer.created_at),
            f"{producer.name}（{producer.region or '产地未填写'}）",
            [
                ("统一社会信用代码", producer.credit_code or "—"),
                ("联系人", f"{producer.contact} {producer.phone}".strip() or "—"),
                ("产地地址", producer.address or "—"),
                ("备案状态", producer.get_audit_status_display()),
            ],
            done=approved,
            level="success" if approved else "warning",
        ))

    # ---- ② 批次建档（采收）----
    if batch:
        nodes.append(_node(
            "batch", "批次建档 · 采收", "Box",
            _dt(batch.harvest_date or batch.created_at),
            f"批次 {batch.batch_no}，品种「{batch.variety or '柑橘'}」，"
            f"采收 {batch.weight_kg} kg",
            [
                ("批次编号", batch.batch_no),
                ("产地", batch.origin or "—"),
                ("种植面积", f"{batch.plant_area_mu} 亩"),
                ("批次重量", f"{batch.weight_kg} kg"),
            ],
            anchor=_anchor_of(EvidenceRecord.BizType.BATCH, batch.id, anchor_cache),
        ))

    # ---- ③ 农事记录（有内容才出现）----
    if batch and (batch.farming_record or "").strip():
        nodes.append(_node(
            "farming", "农事管理记录", "Sunny",
            _dt(batch.created_at),
            "施肥、用药等生产管理过程留痕",
            [("农事记录", batch.farming_record.strip()[:300])],
        ))

    # ---- ④ 检测 ----
    if report:
        good = report.conclusion == report.Conclusion.QUALIFIED
        nodes.append(_node(
            "report", "第三方质量检测", "DocumentChecked",
            _dt(report.test_date or report.created_at),
            f"{report.test_org or '检测机构'} 出具报告 {report.report_no}，"
            f"结论「{report.get_conclusion_display()}」",
            [
                ("报告编号", report.report_no),
                ("检测机构", report.test_org or "—"),
                ("农药残留", report.pesticide_residue or "—"),
                ("重金属", report.heavy_metal or "—"),
                ("检测结论", report.get_conclusion_display()),
            ],
            level="success" if good else "danger",
            anchor=_anchor_of(EvidenceRecord.BizType.REPORT, report.id, anchor_cache),
        ))

    # ---- ⑤ 签发上链（核心节点）----
    if cert.cert_no:
        nodes.append(_node(
            "issue", "合格证签发 · 可信存证", "Postcard",
            _dt(cert.issue_date or cert.anchored_at),
            f"编号 {cert.cert_no}，开具 {cert.quantity_kg} kg，"
            + ("已完成联盟链存证" if cert.is_anchored else "待存证"),
            [
                ("合格证编号", cert.cert_no),
                ("开具数量", f"{cert.quantity_kg} kg"),
                ("有效期至", _dt(cert.valid_until) or "—"),
                ("SHA-256 摘要", cert.cert_hash or "—"),
                ("存证时间", _dt(cert.anchored_at, "%Y-%m-%d") or "—"),
            ],
            level="success" if cert.status == Certificate.Status.ISSUED else "warning",
            anchor=_anchor_of(EvidenceRecord.BizType.CERTIFICATE, cert.id, anchor_cache),
        ))

    # ---- ⑥ 流通（产销对接）----
    supplies = SupplyDemand.objects.filter(certificate=cert).order_by("id")
    for sd in supplies[:3]:
        intents = sd.intents.count()
        nodes.append(_node(
            "supply", "进入流通 · 产销对接", "ShoppingCart",
            _dt(sd.created_at),
            f"发布{sd.get_kind_display()}信息「{sd.title}」"
            f"，{sd.quantity_kg} kg，期望 {sd.price} 元/kg",
            [
                ("类型", sd.get_kind_display()),
                ("地区", sd.region or "—"),
                ("对接状态", sd.get_deal_status_display()),
                ("收到意向", f"{intents} 条"),
            ],
            level="success" if sd.deal_status == SupplyDemand.DealStatus.MATCHED else "info",
        ))

    # ---- ⑦ 监督检查（有才出现）----
    inspections = cert.inspections.order_by("check_time", "id")
    for ins in inspections[:5]:
        bad = ins.check_result == ins.CheckResult.UNQUALIFIED
        detail = [
            ("检查类型", ins.get_check_type_display()),
            ("检查结果", ins.get_check_result_display()),
            ("检查评分", str(ins.check_score)),
            ("检查部门", ins.department or "—"),
        ]
        if ins.problem_description:
            detail.append(("发现问题", ins.problem_description[:200]))
        if ins.rectify_deadline:
            detail.append(("整改期限", _dt(ins.rectify_deadline)))
        detail.append(("整改状态", ins.get_rectify_status_display()))
        if ins.penalty_amount and float(ins.penalty_amount) > 0:
            detail.append(("罚款金额", f"{ins.penalty_amount} 元"))
        if ins.legal_basis:
            detail.append(("法律依据", ins.legal_basis))
        nodes.append(_node(
            # ★ key 必须唯一：同一张证可能有多条检查记录，
            #   若都用 "inspection" 会重复。前端 v-for 依赖 key 做 diff，
            #   重复 key 会导致节点复用错乱（渲染出的内容与数据对不上）。
            f"inspection-{ins.id}", f"监督检查（检查#{ins.id}）", "Stamp",
            _dt(ins.check_time or ins.created_at),
            f"{ins.department or '监管部门'}现场检查，"
            f"结论「{ins.get_check_result_display()}」"
            + (f"，{ins.get_rectify_status_display()}" if ins.rectify_deadline else ""),
            detail,
            level="danger" if bad else "success",
            anchor=_anchor_of(EvidenceRecord.BizType.INSPECTION, ins.id, anchor_cache),
        ))

    # ---- 当前状态收尾 ----
    verify = services.verify_certificate(cert, check_chain=False)

    return {
        "nodes": nodes,
        "summary": {
            "total": len(nodes),
            "anchored_nodes": sum(1 for n in nodes if n["anchor"]),
            "inspection_count": inspections.count(),
            "unqualified_count": inspections.filter(
                check_result=InspectionRecord.CheckResult.UNQUALIFIED).count(),
            "status": cert.status,
            "status_display": cert.get_status_display(),
        },
        "verify": {
            "trusted": verify["trusted"],
            "message": verify["message"],
        },
    }
