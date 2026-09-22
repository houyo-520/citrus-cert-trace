"""
认证相关序列化器
"""
from rest_framework import serializers
from .models import User


class UserSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(source="get_role_display", read_only=True)
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "nickname", "email", "phone",
                  "role", "role_display", "org_name", "is_active", "permissions"]
        extra_kwargs = {"username": {"read_only": True}}

    # AbstractUser 没有 nickname 字段，这里提供兼容属性
    nickname = serializers.SerializerMethodField()

    def get_nickname(self, obj):
        return obj.first_name or obj.username

    def get_permissions(self, obj):
        """
        返回该用户的权限清单，供前端做菜单/按钮级控制。

        与后端 permissions.py 中的判定保持一致 —— 前端隐藏只是体验优化，
        真正的拦截仍在后端（防绕过）。
        """
        role = obj.role
        is_regulator = role in ("admin", "regulator")
        return {
            "is_admin": role == "admin",
            "is_regulator": is_regulator,
            "is_producer": role == "producer",
            "is_buyer": role == "buyer",
            "can_audit": is_regulator,          # 主体审核
            "can_issue": is_regulator,          # 合格证签发
            "can_revoke": is_regulator,         # 合格证作废
            "can_anchor": is_regulator or role == "producer",   # 存证
            "can_write_production": is_regulator or role == "producer",  # 生产数据
            "can_publish_supply": role in ("admin", "regulator",
                                           "producer", "buyer"),  # 发布供需
            "can_view_all": is_regulator,       # 是否全量可见
            "can_view_logs_all": is_regulator,  # 是否可看全部操作日志
        }


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)