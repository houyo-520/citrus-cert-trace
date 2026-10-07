"""
站内消息通知服务。

设计原则：
  1. **通知与业务解耦**：业务代码只调 `notify.to_xxx(...)`，不关心消息怎么存。
     将来要接短信/邮件/企业微信，只改这个文件，业务侧零改动。
  2. **收件人显式指定**：不用「给某角色所有人广播」。同一角色可能有多个用户
     （producer / producer2），广播会把别人家的业务推给我。
  3. **通知失败绝不影响业务**：所有写消息的动作都包了 try/except。
     发消息是"锦上添花"，绝不能因为消息表写失败导致签发/检查这种核心动作回滚。
  4. **不给自己发消息**：监管签发后不需要提醒监管自己，只提醒申请人。

消息的「谁该知道」映射（这是本模块的业务核心）：
  种植主体提交送审   → 提醒所有监管/管理员（该来签发了）
  监管签发/驳回/作废  → 提醒该合格证所属主体（结果出来了）
  监督检查不合格     → 提醒被检主体（要整改）+ 该证所属主体
  整改复查           → 提醒监管（该复核了）
  产销意向发起       → 提醒供需单发布方（有人找你）
  意向回应           → 提醒意向发起人（对方答复了）
  主体审核           → 提醒主体 owner（审核结果）
"""
import logging

from django.contrib.auth import get_user_model
from django.db.models import Count

from .models import Notification

logger = logging.getLogger(__name__)

User = get_user_model()


def _regulators():
    """所有监管与管理员 —— 需要「有人送审了」这类待办提醒的人。"""
    from apps.accounts.models import Role
    return User.objects.filter(role__in=[Role.ADMIN, Role.REGULATOR])


def send(recipient, category, title, content="", level=Notification.Level.INFO,
         link_route="", link_id=None, link_label="", biz_type="", biz_no="",
         actor=None):
    """
    发一条消息给指定用户。

    ★ 绝不抛异常：通知失败不能连累业务。这里吞掉所有异常并记日志。
    """
    if recipient is None:
        return None
    try:
        # 不给自己发：操作人自己知道刚做了什么
        if actor is not None and getattr(actor, "id", None) == recipient.id:
            return None
        return Notification.objects.create(
            recipient=recipient,
            category=category,
            level=level,
            title=title[:120],
            content=content or "",
            link_route=link_route or "",
            link_id=link_id,
            link_label=link_label or "",
            biz_type=biz_type or "",
            biz_no=(biz_no or "")[:60],
            actor=actor,
        )
    except Exception as exc:  # noqa: BLE001 通知失败不应影响业务
        logger.warning("发送站内消息失败：%s", exc)
        return None


def send_many(recipients, **kwargs):
    """群发（逐个建记录，保证各自独立已读状态）。"""
    out = []
    for r in recipients:
        n = send(r, **kwargs)
        if n:
            out.append(n)
    return out


# ===============================================================
# 业务语义封装 —— 业务代码只调这些，不直接拼 title/content
# ===============================================================

def cert_submitted(cert, actor):
    """种植主体提交合格证送审 → 提醒监管来签发。"""
    label = cert.cert_no or f"草稿#{cert.id}"
    return send_many(
        _regulators(),
        category=Notification.Category.CERT_SUBMIT,
        title=f"合格证待签发：{cert.producer_name or label}",
        content=f"{actor.username} 提交了合格证送审，产品「{cert.product_name}」"
                f"{cert.quantity_kg} kg，请及时审核签发。",
        level=Notification.Level.WARNING,
        link_route="certificate", link_id=cert.id, link_label="去签发",
        biz_type="合格证", biz_no=label, actor=actor,
    )


def cert_issued(cert, actor):
    """监管签发 → 提醒主体。"""
    owner = _cert_owner(cert)
    return send(
        owner,
        category=Notification.Category.CERT_ISSUE,
        title=f"合格证已签发：{cert.cert_no}",
        content=f"您提交的合格证已通过审核并完成可信存证，编号 {cert.cert_no}。"
                f"可打印证面并对外流通。",
        level=Notification.Level.SUCCESS,
        link_route="certificate", link_id=cert.id, link_label="查看合格证",
        biz_type="合格证", biz_no=cert.cert_no, actor=actor,
    )


def cert_rejected(cert, actor, reason=""):
    """监管驳回 → 提醒主体补充材料。"""
    return send(
        _cert_owner(cert),
        category=Notification.Category.CERT_REJECT,
        title="合格证申请被驳回",
        content=f"您提交的合格证申请未通过审核。"
                + (f"驳回意见：{reason}" if reason else "请补充材料后重新提交。"),
        level=Notification.Level.DANGER,
        link_route="certificate", link_id=cert.id, link_label="去修改",
        biz_type="合格证", biz_no=cert.cert_no or f"草稿#{cert.id}", actor=actor,
    )


def cert_revoked(cert, actor, reason=""):
    """监管作废 → 提醒主体。"""
    return send(
        _cert_owner(cert),
        category=Notification.Category.CERT_REVOKE,
        title=f"合格证已作废：{cert.cert_no or ''}",
        content="您持有的合格证已被作废，不可再用于流通。"
                + (f"原因：{reason}" if reason else ""),
        level=Notification.Level.DANGER,
        link_route="certificate", link_id=cert.id, link_label="查看详情",
        biz_type="合格证", biz_no=cert.cert_no or "", actor=actor,
    )


def cert_suspended(cert, actor, reason=""):
    """监督检查不合格导致合格证暂停 → 提醒主体。"""
    return send(
        _cert_owner(cert),
        category=Notification.Category.CERT_SUSPEND,
        title=f"合格证被暂停使用：{cert.cert_no or ''}",
        content="该合格证因监督检查不合格被暂停流通。整改复查通过后，"
                "本批次合格证将作废，需针对新批次重新申领。"
                + (f"\n原因：{reason}" if reason else ""),
        level=Notification.Level.DANGER,
        link_route="certificate", link_id=cert.id, link_label="查看合格证",
        biz_type="合格证", biz_no=cert.cert_no or "", actor=actor,
    )


def inspection_created(record, actor):
    """录入监督检查 → 提醒被检主体。"""
    return send(
        _cert_owner(record.certificate),
        category=Notification.Category.INSPECTION,
        title=f"收到监督检查：{record.get_check_result_display()}",
        content=f"{record.department or '监管部门'}于"
                f"{(record.check_time.strftime('%Y-%m-%d') if record.check_time else '近期')}"
                f"对您的合格证开展{record.get_check_type_display()}，"
                f"结论为「{record.get_check_result_display()}」，评分 {record.check_score}。"
                + (f"\n发现问题：{record.problem_description}"
                   if record.problem_description else "")
                + (f"\n整改期限：{record.rectify_deadline}"
                   if record.rectify_deadline else ""),
        level=(Notification.Level.DANGER
               if record.check_result == 'unqualified' else Notification.Level.INFO),
        link_route="inspection", link_id=record.id, link_label="查看检查记录",
        biz_type="监督检查", biz_no=f"INS-{record.id}", actor=actor,
    )


def rectify_done(record, actor):
    """整改复查提交 → 提醒监管复核。"""
    return send_many(
        _regulators(),
        category=Notification.Category.RECTIFY,
        title=f"整改复查待确认：检查#{record.id}",
        content=f"{actor.username} 提交了整改说明，复查结论"
                f"「{record.get_rectify_status_display()}」，请确认闭环。",
        level=Notification.Level.WARNING,
        link_route="inspection", link_id=record.id, link_label="去复核",
        biz_type="监督检查", biz_no=f"INS-{record.id}", actor=actor,
    )


def intent_created(intent, actor):
    """发起产销意向 → 提醒供需单发布方。"""
    supply = intent.supply
    return send(
        supply.publisher,
        category=Notification.Category.MATCH,
        title=f"收到产销对接意向：{supply.title}",
        content=f"{actor.username} 对您发布的「{supply.get_kind_display()}」信息发起了对接，"
                f"意向数量 {intent.quantity_kg} kg"
                + (f"，匹配度 {intent.match_score} 分" if intent.match_score else "")
                + "，请及时回应。",
        level=Notification.Level.WARNING,
        link_route="intent", link_id=intent.id, link_label="去回应",
        biz_type="产销意向单", biz_no=supply.title, actor=actor,
    )


def intent_responded(intent, actor):
    """意向回应 → 提醒发起人。"""
    return send(
        intent.initiator,
        category=Notification.Category.MATCH,
        title=f"对接意向已回应：{intent.get_status_display()}",
        content=f"您对「{intent.supply.title if intent.supply_id else ''}」发起的对接意向，"
                f"对方已回应：{intent.get_status_display()}。",
        level=(Notification.Level.SUCCESS
               if intent.status == 'accepted' else Notification.Level.INFO),
        link_route="intent", link_id=intent.id, link_label="查看意向单",
        biz_type="产销意向单",
        biz_no=intent.supply.title if intent.supply_id else "", actor=actor,
    )


def producer_audited(producer, actor, status):
    """主体审核 → 提醒主体 owner。"""
    ok = status == "approved"
    return send(
        producer.owner,
        category=Notification.Category.AUDIT,
        title=f"主体审核结果：{producer.get_audit_status_display()}",
        content=f"您提交的生产主体「{producer.name}」审核结果为"
                f"「{producer.get_audit_status_display()}」。"
                + ("现在可以正常开展批次建档与合格证申请。" if ok
                   else "请根据驳回意见修改后重新提交。"),
        level=(Notification.Level.SUCCESS if ok else Notification.Level.DANGER),
        link_route="producer", link_id=producer.id, link_label="查看主体",
        biz_type="生产主体", biz_no=producer.name, actor=actor,
    )


def _cert_owner(cert):
    """
    取合格证的「业务归属人」。

    优先用批次的 owner（真实业务关系）；批次没绑 owner 时退回签发人，
    再退回 None（此时 send 会静默跳过）。这样即使演示数据缺 owner 也不会报错。
    """
    try:
        if cert.batch_id and cert.batch.producer_id and cert.batch.producer.owner_id:
            return cert.batch.producer.owner
    except Exception:  # noqa: BLE001
        pass
    return None


# ===============================================================
# 未读统计（供顶栏铃铛轮询）
# ===============================================================
def unread_summary(user):
    """未读消息汇总：总数 + 按类型分布 + 重要消息数。"""
    qs = Notification.objects.filter(recipient=user, read_at__isnull=True)
    rows = qs.values("category").annotate(c=Count("id"))
    label = dict(Notification.Category.choices)
    by_cat = [{"category": r["category"],
               "label": label.get(r["category"], r["category"]),
               "count": r["c"]} for r in rows]
    return {
        "unread": qs.count(),
        "by_category": by_cat,
        "danger": qs.filter(level=Notification.Level.DANGER).count(),
    }
