"""
认证视图：登录返回 JWT + 用户信息
"""
from django.contrib.auth import authenticate
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import LoginRecord, parse_user_agent
from .serializers import UserSerializer
from .throttling import clear_failures, is_blocked, record_failure, _client_ip


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username = request.data.get("username", "").strip()
        password = request.data.get("password", "")
        ip = _client_ip(request)

        # ★ 限流：同一 IP 或同一用户名短期内频繁失败 → 直接 429，
        #   不进入密码校验，从根上阻断暴力破解。
        if is_blocked(ip, username):
            self._log(request, username, success=False,
                      detail=f"账号 {username} 登录被限流拦截（来源 {ip}）")
            self._record_login(request, username, success=False,
                               fail_reason="限流拦截")
            return Response(
                {"code": 429, "message": "尝试过于频繁，请稍后再试"},
                status=status.HTTP_429_TOO_MANY_REQUESTS)

        user = authenticate(username=username, password=password)
        if user is None:
            record_failure(ip, username)
            # 登录失败也留痕（安全审计需要）
            self._log(request, username, success=False,
                      detail=f"账号 {username} 登录失败：用户名或密码错误")
            self._record_login(request, username, success=False,
                               fail_reason="用户名或密码错误")
            return Response({"code": 400, "message": "用户名或密码错误"},
                            status=status.HTTP_400_BAD_REQUEST)
        if not user.is_active:
            self._log(request, username, success=False,
                      detail=f"账号 {username} 登录失败：账号已被禁用")
            self._record_login(request, username, success=False,
                               user=user, fail_reason="账号已被禁用")
            return Response({"code": 400, "message": "账号已被禁用"},
                            status=status.HTTP_400_BAD_REQUEST)

        refresh = RefreshToken.for_user(user)
        # 登录成功：清空该用户/该 IP 的失败记录，避免正常用户被误锁
        clear_failures(ip=ip, username=username)
        self._log(request, username, success=True,
                  detail=f"{user.username}({user.get_role_display()}) 登录成功",
                  operator=user)
        self._record_login(request, username, success=True, user=user)
        return Response({
            "code": 200,
            "message": "登录成功",
            "data": {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UserSerializer(user).data,
            },
        })

    @staticmethod
    def _log(request, username, success=True, detail="", operator=None):
        """写登录审计日志（延迟导入，避免 app 循环依赖）。"""
        try:
            from apps.traceability.audit import log_operation
            from apps.traceability.models import OperationLog
            log_operation(request, OperationLog.Action.LOGIN,
                          target_type="用户", target_no=username,
                          detail=detail, success=success, operator=operator)
        except Exception:
            # 审计失败不能影响登录主流程
            pass

    @classmethod
    def _record_login(cls, request, username, success=True, user=None,
                      fail_reason=""):
        """
        写登录历史（含 IP + 地理位置），供管理员登录监控。

        ★ 绝不影响登录主流程：地理位置解析、数据库写入全部 try/except 兜住，
          登录历史是「审计增强」，不能因为这张表故障就把登录搞挂。
        """
        try:
            from apps.traceability import geoloc
            ip = _client_ip(request)
            geo = geoloc.resolve(ip)
            ua = request.META.get("HTTP_USER_AGENT", "") or ""
            LoginRecord.objects.create(
                user=user,
                username=(user.username if user else username) or "",
                role=getattr(user, "role", "") or "",
                ip=geo.get("ip", ip) or ip,
                province=geo.get("province", ""),
                city=geo.get("city", ""),
                isp=geo.get("isp", ""),
                location=geo.get("location", ""),
                is_internal=geo.get("is_internal", False),
                success=success,
                fail_reason=fail_reason,
                user_agent=parse_user_agent(ua),
            )
        except Exception:
            # 登录历史写入失败不阻断登录
            pass


class ChangePasswordView(APIView):
    """
    修改自己的密码。

    ★ 续审修复：配合 `must_change_password` —— 演示账号首次登录后需要一个
    可用的改密入口，否则「强制改密」没有落地路径。
    入参：{"old_password": "...", "new_password": "..."}
    成功后清除 must_change_password 标记。
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        old = (request.data.get("old_password") or "").strip()
        new = (request.data.get("new_password") or "").strip()
        if not old or not new:
            return Response({"code": 400, "message": "请填写原密码与新密码"},
                            status=status.HTTP_400_BAD_REQUEST)
        if not user.check_password(old):
            return Response({"code": 400, "message": "原密码不正确"},
                            status=status.HTTP_400_BAD_REQUEST)
        if len(new) < 8:
            # 演示口令是随机 16 位；新口令要求至少 8 位，避免改成 123456
            return Response({"code": 400, "message": "新密码长度至少 8 位"},
                            status=status.HTTP_400_BAD_REQUEST)
        if new == old:
            return Response({"code": 400, "message": "新密码不能与原密码相同"},
                            status=status.HTTP_400_BAD_REQUEST)
        user.set_password(new)
        fields = ["password"]
        if hasattr(user, "must_change_password"):
            user.must_change_password = False
            fields.append("must_change_password")
        user.save(update_fields=fields)

        # ★ 第三轮审计修复：改密后**撤销该用户所有旧 refresh 会话**。
        #   原实现只改口令，旧 refresh 仍能换新 access —— 对「待改密」场景尤其糟糕：
        #   演示账号口令被轮换了，但攻击者若已持有旧 refresh，改密也踢不掉他。
        #   做法：用 simplejwt 黑名单把该用户全部未过期 refresh 拉黑。
        revoked = 0
        try:
            from rest_framework_simplejwt.token_blacklist.models import (
                OutstandingToken)
            from rest_framework_simplejwt.tokens import RefreshToken as RT
            for tok in OutstandingToken.objects.filter(user=user):
                try:
                    RT(tok.token).blacklist()
                    revoked += 1
                except Exception:  # noqa: BLE001 已过期/已拉黑都跳过
                    continue
        except Exception:  # noqa: BLE001 黑名单应用未启用时静默跳过
            pass

        return Response({"code": 200, "message": "密码已修改，请重新登录",
                         "data": {"revoked_sessions": revoked}})


class ProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"code": 200, "message": "ok",
                         "data": UserSerializer(request.user).data})

class LogoutView(APIView):
    """
    注销：把 refresh token 加入黑名单。

    ★ 为什么需要（JWT 加固）：JWT 是无状态的，签出去就在有效期内一直有效，
    没有服务端「注销」概念。开启 token_blacklist 后，注销时把 refresh 拉黑，
    该 refresh 无法再换新 access；配合 ROTATE+BACKLIST，泄露窗口大幅收窄。
    入参：{"refresh": "<refresh token>"}。access 无需传（短时效自然过期）。
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = request.data.get("refresh")
        if not token:
            return Response({"code": 400, "message": "缺少 refresh token"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            from rest_framework_simplejwt.tokens import RefreshToken as RT
            RT(token).blacklist()
        except Exception:  # noqa: BLE001 token 非法/已过期/已拉黑都算注销成功
            pass
        return Response({"code": 200, "message": "已注销", "data": None})
