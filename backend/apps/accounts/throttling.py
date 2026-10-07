"""
登录接口限流（防盗爆破）

为什么不用 DRF 内置 AnonRateThrottle / ScopedRateThrottle：
它们依赖 CACHE 后端。本系统生产用 gunicorn 多 worker + SQLite，若走 locmem 缓存，
每个 worker 内存各自独立，限流阈值会被 worker 数稀释，攻击者并发/轮换 IP 即可绕过。

本实现改为「数据库表 + 时间窗」，跨 worker / 跨进程天然共享：
1. 失败只记录、不原地更新计数（SQLite 无行锁，避免 update_or_create 类锁冲突）
2. 计数 = 每次查询窗口内条数，幂等无副作用
3. 判定：来源 IP 或用户名任一超过阈值 → 429（不区分密码对错，
   避免「猜中一个再换下一个」的侧信道）
4. 登录成功清空对应失败记录，避免正常用户偶发输错多次被误锁
"""
import os

from django.db import models
from django.utils import timezone


_WINDOW_SECONDS = int(os.environ.get("LOGIN_THROTTLE_WINDOW", "300"))
_MAX_FAILURES = int(os.environ.get("LOGIN_THROTTLE_MAX", "10"))


class LoginAttempt(models.Model):
    """登录失败记录（限流用）。只增不删，靠时间窗过滤过期数据。"""
    ip = models.CharField(max_length=64, db_index=True)
    username = models.CharField(max_length=150, db_index=True, blank=True, default="")
    attempted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sys_login_attempt"
        verbose_name = "登录失败记录"
        verbose_name_plural = "登录失败记录"
        indexes = [
            models.Index(fields=["ip", "attempted_at"]),
            models.Index(fields=["username", "attempted_at"]),
        ]

    def __str__(self):
        return f"{self.ip} -> {self.username} @ {self.attempted_at}"


def _client_ip(request):
    """
    取真实来源 IP。

    ★ B4 修复：统一改走 netutil.get_client_ip —— 只有 REMOTE_ADDR 属于可信代理
    （默认 127.0.0.1/::1）时才采信 X-Real-IP / XFF；直连时一律用 REMOTE_ADDR，
    避免「绕过 nginx 直连 8000 端口 + 伪造 X-Real-IP」绕过 IP 限流。
    同时与审计日志共用同一函数，跳数规则不再两处各写一份。
    """
    from .netutil import get_client_ip
    return get_client_ip(request) or "unknown"


def _cutoff():
    return timezone.now() - timezone.timedelta(seconds=_WINDOW_SECONDS)


def is_blocked(ip, username):
    """窗口期内，IP 或用户名失败次数是否超阈值。"""
    cutoff = _cutoff()
    if LoginAttempt.objects.filter(ip=ip, attempted_at__gte=cutoff).count() >= _MAX_FAILURES:
        return True
    if username and LoginAttempt.objects.filter(
            username=username, attempted_at__gte=cutoff).count() >= _MAX_FAILURES:
        return True
    return False


def record_failure(ip, username):
    """登录失败记一条。"""
    LoginAttempt.objects.create(ip=ip, username=username or "")


def clear_failures(ip=None, username=None):
    """
    登录成功清除对应失败记录，避免正常用户被误锁。

    ★ 修复（原 Minor）：原实现 ip 与 username 是 **AND** —— 只删「同 IP 且同用户名」
    的行。但 is_blocked 是「IP 或用户名」任一超限即拦，清空也必须按 **OR** 语义：
    只要匹配该 IP 或该用户名，就一并清掉，否则用户在 A 网络输错几次、
    换到 B 网络登录成功后，A 网络留下的用户名维度失败记录仍会累积把账号锁死。
    """
    from django.db.models import Q

    cond = Q()
    if ip:
        cond |= Q(ip=ip)
    if username:
        cond |= Q(username=username)
    if not cond:
        return
    LoginAttempt.objects.filter(cond).delete()