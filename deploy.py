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
"""
import sys, os, posixpath, stat, json, time
import paramiko

HOST = "<your-server-ip>"
USER = "ubuntu"
PWD = "__SET_VIA_ENV__"

REMOTE_ROOT = "/home/ubuntu/citrus"
REMOTE_BACKEND = REMOTE_ROOT + "/backend"
REMOTE_FRONT = REMOTE_ROOT + "/frontend_dist"

# 本地源码根 = 本脚本所在目录（源代码/），便于整体移动
LOCAL = os.path.dirname(os.path.abspath(__file__))

SECRET_KEY = "__SET_VIA_ENV__"


def conn():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=22, username=USER, password=PWD, timeout=25)
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
    env = f"""# 生产环境配置（服务器 <your-server-ip>）
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=<your-server-ip>,localhost,127.0.0.1
DJANGO_SECRET_KEY={SECRET_KEY}

# 链与 Django 同机，走回环（RPC 不暴露公网）
FISCO_WEB3_RPC=http://127.0.0.1:8545
FISCO_CHAIN_ID=20200
FISCO_GROUP_ID=group0
FISCO_CONTRACT_ADDRESS=0x9D6aAc145B199bc406Cc4AFb8cE1571ADC619820
FISCO_PRIVATE_KEY=__SET_VIA_ENV__
FISCO_GAS_LIMIT=500000
FISCO_ENABLE_CHAIN=true

FRONTEND_VERIFY_BASE=http://<your-server-ip>:8090/#/verify
"""
    with sftp.open(REMOTE_BACKEND + "/.env", "w") as f:
        f.write(env)
    sftp.chmod(REMOTE_BACKEND + "/.env", 0o600)
    print("  已写入 .env（权限 600）")
    sftp.close()


# ---------------------------------------------------------------- setup
def do_setup(cli):
    print("=== 创建 venv 并安装依赖（可能耗时几分钟）===")
    run(cli, f"cd {REMOTE_ROOT} && python3 -m venv venv && "
             f"{REMOTE_ROOT}/venv/bin/pip install -q --upgrade pip", timeout=600)

    run(cli, f"{REMOTE_ROOT}/venv/bin/pip install -q "
             f"-i https://mirrors.aliyun.com/pypi/simple/ "
             f"-r {REMOTE_BACKEND}/requirements.txt gunicorn", timeout=1800)

    print("=== 依赖清单确认 ===")
    run(cli, f"{REMOTE_ROOT}/venv/bin/pip list 2>/dev/null | grep -Ei "
             f"'django|rest|jwt|cors|qrcode|pillow|web3|gunicorn|pymysql'")


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

    # 前端静态站点（Vue3 SPA，hash 路由）
    root /home/ubuntu/citrus/frontend_dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    # ★ index.html 绝不缓存：它是 SPA 的唯一入口，引用的 chunk 名每次构建都变。
    #   一旦被缓存，用户会长期停留在旧版本（配合残留的旧 chunk 就更难察觉）。
    location = /index.html {
        add_header Cache-Control "no-store, no-cache, must-revalidate";
        add_header Pragma "no-cache";
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

    # Django admin 与静态资源
    location /admin/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
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
    }

    # 静态资源长缓存：文件名含内容哈希，内容变则文件名变，可放心 immutable
    location ^~ /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
        try_files $uri =404;
    }
    location ~* \\.(js|css|png|jpg|jpeg|gif|ico|svg|woff2?)$ {
        expires 7d;
        add_header Cache-Control "public, immutable";
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
