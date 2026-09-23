#!/bin/bash
# 用 git filter-branch 把明文凭据从全部历史提交中替换为占位符。
#
# 为什么用 filter-branch 而不是 filter-repo：
#   本机 git 未安装 filter-repo（需单独下载 Python 脚本），
#   filter-branch 是 git 自带命令，够用且无需额外依赖。
#
# ⚠️ 这会重写全部提交哈希。
#    已做完整备份：毕设源/源代码_备份_20260923/
#
# ★ 本脚本**不含凭据明文** —— 否则它自己就成了泄露源（会被提交进仓库）。
#   待清除的值从 .known_secrets 读取（该文件已被 .gitignore 排除）。
#   故本脚本本身可以安全入库，日后重跑也没问题。
#
# 做法：tree-filter 对每个提交「检出 → 执行命令 → 重新提交」，
#   因此能把历史上任意版本的明文都换掉，而不只是最新版。

set -e
cd "$(dirname "$0")/.."

SECRETS_FILE=".known_secrets"
if [ ! -f "$SECRETS_FILE" ]; then
  echo "❌ 找不到 $SECRETS_FILE，无法确定要清除哪些值"
  echo "   该文件每行一个待清除的字符串（# 开头为注释）"
  exit 1
fi

echo "============================================================"
echo "重写 git 历史：清除代码中的明文凭据"
echo "============================================================"
echo ""
echo "改写前的提交数：$(git rev-list --count HEAD)"
echo "待清除的值数量：$(grep -c -v '^#' "$SECRETS_FILE" | tr -d ' ')"
echo ""

# 生成 sed 表达式（跳过注释行与空行）
SED_ARGS=()
while IFS= read -r line; do
  case "$line" in
    ''|'#'*) continue ;;
  esac
  # 转义 sed 特殊字符
  esc=$(printf '%s' "$line" | sed -e 's/[\/&]/\\&/g')
  SED_ARGS+=(-e "s/${esc}/__SET_VIA_ENV__/g")
done < "$SECRETS_FILE"

if [ ${#SED_ARGS[@]} -eq 0 ]; then
  echo "❌ $SECRETS_FILE 中没有有效值"
  exit 1
fi

# ★ FILTER_BRANCH_SQUELCH_WARNING：git 2.22+ 会对 filter-branch 打印
#   「有风险，建议用 filter-repo」的警告，这里已知风险且已备份，静音它。
export FILTER_BRANCH_SQUELCH_WARNING=1

echo "开始重写（每个提交都会检出并重新提交，需要一点时间）..."
echo ""

git filter-branch --force --tree-filter "
  if [ -f deploy.py ]; then
    sed -i $(printf '%s ' "${SED_ARGS[@]}") deploy.py
  fi
  # 私钥文件若曾入库，一并移除
  rm -f backend/.fisco_account.json backend/.fisco_contract.json 2>/dev/null
  true
" --tag-name-filter cat -- --all

echo ""
echo "清理旧引用与 reflog..."

# 删除 filter-branch 留下的备份引用（否则旧提交仍可访问）
rm -rf .git/refs/original/
git reflog expire --expire=now --all
git gc --prune=now --aggressive --quiet

echo ""
echo "改写后的提交数：$(git rev-list --count HEAD)"
echo ""
echo "✅ 历史重写完成。请运行 tools/scan_git_history.py 复核。"
