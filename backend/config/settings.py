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

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-citrus-cert-trace-2026-change-me-in-production",
)
# ★ 安全默认（原 C4 修复）：DEBUG 默认 **false**，而不是 true。
#   原实现默认 "true" —— 一旦忘设 DJANGO_DEBUG，DEBUG 就是 True，
#   于是下面的「生产密钥断言」整块被跳过、ALLOWED_HOSTS 落到 *、
#   错误页还会回显堆栈。安全配置不能靠「你记得设」，必须默认最严。
#   本地开发请在 .env 里显式写 DJANGO_DEBUG=true。
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"

# ★ 生产安全断言：DEBUG=false 时若仍用默认/明显弱密钥，直接拒绝启动。
#   防止「代码能跑、功能正常」但密钥是模板值的假上线 —— 攻击者拿到 SECRET_KEY
#   即可伪造会话/签名。默认值只在本地开发（DEBUG=true）允许。
_DEFAULT_INSECURE_KEYS = {
    "django-insecure-citrus-cert-trace-2026-change-me-in-production",
}
if not DEBUG and SECRET_KEY in _DEFAULT_INSECURE_KEYS:
    raise RuntimeError(
        "生产环境未设置 DJANGO_SECRET_KEY，拒绝启动。"
        "请在 .env 中设置一个随机强密钥（如 `python -c "
        "\"import secrets; print(secrets.token_urlsafe(64))\"` 生成）。"
    )

# ★ ALLOWED_HOSTS 默认收紧（原 C4 修复）：不再默认 "*"。
#   未显式配置时只允许本机/回环，避免「忘设 → 任意 Host 头被接受」。
_ALLOWED_HOSTS_DEFAULT = "localhost,127.0.0.1"
ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", _ALLOWED_HOSTS_DEFAULT).split(",")
    if h.strip()
]

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
STATIC_ROOT = BASE_DIR / "staticfiles"
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
# CORS：前后端分离，白名单控制跨域来源。
# ★ 之前 CORS_ALLOW_ALL_ORIGINS=True 意味着任意网站都能跨域调用本 API，
#   公网暴露下等于把接口开放给所有站点。改为白名单：
#   - 同源部署（nginx 反代，前端与 /api 同域）时，浏览器不跨域，无需 CORS
#   - 本地开发时前端跑 5173，需放行 localhost:5173
#   - 可通过 CORS_ALLOWED_ORIGINS 环境变量追加（逗号分隔）
# ---------------------------------------------------------------
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if o.strip()
]

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

# ---------------------------------------------------------------
# 可信存证哈希加盐（Salted Hash）
# ---------------------------------------------------------------
# 存证摘要采用「加盐 SHA-256」：hash = sha256(salt + payload)。
# 加盐的价值：抵御彩虹表/字典逆向 —— 即便某方拿到公开的规范化原文与摘要，
# 也无法用常见字典反向碰撞出原始字段（例如试图离线枚举果农手机号等敏感值）。
# 盐值属于「私密参数」，必须通过环境变量注入，绝不写进 Git/前端。
# 生产环境未设置时给默认值（仅本地开发），生产会在 DEBUG=false + 弱盐时拒绝启动。
EVIDENCE_SALT = os.environ.get("EVIDENCE_SALT", "")
if not EVIDENCE_SALT and not DEBUG:
    raise RuntimeError(
        "生产环境未设置 EVIDENCE_SALT，拒绝启动。"
        "请在 .env 中设置随机盐值（如 `python -c "
        "\"import secrets; print(secrets.token_urlsafe(32))\"` 生成）。"
    )

# ---------------------------------------------------------------
# 缓存：验真接口限流等用途（生产建议换 Redis，单机用 LocMem 即可）
# ---------------------------------------------------------------
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "citrus-local-cache",
    }
}

# 验真接口限流：每个来源 IP 每分钟允许的查询次数（防刷/防拖库）
VERIFY_RATE_LIMIT = int(os.environ.get("VERIFY_RATE_LIMIT", "30"))

# ★ 可信代理层数（原 M6 修复配套）：用于从 X-Forwarded-For 中取出真实客户端 IP。
#   本项目 Django 前面只有一层自建 nginx（且在 nginx 里覆写了 X-Real-IP），
#   因此默认 1 —— 优先用 X-Real-IP，退化到 XFF 时从右往左数第 1 跳。
#   若将来前面加了 CDN / 多层代理，需相应增大此值，否则取到的会是对端代理 IP。
TRUSTED_PROXY_HOPS = int(os.environ.get("TRUSTED_PROXY_HOPS", "1"))

# ★ 可信代理 IP 白名单（B4 修复）：只有 REMOTE_ADDR 属于此列表时，
#   才采信 X-Real-IP / X-Forwarded-For。默认本机（nginx 与 gunicorn 同机）。
#   若 gunicorn 意外对外监听，攻击者直连时伪造的转发头会因 REMOTE_ADDR 不在
#   白名单而被忽略 —— 这是抵御「伪造来源 IP 绕限流」的最后一道闸。
TRUSTED_PROXY_IPS = [
    ip.strip()
    for ip in os.environ.get("TRUSTED_PROXY_IPS", "127.0.0.1,::1").split(",")
    if ip.strip()
]

# ★ OCR 资源上限（M13 修复）：OCR 是「上传即触发」的同步入口，
#   没有上限时一张解压炸弹图/超大 MediaBox 的 PDF 就能把 worker 拖死。
OCR_MAX_IMAGE_PIXELS = int(os.environ.get("OCR_MAX_IMAGE_PIXELS", "40000000"))
OCR_MAX_PDF_PAGES = int(os.environ.get("OCR_MAX_PDF_PAGES", "3"))
OCR_MAX_PDF_DPI = int(os.environ.get("OCR_MAX_PDF_DPI", "200"))
OCR_MAX_PDF_RENDER_PIXELS = int(os.environ.get("OCR_MAX_PDF_RENDER_PIXELS",
                                               "40000000"))
OCR_TIMEOUT_SECONDS = int(os.environ.get("OCR_TIMEOUT_SECONDS", "60"))