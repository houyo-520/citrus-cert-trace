"""
认证相关序列化器
"""
from rest_framework import serializers
from .models import User


class UserSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "nickname", "email", "phone",
                  "role", "role_display", "org_name", "is_active"]
        extra_kwargs = {"username": {"read_only": True}}

    # AbstractUser 没有 nickname 字段，这里提供兼容属性
    nickname = serializers.SerializerMethodField()

    def get_nickname(self, obj):
        return obj.first_name or obj.username


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)