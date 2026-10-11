"""
自定义安全中间件。

★ 为什么需要它（2026-10-11 安全审计加固）：
  Django 原生没有「CSP 响应头」的内置支持（只有 SECURE_* 那一批，
  不含 Content-Security-Policy）。而 CSP 是当前架构下（JWT 存 localStorage）
  抵御 XSS 窃取 token 的**最后一道有效防线**：

    - `script-src 'self'`：注入的 <script> 标签 / 内联脚本不会执行；
    - `connect-src 'self'`：即使出现 XSS，脚本也无法把 token POST 到外域；
    - `object-src 'none'` / `base-uri 'self'`：堵掉插件与 base 标签劫持。

  仅当 settings.SECURE_CSP 非空时生效（由环境变量 DJANGO_CSP 控制），
  因此本地 Vite 开发不受影响。
"""
from django.conf import settings


class ContentSecurityPolicyMiddleware:
    """把 settings.SECURE_CSP 写入每个响应的 Content-Security-Policy 头。"""

    def __init__(self, get_response):
        self.get_response = get_response
        self.policy = getattr(settings, "SECURE_CSP", "") or ""

    def __call__(self, request):
        response = self.get_response(request)
        if self.policy:
            # 只有在视图/其他中间件没设过时才补，避免覆盖更细粒度的策略
            if "Content-Security-Policy" not in response:
                response["Content-Security-Policy"] = self.policy
        return response
