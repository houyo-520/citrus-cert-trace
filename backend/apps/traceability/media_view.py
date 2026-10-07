"""
受保护的附件访问（/media/ 鉴权托管）

为什么需要这个模块：
  原实现用 `django.views.static.serve` 直挂 /media/，它是纯 Django 视图，
  **不经过 DRF 权限层** —— 任何人拿到路径就能匿名下载检测报告原件
  （含检测机构名，扫描件还可能被 OCR 出个人信息）。这是实测确认的漏洞。

设计：
  - 用 APIView 承载，`permission_classes = [IsAuthenticated]` 强制登录；
  - 再按业务做数据级校验：检测报告附件只对该报告所属主体（或其 owner）、
    监管/管理员开放；其余 media（如未来新增类型）默认要求登录即可。
  - 用 FileResponse 流式返回，避免把大文件一次性读进内存。

为什么不用 nginx 托管：
  生产更优解是 nginx `internal` + `X-Accel-Redirect`（零拷贝、性能最好）。
  但当前 Django 与 nginx 同机、附件量小，先在应用层做对鉴权更重要；
  后续要提速可平滑切到 X-Accel-Redirect，只需改这一个视图。
"""
import mimetypes
import os

from django.conf import settings
from django.http import FileResponse, Http404
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.accounts.models import Role

from .models import TestReport


class ProtectedMediaView(APIView):
    """
    鉴权后的 /media/<path> 文件访问。

    仅处理「已知业务可访问」的文件；路径不合法或文件不存在一律 404，
    不向调用方泄露目录结构。
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, path):
        # 1) 防目录穿越：规范化后必须仍落在 MEDIA_ROOT 内
        media_root = os.path.realpath(str(settings.MEDIA_ROOT))
        target = os.path.realpath(os.path.join(media_root, path))
        if not target.startswith(media_root + os.sep):
            raise Http404("文件不存在")
        if not os.path.isfile(target):
            raise Http404("文件不存在")

        # 2) 数据级校验：检测报告附件需按归属放行
        user = request.user
        if path.startswith("reports/"):
            if not self._can_access_report(user, path):
                # 用 404 而非 403，避免确认「该文件存在但不是你的」
                raise Http404("文件不存在")

        content_type, _ = mimetypes.guess_type(target)
        return FileResponse(open(target, "rb"),
                            content_type=content_type or "application/octet-stream")

    @staticmethod
    def _can_access_report(user, path):
        """
        能否访问某个 reports/ 下的附件。

        规则（与 TestReport 的数据可见范围一致）：
          - 监管/管理员：全部可读；
          - 种植主体：仅自己名下主体的报告；
          - 收购商：仅已审核通过主体的报告；
          - 其余：拒绝。
        """
        role = getattr(user, "role", None)
        if role in (Role.ADMIN, Role.REGULATOR):
            return True

        # 附件在 TestReport.file 上，按存储相对路径精确匹配（file.name 形如 reports/xxx.pdf）
        rel = path.replace("\\", "/")
        report = (TestReport.objects
                  .select_related("batch", "batch__producer")
                  .filter(file=rel)
                  .first())
        if report is None:
            return False

        producer = report.batch.producer if report.batch_id else None
        if producer is None:
            return False
        if role == Role.PRODUCER:
            return producer.owner_id == user.id
        if role == Role.BUYER:
            from .models import Producer
            return producer.audit_status == Producer.AuditStatus.APPROVED
        return False
