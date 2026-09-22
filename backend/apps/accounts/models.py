"""
用户与角色模型
四类角色：监管人员 / 种植主体 / 收购商 / 公众
"""
from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.TextChoices):
    ADMIN = "admin", "系统管理员"
    REGULATOR = "regulator", "监管人员"
    PRODUCER = "producer", "种植主体"
    BUYER = "buyer", "收购商"


class User(AbstractUser):
    """扩展用户：角色 + 归属主体名称"""
    role = models.CharField("角色", max_length=20, choices=Role.choices,
                            default=Role.PRODUCER)
    phone = models.CharField("联系电话", max_length=20, blank=True, default="")
    org_name = models.CharField("所属单位/主体名称", max_length=100, blank=True, default="")

    class Meta:
        db_table = "sys_user"
        verbose_name = "用户"
        verbose_name_plural = "用户"

    def __str__(self):
        return f"{self.username}({self.get_role_display()})"

    @property
    def is_regulator(self):
        return self.role in (Role.ADMIN, Role.REGULATOR)

    @property
    def is_producer(self):
        return self.role == Role.PRODUCER