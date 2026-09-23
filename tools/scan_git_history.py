# -*- coding: utf-8 -*-
"""
扫描 git **历史** 中的明文凭据（工作区已清理，但历史还没清）。

为什么必须扫历史：git 提交是永久的。即使最新版本里删掉了密码，
只要它曾出现在任何一次提交里，`git log -p` 就能翻出来，
推送到公开仓后任何人都能拿到。

做法：遍历全部提交，读取每个版本的 deploy.py，检查是否含已知凭据。
"""
import re
import subprocess
import sys

REPO = r"D:\HuaweiMoveData\Users\houyo\Desktop\毕设源\源代码"

KNOWN = {
    "服务器 SSH 密码": "__SET_VIA_ENV__",
    "Django SECRET_KEY": "__SET_VIA_ENV__",
    "链上账户私钥": "__SET_VIA_ENV__",
}


def git(*args, **kw):
    return subprocess.run(["git"] + list(args), cwd=REPO,
                          capture_output=True, text=True, **kw)


print("=" * 66)
print("扫描 git 历史中的明文凭据")
print("=" * 66)

# 全部提交
commits = [c for c in git("rev-list", "--all").stdout.splitlines() if c.strip()]
print(f"\n共 {len(commits)} 个提交待检查\n")

# 所有被追踪过的文件（历史上出现过的都算）
all_files = set()
r = git("log", "--all", "--pretty=format:", "--name-only")
for line in r.stdout.splitlines():
    if line.strip():
        all_files.add(line.strip())
print(f"历史上出现过的文件：{len(all_files)} 个")

# 重点检查这些文件（历史上含过凭据的）
SUSPECT = [f for f in all_files
           if f.endswith((".py", ".json", ".md", ".env", ".example", ".yml",
                          ".yaml", ".txt", ".sh"))
           and "node_modules" not in f]
print(f"其中需检查文本内容的：{len(SUSPECT)} 个\n")

dirty = {}
checked = 0
for c in commits:
    short = c[:8]
    for f in SUSPECT:
        # 该提交里这个文件的内容
        blob = git("show", f"{c}:{f}")
        if blob.returncode != 0:
            continue
        checked += 1
        txt = blob.stdout
        for label, val in KNOWN.items():
            if val in txt:
                dirty.setdefault(short, set()).add((label, f))

print(f"已检查 {checked} 个 (提交, 文件) 组合\n")

if dirty:
    print("❌ 以下提交的历史版本中含明文凭据：\n")
    for short in sorted(dirty):
        items = sorted(dirty[short])
        desc = ", ".join(f"{label}@{f}" for label, f in items)
        print(f"  {short}  {desc}")
    print(f"\n共 {len(dirty)} 个提交需要清理")
    print("\n→ 需要用 git filter-branch 重写这些提交，把它们从历史中彻底移除")
    sys.exit(1)

print("✅ git 历史中未发现明文凭据")
print("=" * 66)
