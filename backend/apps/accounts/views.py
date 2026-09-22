"""
认证视图：登录返回 JWT + 用户信息
"""
from django.contrib.auth import authenticate
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .serializers import UserSerializer


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username = request.data.get("username", "").strip()
        password = request.data.get("password", "")
        user = authenticate(username=username, password=password)
        if user is None:
            # 登录失败也留痕（安全审计需要）
            self._log(request, username, success=False,
                      detail=f"账号 {username} 登录失败：用户名或密码错误")
            return Response({"code": 400, "message": "用户名或密码错误"},
                            status=status.HTTP_400_BAD_REQUEST)
        if not user.is_active:
            self._log(request, username, success=False,
                      detail=f"账号 {username} 登录失败：账号已被禁用")
            return Response({"code": 400, "message": "账号已被禁用"},
                            status=status.HTTP_400_BAD_REQUEST)

        refresh = RefreshToken.for_user(user)
        self._log(request, username, success=True,
                  detail=f"{user.username}({user.get_role_display()}) 登录成功",
                  operator=user)
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


class ProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"code": 200, "message": "ok",
                         "data": UserSerializer(request.user).data})