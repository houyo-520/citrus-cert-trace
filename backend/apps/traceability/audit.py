"""
操作审计日志服务

与「链上存证」互补：
  - 链上存证锚定的是「数据摘要」—— 证明数据没被改
  - 操作日志锚定的是「操作行为」—— 证明是谁在何时做了什么

写入统一走 log_operation()，避免各视图里散落重复代码。
"""
from django.conf import settings

from .models import OperationLog


def get_client_ip(request) -> str:
    """
    从请求中提取真实客户端 IP（兼容 nginx 反代）。

    ★ 安全修复（原 M6）：原实现无条件取 X-Forwarded-For 的**第一段**。
    但 XFF 是客户端可伪造的请求头 —— 攻击者每个请求换一个 XFF 第一段，
    限流阈值就形同虚设（30 次/分/IP 被拆成 N 个不同"IP"），
    审计日志里的来源 IP 也可被任意伪造。

    正确做法（取决于信任边界）：
      - 本项目 Django 前面只有一层自建 nginx（可信代理），nginx 会把
        真实来源写进 X-Real-IP，并把上游 XFF 追加在其后；
      - 因此**优先信任 X-Real-IP**（由可信 nginx 覆写，客户端伪造不了）；
      - 退化到 XFF 时取**最后一段**（由最近的代理追加，比第一段可信）；
      - 都没有才用 REMOTE_ADDR。
    生产 nginx 已配置 `proxy_set_header X-Real-IP $remote_addr;`（见部署文档）。
    """
    if request is None:
        return ""

    # 1) 优先可信代理写入的 X-Real-IP
    real_ip = request.META.get("HTTP_X_REAL_IP", "").strip()
    if real_ip:
        return real_ip

    # 2) 退化：XFF 取最后一段（最近的可信代理追加的那一跳）
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            # 若配置了可信代理层数，则从右往左数第 N 跳才是真实客户端；
            # 未配置时保守取最后一段（不可被客户端单独伪造）。
            hops = int(getattr(settings, "TRUSTED_PROXY_HOPS", 1) or 1)
            idx = max(0, len(parts) - hops)
            return parts[idx]

    # 3) 最后兜底
    return request.META.get("REMOTE_ADDR", "")


def mask_ip(ip: str) -> str:
    """IP 掩码：仅保留前两段 + 末段，中间打码，避免水印本身泄露完整地址。

    例：203.0.113.5 -> 203.0.*.5 ；IPv6 过长则整体截断打码。
    """
    if not ip:
        return "未知"
    if ":" in ip:  # IPv6
        return ip[:6] + "…"
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.*.{parts[3]}"
    # 非法/非标准 IP：直接打码前段，避免原样输出
    return ip[:3] + ".***"


def log_operation(request, action: str, target_type: str = "", target_id=None,
                  target_no: str = "", detail: str = "", success: bool = True,
                  operator=None) -> OperationLog:
    """
    写一条操作日志。

    request 可为 None（后台任务/命令行场景），此时 operator 需显式传入。
    """
    user = operator or (getattr(request, "user", None) if request else None)
    if user is not None and not getattr(user, "is_authenticated", False):
        user = None

    return OperationLog.objects.create(
        operator=user,
        operator_name=getattr(user, "username", "") or "匿名",
        operator_role=getattr(user, "role", "") or "",
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_no=target_no or "",
        detail=detail[:255],
        ip=get_client_ip(request),
        success=success,
    )
