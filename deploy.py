"""
一键部署：把 Django 后端 + Vue 前端 dist 部署到 <your-server-ip>

用法（在本文件所在目录，即 源代码/ 下执行）：
  python deploy.py upload     # 上传源码与前端产物
  python deploy.py setup      # 服务器端安装依赖
  python deploy.py migrate    # 迁移 + collectstatic + 演示数据
  python deploy.py service    # 写 systemd 服务
  python deploy.py nginx      # 配置 nginx
  python deploy.py start      # 启动服务
  python deploy.py check      # 健康检查
  python deploy.py all        # 全流程

依赖：paramiko（本机 venv: C:/Users/houyo/.workbuddy/binaries/python/envs/default）

---------------------------------------------------------------
★ 凭据管理（重要）
---------------------------------------------------------------
本脚本**不再硬编码任何密码/密钥** —— 早先版本把服务器 SSH 密码、
Django SECRET_KEY、链上账户私钥直接写在源码里，一旦仓库公开就全泄露。

现在统一从「环境变量」或「.deploy_env 文件」读取，两者取其一：

  方式一：环境变量（推荐 CI / 临时部署）
    export CITRUS_SSH_PASSWORD='...'
    export CITRUS_SECRET_KEY='...'
    export CITRUS_CHAIN_PRIVATE_KEY='...'
    python deploy.py upload

  方式二：.deploy_env 文件（推荐日常开发，免每次手设）
    在 源代码/ 下建 .deploy_env，内容形如：
      CITRUS_SSH_PASSWORD=xxx
      CITRUS_SECRET_KEY=xxx
      CITRUS_CHAIN_PRIVATE_KEY=xxx
    该文件已在 .gitignore 中排除，不会被提交。

凭据实际值见 毕设源/凭据清单_请勿提交.md
"""
import sys, os, posixpath, stat, json, time
import paramiko

HOST = os.environ.get("CITRUS_SSH_HOST", "<your-server-ip>")
USER = os.environ.get("CITRUS_SSH_USER", "ubuntu")

REMOTE_ROOT = "/home/ubuntu/citrus"
REMOTE_BACKEND = REMOTE_ROOT + "/backend"
REMOTE_FRONT = REMOTE_ROOT + "/frontend_dist"

# 本地源码根 = 本脚本所在目录（源代码/），便于整体移动
LOCAL = os.path.dirname(os.path.abspath(__file__))


def _load_deploy_env():
    """
    读取 源代码/.deploy_env（若存在）并注入 os.environ。

    只填「尚未设置」的键 —— 已存在的环境变量优先，
    这样临时覆盖（export CITRUS_SSH_PASSWORD=xxx）始终生效。
    """
    path = os.path.join(LOCAL, ".deploy_env")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            # 容忍 `export KEY=value` 这种 shell 写法 —— 直接抄 shell 命令
            # 进来是很自然的动作，若不剥掉 `export ` 前缀，
            # 解析出的键会变成 "export KEY"，凭据静默读不到。
            if line.startswith("export "):
                line = line[len("export "):].strip()
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip("'\"")
            if k and k not in os.environ:
                os.environ[k] = v


_load_deploy_env()


def _need(key, hint):
    """取必需凭据；缺失时给出明确指引而不是抛 KeyError。"""
    val = os.environ.get(key, "").strip()
    if not val:
        print(f"❌ 缺少凭据 {key}")
        print(f"   请设置环境变量，或在 源代码/.deploy_env 中写入一行：")
        print(f"     {key}=<{hint}>")
        print(f"   凭据实际值见 毕设源/凭据清单_请勿提交.md")
        sys.exit(1)
    return val


PWD = os.environ.get("CITRUS_SSH_PASSWORD", "")
SECRET_KEY = os.environ.get("CITRUS_SECRET_KEY", "")


def conn():
    pwd = _need("CITRUS_SSH_PASSWORD", "服务器 ubuntu 用户密码")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=22, username=USER, password=pwd, timeout=25)
    return c


def run(cli, cmd, timeout=900, quiet=False):
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "ignore")
    err = stderr.read().decode("utf-8", "ignore")
    code = stdout.channel.recv_exit_status()
    if not quiet:
        print(f"$ {cmd[:110]}")
        if out.strip():
            print(out.rstrip())
        if err.strip():
            print("[stderr]", err.rstrip()[:2000])
        print(f"  -> exit {code}")
    return code, out, err


def mkdirs(sftp, path):
    parts = path.strip("/").split("/")
    cur = ""
    for p in parts:
        cur += "/" + p
        try:
            sftp.stat(cur)
        except IOError:
            sftp.mkdir(cur)


def put_dir(sftp, local_dir, remote_dir, skip=None):
    skip = skip or set()
    count = 0
    for root, dirs, files in os.walk(local_dir):
        dirs[:] = [d for d in dirs if d not in skip]
        rel = os.path.relpath(root, local_dir).replace("\\", "/")
        target = remote_dir if rel == "." else posixpath.join(remote_dir, rel)
        mkdirs(sftp, target)
        for f in files:
            if f in skip:
                continue
            lp = os.path.join(root, f)
            rp = posixpath.join(target, f)
            try:
                sftp.put(lp, rp)
                count += 1
            except Exception as e:
                print(f"  !! 上传失败 {rel}/{f}: {e}")
    return count


# ---------------------------------------------------------------- upload
def do_upload(cli):
    sftp = cli.open_sftp()
    mkdirs(sftp, REMOTE_ROOT)
    mkdirs(sftp, REMOTE_BACKEND)

    print("=== 1) 上传后端源码 ===")
    # ★ 关键：必须排除 db.sqlite3 与 .env
    #   - db.sqlite3 覆盖服务器会直接毁掉生产数据
    #   - .env 由本脚本在服务器端单独生成（含生产密钥）
    n1 = put_dir(
        sftp,
        os.path.join(LOCAL, "backend"),
        REMOTE_BACKEND,
        skip={"__pycache__", ".venv", "venv", "staticfiles", ".git",
              "db.sqlite3", ".env", "media"},
    )
    print(f"  上传 {n1} 个文件")

    print("=== 2) 上传前端构建产物（清洁同步）===")
    # ★ 必须先清空再上传：
    #   Vite 产物文件名带内容哈希，每次构建 hash 都变。若只上传不删除，
    #   历史 chunk 会无限累积（实测累积到 63 个 JS），配合浏览器缓存的旧
    #   index.html → 旧入口仍能引用到旧 chunk → 页面永远跑旧代码、报错照旧。
    #   清空可保证「服务器上只有本次构建的产物」。
    run(cli, f"rm -rf {REMOTE_FRONT}/assets {REMOTE_FRONT}/index.html", quiet=True)
    mkdirs(sftp, REMOTE_FRONT)
    n2 = put_dir(sftp, os.path.join(LOCAL, "frontend", "dist"), REMOTE_FRONT)
    print(f"  上传 {n2} 个文件（已先清空远端旧产物）")

    print("=== 3) 写入服务器专用 .env ===")
    # ★ 不覆盖已有的 .env
    #   服务器上的 .env 是生产配置的唯一来源（含真实密钥）。
    #   每次部署都重写它，意味着本地必须持有全部生产密钥 —— 正是我们要避免的。
    #   改为：只在服务器上还不存在 .env 时生成一份初始模板，其余情况原样保留。
    code, out, _ = run(cli, f"test -f {REMOTE_BACKEND}/.env && echo EXISTS || echo MISSING",
                       quiet=True)
    if "EXISTS" in out:
        print("  服务器已有 .env，保留不动（避免覆盖生产密钥）")
    else:
        secret = os.environ.get("CITRUS_SECRET_KEY", "")
        chain_key = os.environ.get("CITRUS_CHAIN_PRIVATE_KEY", "")
        if not secret or not chain_key:
            print("  ⚠️  服务器无 .env 且本地未提供密钥，跳过生成")
            print("     需设置 CITRUS_SECRET_KEY / CITRUS_CHAIN_PRIVATE_KEY 后重跑")
        else:
            env = f"""# 生产环境配置（服务器 <your-server-ip>）
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=<your-server-ip>,localhost,127.0.0.1
DJANGO_SECRET_KEY={secret}

# 链与 Django 同机，走回环（RPC 不暴露公网）
FISCO_WEB3_RPC=http://127.0.0.1:8545
FISCO_CHAIN_ID=20200
FISCO_GROUP_ID=group0
FISCO_CONTRACT_ADDRESS=0x9D6aAc145B199bc406Cc4AFb8cE1571ADC619820
FISCO_PRIVATE_KEY={chain_key}
FISCO_GAS_LIMIT=500000
FISCO_ENABLE_CHAIN=true

FRONTEND_VERIFY_BASE=http://<your-server-ip>:8090/#/verify
"""
            with sftp.open(REMOTE_BACKEND + "/.env", "w") as f:
                f.write(env)
            sftp.chmod(REMOTE_BACKEND + "/.env", 0o600)
            print("  已生成初始 .env（权限 600）")
    sftp.close()


# ---------------------------------------------------------------- setup
def do_setup(cli):
    print("=== 创建 venv 并安装依赖（可能耗时几分钟）===")
    run(cli, f"cd {REMOTE_ROOT} && python3 -m venv venv && "
             f"{REMOTE_ROOT}/venv/bin/pip install -q --upgrade pip", timeout=600)

    run(cli, f"{REMOTE_ROOT}/venv/bin/pip install -q "
             f"-i https://mirrors.aliyun.com/pypi/simple/ "
             f"-r {REMOTE_BACKEND}/requirements.txt gunicorn", timeout=1800)

    # ★ tesseract 是**系统包**，pip 装不了 —— 必须单独 apt 安装。
    #   电子版 PDF 走文本层不需要它，但扫描件/照片识别要靠它。
    #   漏装的后果不是报错而是「扫描件永远识别不出来」，很难被发现。
    print("=== 安装 tesseract（扫描件 OCR 依赖，系统包）===")
    run(cli, "command -v tesseract >/dev/null 2>&1 && echo 'tesseract 已安装，跳过' || "
             "(sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq && "
             "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
             "tesseract-ocr tesseract-ocr-chi-sim)", timeout=900)
    run(cli, "tesseract --version 2>&1 | head -1 && tesseract --list-langs 2>&1 | tail -n +2 | tr '\\n' ' '")

    print("=== 依赖清单确认 ===")
    run(cli, f"{REMOTE_ROOT}/venv/bin/pip list 2>/dev/null | grep -Ei "
             f"'django|rest|jwt|cors|qrcode|pillow|web3|gunicorn|pymysql|"
             f"pymupdf|pdfminer|pytesseract'")


# ---------------------------------------------------------------- migrate
def do_migrate(cli):
    print("=== 数据库迁移 + 演示数据 ===")
    run(cli, f"cd {REMOTE_BACKEND} && {REMOTE_ROOT}/venv/bin/python manage.py migrate --noinput")
    run(cli, f"cd {REMOTE_BACKEND} && {REMOTE_ROOT}/venv/bin/python manage.py collectstatic --noinput")
    code, out, err = run(cli, f"cd {REMOTE_BACKEND} && {REMOTE_ROOT}/venv/bin/python manage.py init_data")
    return code


# ---------------------------------------------------------------- service
SYSTEMD = """[Unit]
Description=Citrus Certificate Trace (Django + Gunicorn)
After=network-online.target fisco-node0.service
Wants=network-online.target
After=fisco-node0.service

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/citrus/backend
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/ubuntu/citrus/venv/bin/gunicorn config.wsgi:application \\
    --bind 127.0.0.1:8000 \\
    --workers 3 \\
    --threads 2 \\
    --timeout 120 \\
    --access-logfile /home/ubuntu/citrus/gunicorn-access.log \\
    --error-logfile /home/ubuntu/citrus/gunicorn-error.log
Restart=on-failure
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
"""


def do_service(cli):
    print("=== 写入 systemd 服务 ===")
    sftp = cli.open_sftp()
    with sftp.open("/tmp/citrus.service", "w") as f:
        f.write(SYSTEMD)
    sftp.close()
    run(cli, "sudo mv /tmp/citrus.service /etc/systemd/system/citrus.service && "
             "sudo chown root:root /etc/systemd/system/citrus.service")
    run(cli, "sudo systemctl daemon-reload && sudo systemctl enable citrus")


# ---------------------------------------------------------------- nginx
NGINX = """server {
    # 端口说明（实测 2026-09）：
    #   80 / 443 / 8080  → 未备案会被腾讯云拦截层「代答握手 + 丢包」，
    #                       表现为 TCP 能连但永远收不到数据
    #   8090            → 非标端口，不在拦截名单，安全组放行即可用 ★ 当前推荐
    listen 80 default_server;
    listen [::]:80 default_server;
    listen 8080;
    listen 8090;
    server_name <your-server-ip> _;

    client_max_body_size 20m;

    # ★ 安全响应头（全局）
    #   X-Frame-Options     防点击劫持（iframe 嵌套）
    #   X-Content-Type-Options  防 MIME 嗅探攻击
    #   Referrer-Policy     控制请求跨站时是否携带来源信息
    #   X-XSS-Protection    浏览器 XSS 过滤兜底（现代浏览器多用 CSP，此为兼容垫）
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header X-XSS-Protection "1; mode=block" always;

    # 前端静态站点（Vue3 SPA，hash 路由）
    root /home/ubuntu/citrus/frontend_dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    # ★ index.html 绝不缓存：它是 SPA 的唯一入口，引用的 chunk 名每次构建都变。
    #   一旦被缓存，用户会长期停留在旧版本（配合残留的旧 chunk 就更难察觉）。
    #   注意：nginx 里 location 内的 add_header 会覆盖 server 级的同名继承，
    #   所以这里必须重复安全头，否则 index.html 会丢掉 X-Frame-Options 等。
    location = /index.html {
        add_header Cache-Control "no-store, no-cache, must-revalidate";
        add_header Pragma "no-cache";
        add_header X-Frame-Options "SAMEORIGIN" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Referrer-Policy "strict-origin-when-cross-origin" always;
        add_header X-XSS-Protection "1; mode=block" always;
        expires -1;
    }

    # 后端 API 反代到 Gunicorn
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }

    # ★ Django admin 已下线（config/urls.py 已注释 path("admin/")），
    #   这里不再反代 /admin/，避免留一个死路由暴露面。
    location /static/ {
        alias /home/ubuntu/citrus/backend/staticfiles/;
    }

    # ★ 用户上传文件（检测报告附件等）。
    #   必须用 `^~` 而不是普通前缀 —— 否则下面那条按扩展名匹配的正则 location
    #   优先级更高（nginx 规则：正则 > 普通前缀），会把 .jpg/.png 类附件
    #   交给 `try_files $uri =404`，而它的基准目录是前端 dist，
    #   找不到文件直接 404 → 「上传成功但打不开」。
    #   `^~` 表示命中该前缀后不再尝试正则匹配，从根上消除歧义。
    location ^~ /media/ {
        alias /home/ubuntu/citrus/backend/media/;
        expires 1d;
        add_header Cache-Control "public";
        add_header X-Frame-Options "SAMEORIGIN" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Referrer-Policy "strict-origin-when-cross-origin" always;
        add_header X-XSS-Protection "1; mode=block" always;
    }

    # 静态资源长缓存：文件名含内容哈希，内容变则文件名变，可放心 immutable
    location ^~ /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
        add_header X-Frame-Options "SAMEORIGIN" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Referrer-Policy "strict-origin-when-cross-origin" always;
        add_header X-XSS-Protection "1; mode=block" always;
        try_files $uri =404;
    }
    location ~* \\.(js|css|png|jpg|jpeg|gif|ico|svg|woff2?)$ {
        expires 7d;
        add_header Cache-Control "public, immutable";
        add_header X-Frame-Options "SAMEORIGIN" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Referrer-Policy "strict-origin-when-cross-origin" always;
        add_header X-XSS-Protection "1; mode=block" always;
        try_files $uri =404;
    }

    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml;
    gzip_min_length 1024;
}
"""


def do_nginx(cli):
    print("=== 配置 nginx ===")
    sftp = cli.open_sftp()
    with sftp.open("/tmp/citrus.conf", "w") as f:
        f.write(NGINX)
    sftp.close()
    run(cli, "sudo mv /tmp/citrus.conf /etc/nginx/sites-available/citrus")
    run(cli, "sudo rm -f /etc/nginx/sites-enabled/default")
    run(cli, "sudo ln -sf /etc/nginx/sites-available/citrus /etc/nginx/sites-enabled/citrus")
    code, out, err = run(cli, "sudo nginx -t 2>&1")
    if code == 0:
        run(cli, "sudo systemctl enable --now nginx && sudo systemctl reload nginx")
    return code


# ---------------------------------------------------------------- start
def do_start(cli):
    print("=== 启动服务 ===")
    run(cli, "sudo systemctl restart citrus && sleep 3 && sudo systemctl is-active citrus")
    run(cli, "sudo systemctl restart nginx && sudo systemctl is-active nginx")


# ---------------------------------------------------------------- check
def do_check(cli):
    print("=== 服务状态 ===")
    run(cli, "systemctl is-active citrus nginx fisco-node0 fisco-node1")
    print("=== 端口 ===")
    run(cli, "sudo ss -lntp | grep -E ':80 |:8000 |:8545 '")
    print("=== 本机 API 自测 ===")
    run(cli, "curl -s -o /dev/null -w 'frontend  HTTP %{http_code}\\n' http://127.0.0.1/")
    run(cli, "curl -s -o /dev/null -w 'verifyAPI HTTP %{http_code}\\n' "
             "http://127.0.0.1/api/public/verify/CERT-2026-1002/")
    run(cli, "curl -s http://127.0.0.1/api/public/verify/CERT-2026-1002/ "
             "| head -c 400; echo")
    print("=== 最近日志 ===")
    run(cli, "sudo journalctl -u citrus -n 15 --no-pager 2>&1 | tail -15")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "all"
    cli = conn()
    try:
        if action in ("upload", "all"):
            do_upload(cli)
        if action in ("setup", "all"):
            do_setup(cli)
        if action in ("migrate", "all"):
            do_migrate(cli)
        if action in ("service", "all"):
            do_service(cli)
        if action in ("nginx", "all"):
            do_nginx(cli)
        if action in ("start", "all"):
            do_start(cli)
        if action in ("check", "all"):
            do_check(cli)
    finally:
        cli.close()
    print("\n=== DONE:", action, "===")
