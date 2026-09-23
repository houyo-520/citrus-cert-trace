# -*- coding: utf-8 -*-
"""
推送前敏感信息扫描。

推送到公开仓之前必须过一遍 —— 凭据一旦推上去，
即使随后删除，也会留在别人的 fork、缓存、搜索引擎快照里。

扫描目标：
  1. 已知的凭据明文（密码 / 密钥 / 私钥）
  2. 常见凭据模式（password= / secret= / token= / api_key= 后跟真实值）
  3. 大体积或不该入库的文件（db.sqlite3 / media / node_modules / dist）
  4. .gitignore 是否覆盖了所有该排除的路径

只读检查，不改任何文件。
"""
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---- 已知的凭据明文 ----
# ★ 不写死在本文件里 —— 否则扫描器自己就成了新的泄露源
#   （它会被提交进仓库，明文跟着进去）。
#   改从 .known_secrets 读取，该文件已被 .gitignore 排除。
SECRETS_FILE = os.path.join(REPO, ".known_secrets")


def load_known():
    """从 .known_secrets 读取待检凭据。文件不存在则返回空（仅靠模式匹配）。"""
    if not os.path.isfile(SECRETS_FILE):
        return {}
    out = {}
    with open(SECRETS_FILE, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            s = line.strip()
            if s and not s.startswith("#"):
                out[f"已知凭据 #{i}"] = s
    return out


KNOWN = load_known()

# ---- 通用凭据模式（排除占位符/环境变量引用）----
PATTERNS = [
    (r"(?i)(password|passwd|pwd)\s*[:=]\s*['\"][^'\"\s]{4,}['\"]", "疑似硬编码密码"),
    (r"(?i)(secret|secret_key)\s*[:=]\s*['\"][^'\"\s]{8,}['\"]", "疑似硬编码密钥"),
    (r"(?i)(private_key|privkey)\s*[:=]\s*['\"]0x?[0-9a-fA-F]{32,}['\"]", "疑似私钥"),
    (r"(?i)(token|api_key|apikey)\s*[:=]\s*['\"][^'\"\s]{12,}['\"]", "疑似 token"),
    (r"0x[0-9a-fA-F]{64}", "疑似以太坊/联盟链私钥（64位十六进制）"),
]

# ---- 不应入库的路径（`.env.example` 是模板，本就该入库，故不列入）----
FORBIDDEN = [
    "backend/db.sqlite3", "backend/media", "frontend/node_modules",
    "frontend/dist", "backend/staticfiles", "backend/.env",
    ".deploy_env", ".known_secrets", "backend/.fisco_account.json",
    "backend/.fisco_contract.json",
]

SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv",
             "dist", "staticfiles", ".pytest_cache"}
# ★ 本扫描器自身含有"已知凭据"字符串（那是检测规则，不是泄露），
#   必须排除，否则每次扫描都会自我命中，掩盖真正的问题。
SKIP_FILES = {"scan_secrets.py"}
SCAN_EXT = {".py", ".js", ".mjs", ".vue", ".json", ".md", ".yaml", ".yml",
            ".conf", ".sh", ".txt", ".env", ".ini", ".cfg", ".html"}

# 允许的例外：演示账号密码（公开信息，答辩要写在文档里）+ 模板占位
ALLOW = [
    "admin123",          # 演示账号统一密码，设计上就公开
    ".env.example",      # 配置模板，值应为空
]


def _allowed(frag: str, rel: str) -> bool:
    if any(a in frag for a in ALLOW):
        return True
    if ".env.example" in rel.replace("\\", "/"):
        return True
    return False

hits = []
warns = []
print("=" * 66)
print("推送前敏感信息扫描")
print("=" * 66)
if not KNOWN:
    print("\n⚠️  未找到 .known_secrets，仅按通用模式匹配（漏检率会升高）")
else:
    print(f"\n已从 .known_secrets 载入 {len(KNOWN)} 个待检凭据")

# ---------- 0. 先确定「哪些文件会被推送」----------
# ★ 这是本扫描器的核心判据：只有 git 会推送的内容才构成泄露风险。
#   本地存在但已被 .gitignore 排除的文件（如 .fisco_account.json）
#   推不上去，属于「提醒」而非「阻塞」—— 混在一起报会掩盖真正的问题。
r = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True)
tracked = [x for x in r.stdout.splitlines() if x.strip()]
r2 = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"],
                    cwd=REPO, capture_output=True, text=True)
untracked = [x for x in r2.stdout.splitlines() if x.strip()]
to_push = set(tracked) | set(untracked)
print(f"\n[0] 会被推送的文件：已追踪 {len(tracked)} + 未追踪待加 {len(untracked)}")

# ---------- 1 & 2. 只扫描会被推送的文件内容 ----------
print("\n[1] 扫描将被推送的文件内容")
scanned = 0
for rel in sorted(to_push):
    p = os.path.join(REPO, rel.replace("/", os.sep))
    if not os.path.isfile(p):
        continue
    if os.path.splitext(os.path.basename(p))[1].lower() not in SCAN_EXT:
        continue
    if os.path.basename(p) in SKIP_FILES:
        continue
    try:
        txt = open(p, encoding="utf-8", errors="ignore").read()
    except OSError:
        continue
    scanned += 1
    for label, val in KNOWN.items():
        if val in txt and not _allowed(val, rel):
            hits.append(("已知凭据", label, rel))
    for pat, label in PATTERNS:
        for m in re.finditer(pat, txt):
            frag = m.group(0)
            if "os.environ" in frag or "getenv" in frag:
                continue
            if re.search(r"(xxx|your|placeholder|示例|<|>|\*)", frag, re.I):
                continue
            if _allowed(frag, rel):
                continue
            hits.append((label, frag[:70], rel))
print(f"  已扫描 {scanned} 个将被推送的文件")

# ---------- 1b. 本地存在但已忽略的敏感文件 → 仅提醒 ----------
print("\n[1b] 本地存在但已被忽略的敏感文件（推不上去，仅提醒）")
for name in (".fisco_account.json", ".fisco_contract.json", ".env", ".deploy_env"):
    found = None
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        if name in files:
            found = os.path.relpath(os.path.join(root, name), REPO)
            break
    if found:
        ri = subprocess.run(["git", "check-ignore", "-q", found], cwd=REPO,
                            capture_output=True)
        if ri.returncode == 0:
            warns.append(f"{found} 存在但已被 .gitignore 排除（不会推送）")
        else:
            hits.append(("敏感文件未被忽略", found, found))

# ---------- 3. 不应入库的路径 ----------
print("\n[2] 检查不应入库的路径")
for f in FORBIDDEN:
    p = os.path.join(REPO, f.replace("/", os.sep))
    exists = os.path.exists(p)
    ri = subprocess.run(["git", "check-ignore", "-q", f], cwd=REPO,
                        capture_output=True)
    ignored = (ri.returncode == 0)
    status = "✅ 已忽略" if ignored else ("⚠️ 存在且未忽略" if exists else "— 不存在")
    print(f"  {f:<28} {status}")
    if exists and not ignored:
        hits.append(("未忽略的敏感路径", f, f))

# ---------- 4. 已追踪文件里有没有敏感物 ----------
print("\n[3] 检查 git 已追踪文件")
bad_tracked = [t for t in tracked
               if any(t == f or t.startswith(f + "/") for f in FORBIDDEN)]
if bad_tracked:
    print("  ⚠️ 以下敏感文件已被追踪：")
    for t in bad_tracked:
        print("     -", t)
    hits.append(("敏感文件被追踪", "", ", ".join(bad_tracked[:5])))
else:
    print("  ✅ 无敏感文件被追踪")
print(f"  已追踪文件总数：{len(tracked)}")

# ---------- 汇总 ----------
print("\n" + "=" * 66)
if warns:
    print("ℹ️  提醒（不影响推送）：")
    for w in warns:
        print("   -", w)
if hits:
    print(f"\n❌ 发现 {len(hits)} 处问题：\n")
    for kind, frag, loc in hits:
        print(f"  [{kind}] {loc}")
        if frag:
            print(f"      {frag}")
    sys.exit(1)
print("\n✅ 未发现敏感信息，可以推送")
print("=" * 66)
