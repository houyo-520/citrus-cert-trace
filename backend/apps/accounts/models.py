"""
用户与角色模型
四类角色：监管人员 / 种植主体 / 收购商 / 公众
"""
from django.conf import settings
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
    # 微信小程序登录标识（绑定后用于免密登录）。该字段由线上迁移
    # 0003_user_openid 引入，本地模型需与之保持一致，否则会生成反向迁移。
    openid = models.CharField("微信openid", max_length=64, blank=True, default="",
                              db_index=True,
                              help_text="微信小程序登录标识，绑定后用于免密登录")
    # ★ 续审修复：强制首次改密标记。
    #   原 init_data 用 hasattr(user, "must_change_password") 判断，但模型**没有**
    #   该字段 → 分支永不生效，「创建演示账号后强制改密」形同虚设。
    #   现补上字段：init_data 创建演示账号时置 True，登录接口据此提示/拦截。
    must_change_password = models.BooleanField(
        "需强制修改密码", default=False,
        help_text="为 True 时，登录后应提示（或强制）用户修改口令")

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


class LoginRecord(models.Model):
    """
    登录历史记录（供系统管理员做「登录 IP / 地点」安全监控）。

    为什么独立于 OperationLog：
      OperationLog 是「谁对哪条业务数据做了什么」的通用审计，
      而登录监控需要的是「按用户维度聚合」的登录时间线 ——
      该用户最近从哪些 IP、哪些地点、成功还是失败。两者粒度不同。

      这张表「只增不删」，是账号安全审计的原始证据：
      异地登录、异常时段登录、高频失败登录都能从这里直接看出。
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                             null=True, blank=True, verbose_name="登录用户",
                             related_name="login_records")
    username = models.CharField("登录账号", max_length=150, blank=True, default="")
    role = models.CharField("角色", max_length=20, blank=True, default="")
    ip = models.CharField("来源IP", max_length=64, blank=True, default="",
                          db_index=True)
    # ---- 地理位置（离线解析，见 traceability/geoloc.py）----
    province = models.CharField("省份", max_length=50, blank=True, default="")
    city = models.CharField("城市", max_length=50, blank=True, default="")
    isp = models.CharField("运营商", max_length=50, blank=True, default="")
    location = models.CharField("地理位置", max_length=100, blank=True, default="")
    is_internal = models.BooleanField("是否内网", default=False)
    # ---- 登录结果 ----
    success = models.BooleanField("是否成功", default=True, db_index=True)
    fail_reason = models.CharField("失败原因", max_length=100, blank=True, default="")
    # ---- 设备指纹 ----
    user_agent = models.CharField("浏览器/设备", max_length=255, blank=True, default="")
    login_at = models.DateTimeField("登录时间", auto_now_add=True, db_index=True)

    class Meta:
        db_table = "sys_login_record"
        verbose_name = "登录历史"
        verbose_name_plural = "登录历史"
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["user", "login_at"]),
            models.Index(fields=["ip", "login_at"]),
            models.Index(fields=["success", "login_at"]),
        ]

    def __str__(self):
        return f"{self.username} @ {self.ip} {self.login_at}"


def parse_user_agent(ua: str) -> str:
    """从 User-Agent 提取可读的设备/浏览器信息（用于登录监控展示）。

    只做粗粒度识别，不追求覆盖所有浏览器：识别出「浏览器名 + 系统」即可，
    无法识别时原样截断返回，绝不抛异常。
    """
    if not ua:
        return ""
    ua = ua[:255]
    os_name = ""
    for key, name in (("Windows", "Windows"), ("Mac OS", "macOS"),
                      ("iPhone", "iOS"), ("Android", "Android"),
                      ("Linux", "Linux"), ("iPad", "iPadOS")):
        if key in ua:
            os_name = name
            break
    browser = ""
    if "Edg/" in ua or "Edge/" in ua:
        browser = "Edge"
    elif "Chrome/" in ua:
        browser = "Chrome"
    elif "Firefox/" in ua:
        browser = "Firefox"
    elif "Safari/" in ua:
        browser = "Safari"
    elif "MicroMessenger" in ua:
        browser = "微信内置浏览器"
    elif "Mozilla" in ua:
        browser = "其他浏览器"
    return f"{browser} / {os_name}".strip(" /") or ua