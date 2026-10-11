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


# ===============================================================
# 强制改密拦截
# ===============================================================
"""
★ 第三轮审计修复（Medium，置信度 10/10）：
  上一轮给 User 加了 `must_change_password` 字段、也加了改密接口，但**没有真正强制** ——
  登录后照样发 JWT，用户可以直接用业务接口跳过改密；前端也只是「可以显示提示」。
  结果是：演示账号被轮换了口令、也标了待改密，但改密成了纯可选操作。

本中间件的职责：待改密用户**只能**访问白名单接口，其余业务请求一律 403 并给出
明确指引。这样「强制」才落在服务端，而不是靠前端自觉。

放行白名单（待改密用户仍需要的）：
  - 改密接口 `/api/auth/change-password/`
  - 注销 `/api/auth/logout/`、刷新 `/api/auth/refresh/`
  - 查自己 `/api/auth/profile/`、登录、公开接口 `/api/public/**`
  - 静态资源 / 后台

实现说明：DRF 的 JWT 认证发生在**视图层**，而中间件跑在其之前 ——
此时 request.user 可能仍是 AnonymousUser。因此这里自行解析 Authorization 头，
避免依赖 request.user；解析失败即放行，交给后续正常鉴权（未登录本就 401）。
"""
import re

from django.http import JsonResponse

# 待改密用户仍可访问的路径前缀（正则）
_PWD_ALLOW_PATTERNS = [
    r"^/api/auth/login/?$",
    r"^/api/auth/logout/?$",
    r"^/api/auth/refresh/?$",
    r"^/api/auth/profile/?$",
    r"^/api/auth/change-password/?$",
    r"^/api/auth/config/?$",
    r"^/api/public/",          # 公众验真等免登录接口
    r"^/static/", r"^/media/",
    # ★ 第四轮审计（ADMIN-WHITELIST-BYPASS，低）：**不再放行 /admin/**。
    #   项目已关闭 Django admin 路由（config/urls.py 注释掉 path("admin/")），
    #   放行它当前没有暴露面，但若将来重开 admin，待改密用户就能绕过本中间件
    #   直达后台。窄化白名单，只保留业务必需项。
]
_PWD_ALLOW_RE = re.compile("|".join(_PWD_ALLOW_PATTERNS))


class ForcePasswordChangeMiddleware:
    """待改密用户仅可访问白名单接口，其余一律 403。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._should_block(request):
            # ★ 第四轮审计修复（FORCE-PWD-MIDDLEWARE-RESPONSE，高，已实机复现）：
            #   原先这里返回 DRF 的 `rest_framework.response.Response` ——
            #   但中间件短路发生在 **DRF 视图的 finalize_response 之前**，
            #   Response 没被设置 accepted_renderer、也没 render()。
            #   后果（实测）：403 状态行与响应头已 flush，随后 WSGI 抛
            #     ContentNotRenderedError: The response content must be rendered
            #     before it can be iterated over.
            #   → 服务端每次记一条未处理异常，客户端 body 为空，
            #     连「请先改密」的提示都到不了前端。
            #   修法：改用 Django 原生 JsonResponse（自己完成序列化与渲染）。
            return JsonResponse(
                {"code": 403,
                 "message": "您的账号处于「首次登录须修改密码」状态，"
                            "请先修改密码后再使用其他功能。",
                 "must_change_password": True},
                status=403,
                json_dumps_params={"ensure_ascii": False},
                headers={"X-Must-Change-Password": "1"})
        return self.get_response(request)

    def _should_block(self, request) -> bool:
        if getattr(settings, "DISABLE_FORCE_PASSWORD_CHANGE", False):
            return False
        if _PWD_ALLOW_RE.match(request.path or ""):
            return False
        user = self._resolve_user(request)
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        return bool(getattr(user, "must_change_password", False))

    @staticmethod
    def _resolve_user(request):
        """从 Authorization 头解析 JWT 并取用户（不依赖 request.user）。"""
        auth = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth.lower().startswith("bearer "):
            return None
        token = auth.split(" ", 1)[1].strip()
        if not token:
            return None
        try:
            from django.contrib.auth import get_user_model
            from rest_framework_simplejwt.tokens import AccessToken
            validated = AccessToken(token)
            uid = validated.get("user_id")
            if not uid:
                return None
            return get_user_model().objects.filter(id=uid).first()
        except Exception:  # noqa: BLE001 过期/非法 token 交给后续鉴权处理
            return None
