"""
产销衔接：智能匹配算法

原实现只有「把 deal_status 改成 matched」一行，撑不起题目里的「产销衔接」。
这里实现一个可解释的多因子加权打分模型，输出 0-100 的匹配度与评分明细，
既能在页面上做推荐排序，也能在答辩时讲清算法设计。

评分模型（总分 100）：
  ┌──────────────┬──────┬────────────────────────────────────────────┐
  │ 因子          │ 权重 │ 说明                                        │
  ├──────────────┼──────┼────────────────────────────────────────────┤
  │ 品种匹配      │ 30   │ 品种完全相同 30；同产品不同品种 15；产品不同 0  │
  │ 数量满足度    │ 25   │ min(供,需)/max(供,需) × 25，越接近越优        │
  │ 价格契合度    │ 20   │ 报价差在期望价 ±15% 内满分，超出线性衰减        │
  │ 地域邻近度    │ 15   │ 同地区 15；同省/市前缀 8；否则 0                │
  │ 信用/合规加分 │ 10   │ 供方主体已审核通过且关联证已签发 10，其一 5      │
  └──────────────┴──────┴────────────────────────────────────────────┘

设计取舍：
  - 用「加权求和」而非机器学习：本科毕设需要的是可解释、可复现、能答辩，
    而不是黑盒。每个因子的取值都能在界面上展示出来。
  - 数量满足度用 min/max 比值而非差值：1000kg 与 1200kg 的差距，
    应该比 10000kg 与 10200kg 的差距更大，比值能体现相对差异。
"""
from decimal import Decimal

from .models import Certificate, Producer, SupplyDemand

# 权重定义（集中管理，便于调参和答辩展示）
WEIGHTS = {
    "variety": 30,      # 品种匹配
    "quantity": 25,     # 数量满足度
    "price": 20,        # 价格契合度
    "region": 15,       # 地域邻近度
    "credit": 10,       # 信用/合规加分
}
TOTAL_WEIGHT = sum(WEIGHTS.values())  # = 100


def _to_decimal(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def _score_variety(supply: SupplyDemand, demand: SupplyDemand):
    """品种匹配：完全相同满分；同产品不同品种给一半。"""
    s_v, d_v = (supply.variety or "").strip(), (demand.variety or "").strip()
    s_p, d_p = (supply.product_name or "").strip(), (demand.product_name or "").strip()
    if s_v and d_v and s_v == d_v:
        return WEIGHTS["variety"], f"品种一致({s_v})"
    if s_p and d_p and s_p == d_p:
        return WEIGHTS["variety"] // 2, f"同产品不同品种({s_p})"
    return 0, "产品不匹配"


def _score_quantity(supply: SupplyDemand, demand: SupplyDemand):
    """数量满足度：供需数量比值，越接近 1 越高。"""
    s_q, d_q = _to_decimal(supply.quantity_kg), _to_decimal(demand.quantity_kg)
    if s_q <= 0 or d_q <= 0:
        return 0, "数量信息缺失"
    ratio = min(s_q, d_q) / max(s_q, d_q)
    score = int(round(ratio * WEIGHTS["quantity"]))
    return score, f"数量比 {ratio:.0%}(供{s_q}/需{d_q})"


def _score_price(supply: SupplyDemand, demand: SupplyDemand):
    """价格契合度：供方报价 vs 需方期望价，±15% 内满分，之后线性衰减。"""
    s_p, d_p = _to_decimal(supply.price), _to_decimal(demand.price)
    if s_p <= 0 or d_p <= 0:
        # 有一方没填价格，给中性分（不惩罚也不奖励）
        return WEIGHTS["price"] // 2, "价格信息不完整，按中性计分"
    diff = abs(s_p - d_p) / d_p
    if diff <= Decimal("0.15"):
        return WEIGHTS["price"], f"价格契合(差{diff:.0%})"
    if diff >= Decimal("0.60"):
        return 0, f"价格分歧大(差{diff:.0%})"
    # 15%~60% 之间线性衰减
    ratio = (Decimal("0.60") - diff) / (Decimal("0.60") - Decimal("0.15"))
    return int(round(ratio * WEIGHTS["price"])), f"价格偏差 {diff:.0%}"


def _score_region(supply: SupplyDemand, demand: SupplyDemand):
    """地域邻近度：完全同地区满分，前缀相同（同省市）给一半。"""
    s_r, d_r = (supply.region or "").strip(), (demand.region or "").strip()
    if not s_r or not d_r:
        return 0, "地区信息缺失"
    if s_r == d_r:
        return WEIGHTS["region"], f"同地区({s_r})"
    # 取前两个字符做粗粒度比较（如「新疆」「阿克苏」）
    if s_r[:2] == d_r[:2]:
        return WEIGHTS["region"] // 2, f"邻近地区({s_r}/{d_r})"
    return 0, f"跨区域({s_r}/{d_r})"


def _score_credit(supply: SupplyDemand, demand: SupplyDemand):
    """信用/合规加分：供方主体已审核 + 关联合格证已签发。"""
    score, notes = 0, []
    cert = supply.certificate
    if cert is not None:
        if cert.status == Certificate.Status.ISSUED:
            score += 5
            notes.append("证已签发")
        producer = cert.batch.producer if cert.batch_id else None
        if producer is not None and producer.audit_status == Producer.AuditStatus.APPROVED:
            score += 5
            notes.append("主体已审核")
    elif supply.batch_id and supply.batch and supply.batch.producer_id:
        if supply.batch.producer.audit_status == Producer.AuditStatus.APPROVED:
            score += 5
            notes.append("主体已审核")
    return score, ("、".join(notes) if notes else "无合规加分")


def score_match(supply: SupplyDemand, demand: SupplyDemand) -> dict:
    """
    计算一条「供应」与一条「求购」的匹配度。

    返回 {score, detail, factors:[{name, score, weight, note}, ...]}
    """
    if supply.kind != SupplyDemand.Kind.SUPPLY or demand.kind != SupplyDemand.Kind.DEMAND:
        raise ValueError("匹配要求一方为供应、另一方为求购")

    factors = []
    total = 0
    for key, fn, label in (
        ("variety", _score_variety, "品种匹配"),
        ("quantity", _score_quantity, "数量满足度"),
        ("price", _score_price, "价格契合度"),
        ("region", _score_region, "地域邻近度"),
        ("credit", _score_credit, "信用合规"),
    ):
        s, note = fn(supply, demand)
        total += s
        factors.append({"name": label, "score": s, "weight": WEIGHTS[key], "note": note})

    return {
        "score": total,
        "detail": "；".join(f"{f['name']}{f['score']}/{f['weight']}" for f in factors),
        "factors": factors,
    }


def recommend_for(sd: SupplyDemand, limit: int = 5):
    """
    为一条供需单推荐匹配的对手方。

    供应单 -> 推荐求购单；求购单 -> 推荐供应单。
    只推荐「待对接」状态的，按匹配度降序返回。
    """
    if sd.kind == SupplyDemand.Kind.SUPPLY:
        candidates = SupplyDemand.objects.filter(
            kind=SupplyDemand.Kind.DEMAND,
            deal_status=SupplyDemand.DealStatus.OPEN,
        ).exclude(id=sd.id).select_related("certificate", "batch", "batch__producer")
        scorer = lambda c: score_match(sd, c)   # noqa: E731
    else:
        candidates = SupplyDemand.objects.filter(
            kind=SupplyDemand.Kind.SUPPLY,
            deal_status=SupplyDemand.DealStatus.OPEN,
        ).exclude(id=sd.id).select_related("certificate", "batch", "batch__producer")
        scorer = lambda c: score_match(c, sd)   # noqa: E731

    results = []
    for c in candidates:
        r = scorer(c)
        results.append({
            "id": c.id,
            "title": c.title,
            "kind": c.kind,
            "kind_display": c.get_kind_display(),
            "product_name": c.product_name,
            "variety": c.variety,
            "quantity_kg": str(c.quantity_kg),
            "price": str(c.price),
            "region": c.region,
            "contact": c.contact,
            "phone": c.phone,
            "cert_no": c.certificate.cert_no if c.certificate_id else "",
            "score": r["score"],
            "detail": r["detail"],
            "factors": r["factors"],
        })
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]
