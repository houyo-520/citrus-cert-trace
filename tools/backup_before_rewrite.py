# -*- coding: utf-8 -*-
"""
git 历史改写前的完整备份。

为什么要备份：接下来要用 `git filter-branch` 清除历史里的明文凭据，
这会**重写全部提交哈希**。虽然本地文件内容不变，但万一操作失误
（比如某次提交丢失、分支指针错乱），没有备份就无法回滚。

备份策略：
  1. 完整复制「源代码」目录，**包含 .git**（历史是这次要改的东西，必须备）
  2. 排除 node_modules（165 MB，`npm i` 可重建，备份它没意义）
  3. 额外单独导出一份凭据清单（这些值即将从代码里移除，得留个记录）
"""
import os
import shutil
import sys

SRC = r"D:\HuaweiMoveData\Users\houyo\Desktop\毕设源\源代码"
DST = r"D:\HuaweiMoveData\Users\houyo\Desktop\毕设源\源代码_备份_20260923"

# 备份时排除：可重建的依赖与缓存
SKIP_DIRS = {"node_modules", "__pycache__", ".venv", "venv", "dist",
             "staticfiles", ".pytest_cache", ".ruff_cache"}

print("=" * 62)
print("git 历史改写前备份")
print("=" * 62)

if os.path.exists(DST):
    print(f"⚠️  备份目录已存在，先删除旧的：{DST}")
    shutil.rmtree(DST)

copied = 0
skipped = 0


def ignore(directory, names):
    """返回要忽略的条目名。"""
    global skipped
    out = []
    for n in names:
        full = os.path.join(directory, n)
        if n in SKIP_DIRS:
            out.append(n)
            skipped += 1
        elif n.endswith((".pyc", ".log")):
            out.append(n)
            skipped += 1
    return out


def copy_tree(src, dst):
    """递归复制，返回文件数。"""
    global copied
    os.makedirs(dst, exist_ok=True)
    n = 0
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)
        if os.path.isdir(s):
            if item in SKIP_DIRS:
                continue
            n += copy_tree(s, d)
        else:
            if item.endswith((".pyc",)):
                continue
            shutil.copy2(s, d)
            n += 1
    return n


copied = copy_tree(SRC, DST)
print(f"\n已复制 {copied} 个文件 → {DST}")

# ---- 校验：.git 必须完整 ----
git_dir = os.path.join(DST, ".git")
has_git = os.path.isdir(git_dir)
git_files = 0
if has_git:
    for r, d, fs in os.walk(git_dir):
        git_files += len(fs)
print(f".git 目录：{'✅ 已备份' if has_git else '❌ 缺失'}  （{git_files} 个文件）")


def dsize(p):
    t = 0
    for r, d, fs in os.walk(p):
        for f in fs:
            try:
                t += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    return t


print(f"备份体积：{dsize(DST) / 1048576:.1f} MB")

if not has_git:
    print("\n❌ 备份失败：.git 没复制成功，无法安全改写历史")
    sys.exit(1)

print("\n✅ 备份完成")
print(f"   位置：{DST}")
print(f"   回滚方式：删掉「源代码」目录，把备份目录改名为「源代码」即可")
