"""
字符串相似度算法（OCR 模糊匹配）

为什么需要这个模块：
  OCR 识别检测报告时，主体名 / 品种可能和库里已备案的标准名称不完全一致，
  典型误差：少字（橙心合作社 vs 橙心柑橘合作社）、错字（柑桔 vs 柑橘）、
  同义（耙耙柑 vs 春见）。若要求「完全相等才匹配」，OCR 结果几乎无法自动
  关联到已备案实体，用户只能重新手敲。

  本模块用 Levenshtein 编辑距离计算「识别名」与「库内候选名」的相似度，
  达到阈值即给出「可能匹配到已备案主体 X（相似度 85%）」的提示，
  让用户一键选用标准名称，而不是手敲错字。

算法：Levenshtein 编辑距离（动态规划）
  编辑距离 = 把一个字符串变成另一个所需的最少单字符编辑次数
              （插入 / 删除 / 替换，各计 1 次）。
  相似度   = 1 - 编辑距离 / max(len(a), len(b))
  - 完全相等 -> 编辑距离 0 -> 相似度 100%
  - 完全无关 -> 编辑距离接近 max -> 相似度趋近 0

  阈值为 0.6：低于则视为不同实体，避免「柑」和「苹果」这种短串误匹配。

  优化：动态规划用滚动数组（只保留上一行），空间 O(min(m,n))。
"""
from typing import List, Tuple


def levenshtein(a: str, b: str) -> int:
    """
    两个字符串的编辑距离（滚动数组版动态规划）。

    时间 O(m*n)，空间 O(min(m,n))。
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    # 让 a 是较短串，降低空间占用
    if len(a) > len(b):
        a, b = b, a

    m, n = len(a), len(b)
    prev = list(range(n + 1))          # prev[j] = 编辑距离(空 -> b[:j])
    for i in range(1, m + 1):
        cur = [i] + [0] * n            # cur[0] = 编辑距离(a[:i] -> 空)
        for j in range(1, n + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(
                prev[j] + 1,           # 删除 a[i-1]
                cur[j - 1] + 1,        # 插入 b[j-1]
                prev[j - 1] + cost,    # 替换
            )
        prev = cur

    return prev[n]


def similarity(a: str, b: str) -> float:
    """
    归一化相似度，0.0 ~ 1.0。

    分母用 max(len)，而非 min 或平均 —— 这样「子串」情况（如识别出残缺名）
    也能给出合理分值：len(a)=2, len(b)=4, 全命中则 1 - 2/4 = 0.5。
    """
    a = (a or "").strip()
    b = (b or "").strip()
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    dist = levenshtein(a, b)
    return 1.0 - dist / max(len(a), len(b))


def best_match(text: str, candidates: List[str], threshold: float = 0.6) -> dict:
    """
    在候选集中找与 text 最相似的一项。

    参数：
      text       识别出的字符串
      candidates 库内候选名（已备案主体名 / 品种名等）
      threshold  相似度阈值，达标才认为「可能匹配」

    返回：
      {"matched": bool, "best": str, "similarity": float}
      - matched=False 时 best 为 ""，similarity 为 0
    """
    text = (text or "").strip()
    best, best_sim = "", 0.0
    for c in candidates:
        s = similarity(text, c)
        if s > best_sim:
            best_sim, best = s, c
    if not text or best_sim < threshold:
        return {"matched": False, "best": "", "similarity": round(best_sim, 4)}
    return {"matched": True, "best": best, "similarity": round(best_sim, 4)}


def suggest_producer(text: str, threshold: float = 0.6) -> dict:
    """
    对库里已备案主体做模糊匹配（供 OCR 回填建议调用）。
    返回 best_match 结构，额外带 producer_id。
    """
    from .models import Producer

    text = (text or "").strip()
    producers = list(Producer.objects.values_list("id", "name"))
    candidates = [name for _, name in producers]
    m = best_match(text, candidates, threshold=threshold)
    if m["matched"]:
        pid = next(pid for pid, name in producers if name == m["best"])
        m["producer_id"] = pid
    else:
        m["producer_id"] = None
    return m


# ---------------------------------------------------------------
# 便捷比较（供测试 / 演示直接调用）
# ---------------------------------------------------------------
def compare(a: str, b: str) -> Tuple[int, float]:
    """返回 (编辑距离, 相似度)。"""
    d = levenshtein(a, b)
    return d, round(1.0 - d / max(len(a), len(b)), 4) if max(len(a), len(b)) else (0, 1.0)