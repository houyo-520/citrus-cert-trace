"""
Django settings —— 柑橘合格证追溯系统
"""
import os
from pathlib import Path
from datetime import timedelta

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path):
    """
    极简 .env 加载器（零依赖）：
      - 已存在的环境变量优先，不被 .env 覆盖
      - 支持 KEY=VALUE、# 注释、引号包裹
    生产环境建议改用 python-dotenv 或系统级环境变量注入。
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(BASE_DIR / ".env")

SECRET_KEY = "django-insecure-citrus-cert-trace-2026-change-me-in-production"
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # 第三方
    "rest_framework",
    "corsheaders",
    # 业务应用
    "apps.accounts",
    "apps.traceability",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------
# 数据库：默认 SQLite（零配置演示）。切换 MySQL 见下方注释。
# ---------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# 如需 MySQL：取消注释并安装 PyMySQL，在 config/__init__.py 里已做 pymysql 兼容
# DATABASES = {
#     "default": {
#         "ENGINE": "django.db.backends.mysql",
#         "NAME": "citrus_trace",
#         "USER": "root",
#         "PASSWORD": "root",
#         "HOST": "127.0.0.1",
#         "PORT": "3306",
#         "OPTIONS": {"charset": "utf8mb4"},
#     }
# }

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 6}},
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = False

STATIC_URL = "static/"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------
# DRF 配置
# ---------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 10,
    "DATETIME_FORMAT": "%Y-%m-%d %H:%M:%S",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=12),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# ---------------------------------------------------------------
# CORS：前后端分离，允许前端开发服务器访问
# ---------------------------------------------------------------
CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True

# 前端验真页地址（二维码指向）
FRONTEND_VERIFY_BASE = os.environ.get(
    "FRONTEND_VERIFY_BASE", "http://localhost:5173/#/verify"
)

# ---------------------------------------------------------------
# FISCO BCOS 联盟链配置
# ---------------------------------------------------------------
# 链节点：<your-server-ip> 单机2节点（chain0/group0，PBFT），已开启 web3 RPC
# - 若 Django 与链同机部署，用 127.0.0.1（推荐，安全）
# - 若跨机访问，改成 http://<your-server-ip>:8545，并确保云安全组已放行
FISCO_BCOS = {
    "WEB3_RPC": os.environ.get("FISCO_WEB3_RPC", "http://127.0.0.1:8545"),
    "CHAIN_ID": int(os.environ.get("FISCO_CHAIN_ID", "20200")),
    "GROUP_ID": os.environ.get("FISCO_GROUP_ID", "group0"),
    # 部署 CertificateEvidence 合约后填入地址与存证函数选择器；留空则走「纯 data 交易」模式
    "CONTRACT_ADDRESS": os.environ.get("FISCO_CONTRACT_ADDRESS", ""),
    "EVIDENCE_METHOD_ID": os.environ.get("FISCO_EVIDENCE_METHOD_ID", ""),
    # 存证账户私钥：务必通过环境变量注入，不要写进代码/Git
    "PRIVATE_KEY": os.environ.get("FISCO_PRIVATE_KEY", ""),
    "GAS_LIMIT": int(os.environ.get("FISCO_GAS_LIMIT", "300000")),
    # 是否启用真实上链（False 则一律走模拟存证，便于离线开发）
    "ENABLE_CHAIN": os.environ.get("FISCO_ENABLE_CHAIN", "true").lower() == "true",
}