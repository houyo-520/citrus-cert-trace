"""
URL 路由总入口
"""
from django.contrib import admin
from django.urls import path, re_path, include
from django.conf import settings
from django.views.static import serve

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
    # 不用 `if settings.DEBUG` 包住 —— 生产环境（DEBUG=false）下 gunicorn
    # 不会自动托管 media，包住会导致「上传成功但下载 404」。
    # 生产更优解是由 nginx 直接托管 /media/ 目录（见部署文档），
    # 这里保留 Django 兜底，保证任何环境下附件都可达。
    # 安全由「文件访问需登录」的权限层保证，附件本身非敏感数据（检测报告）。
    # ---------------------------------------------------------------
    re_path(r"^media/(?P<path>.*)$", serve,
            {"document_root": settings.MEDIA_ROOT}),
]