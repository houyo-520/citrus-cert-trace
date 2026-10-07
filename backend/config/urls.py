"""
URL 路由总入口
"""
from django.contrib import admin
from django.urls import path, re_path, include

from apps.traceability.media_view import ProtectedMediaView

urlpatterns = [
    # ---------------------------------------------------------------
    # ★ Django 自带 /admin/ 后台已关闭。
    # 理由：它是面向开发期「直接改数据库」的裸后门，公网暴露下等于
    # 多一个不受本系统权限体系约束的登录入口，与「登录页不暴露凭据」
    # 同一安全考量。业务操作一律走 /api/ 权限体系，无需 admin。
    # 如需临时管理数据，用 `manage.py shell` 或 init_data 命令。
    # ---------------------------------------------------------------
    # path("admin/", admin.site.urls),
    path("api/auth/", include("apps.accounts.urls")),
    path("api/", include("apps.traceability.urls")),
    # ---------------------------------------------------------------
    # 检测报告附件等用户上传文件的访问入口。
    #
    # ★ 安全修复（原 C3）：早期用 django.views.static.serve 直挂 /media/，
    #   它是纯 Django 视图、不经过 DRF 权限层 —— 任何人拿到路径即可匿名
    #   下载检测报告原件（含检测机构名，扫描件可能含个人信息），已实测确认。
    #   现改为 ProtectedMediaView（IsAuthenticated + 数据级归属校验）。
    #
    # 生产更优解：由 nginx 托管 /media/ 并用 X-Accel-Redirect（零拷贝）。
    # 当前附件量小、Django 与 nginx 同机，先在应用层做对鉴权更重要。
    # ---------------------------------------------------------------
    re_path(r"^media/(?P<path>.*)$", ProtectedMediaView.as_view(),
            name="protected-media"),
]