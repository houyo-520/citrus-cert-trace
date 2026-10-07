"""
客户端真实 IP 提取（审计与限流共用）

★ 信任边界（B4 修复）：

仅当请求的**直连对端**（REMOTE_ADDR）属于可信代理（默认 127.0.0.1/::1，
即本机 nginx）时，才采信 X-Real-IP / X-Forwarded-For。否则一律用 REMOTE_ADDR。

为什么必须这样：
  X-Real-IP / XFF 都是**请求头**，客户端可任意伪造。
  若 gunicorn 监听 0.0.0.0:8000 且云安全组没封 8000，攻击者绕过 nginx 直连
  端口、自己发一个 `X-Real-IP: <任意IP>`，就能伪装来源 IP → 绕过 IP 限流 +
  污染审计日志。只有「流量必经可信代理」时才读这些头才是安全的。

审计（audit.get_client_ip）与登录限流（throttling._client_ip）**共用本函数**，
保证两处取 IP 的规则一致（早期两处各写一份、且跳数来源不同 → 改配置不生效）。
"""
from django.conf import settings

# 默认可信代理：本机（nginx 与 gunicorn 同机部署）
_DEFAULT_TRUSTED = ["127.0.0.1", "::1"]


def _trusted_proxies():
    ips = getattr(settings, "TRUSTED_PROXY_IPS", None)
    if not ips:
        return _DEFAULT_TRUSTED
    return list(ips)


def _hops():
    try:
        return int(getattr(settings, "TRUSTED_PROXY_HOPS", 1) or 1)
    except (TypeError, ValueError):
        return 1


def get_client_ip(request) -> str:
    """
    取真实客户端 IP。

    规则：
      1) REMOTE_ADDR 不在可信代理白名单 → 直接返回 REMOTE_ADDR
         （说明是直连，请求头里的 X-Real-IP / XFF 一律不可信）；
      2) 在可信代理内 → 优先 X-Real-IP（由 nginx 覆写）；
      3) 退化到 XFF 从右往左数 TRUSTED_PROXY_HOPS 跳；
      4) 都没有 → REMOTE_ADDR。
    """
    if request is None:
        return ""
    meta = getattr(request, "META", {})
    remote = (meta.get("REMOTE_ADDR") or "").strip()

    if remote not in _trusted_proxies():
        # 直连（非可信代理）：不采信任何可伪造的转发头
        return remote or "unknown"

    real_ip = (meta.get("HTTP_X_REAL_IP") or "").strip()
    if real_ip:
        return real_ip

    xff = meta.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            hops = _hops()
            idx = max(0, len(parts) - hops)
            return parts[idx]

    return remote or "unknown"
