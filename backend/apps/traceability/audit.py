"""
操作审计日志服务

与「链上存证」互补：
  - 链上存证锚定的是「数据摘要」—— 证明数据没被改
  - 操作日志锚定的是「操作行为」—— 证明是谁在何时做了什么

写入统一走 log_operation()，避免各视图里散落重复代码。
"""
from .models import OperationLog


def get_client_ip(request) -> str:
    """从请求中提取真实客户端 IP（兼容 nginx 反代）。"""
    if request is None:
        return ""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
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
