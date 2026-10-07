"""
Merkle 树汇聚存证（区块链硬核亮点）

为什么需要这个模块：
  一张合格证背后关联多份证据（生产批次、检测报告、监督检查结论……），
  现状是「每类证据各自单独上链」，链上多条记录、核验也要逐条查。
  Merkle 树把这些证据的哈希汇聚成**一个根哈希（merkle root）**，
  链上只需存这一个根哈希即可代表「整棵证据树」。

  收益：
  1. 空间效率：N 份证据 -> 1 个根哈希上链；
  2. 防篡改定位：任意一份证据变了，根哈希必变；结合 Merkle 证明
     （proof）还能精确定位到「到底哪一份变了」，而不必重算全部；
  3. 与联盟链天然契合：根哈希正是 PoW/PBFT 链上常见的「把批量数据
     压缩成单一摘要」的经典结构。

设计取舍（增量、不破坏现有验真）：
  - 现有 evidence 链上索引是 `records[bizType][bizNo]` 单值映射，
    本模块**不改动**它，而是新增一个独立的纯函数模块，把「单对象关联的
    若干份证据哈希」算成根哈希；根哈希可由调用方额外上链（新 bizType）。
  - 因此它是「可演示、可答辩、可独立测试」的能力，不侵入现有存证主链路。

哈希拼接约定：
  每层相邻两个节点拼接后做 SHA-256；奇数个节点时，最后一个节点「自复制」
  上提（与 Bitcoin 的 Merkle 树一致）。
"""
import hashlib


def _h(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def merkle_root(leaves: list) -> str:
    """
    计算叶子哈希列表的 Merkle 根。

    参数 leaves：叶子节点的**哈希字符串列表**（hex 或任意 str/bool 皆可，
    内部统一编码后做二次哈希）。
    空列表返回空串；单叶直接返回该叶的二次哈希。
    """
    if not leaves:
        return ""
    # 叶子统一转 bytes
    level = [_h(str(x).encode("utf-8")) for x in leaves]
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])   # 奇数时自复制最后一个
        nxt = []
        for i in range(0, len(level), 2):
            nxt.append(_h(level[i] + level[i + 1]))
        level = nxt
    return level[0].hex()


def build_certificate_merkle_root(cert) -> str:
    """
    把一张合格证的关联证据哈希汇聚成 Merkle 根。

    关联证据（按固定顺序）：
      批次存证哈希 -> 报告存证哈希 -> 合格证自身哈希
      若还关联检查记录，追加每条检查的结论哈希。
    顺序固定，保证同一张证每次计算结果一致。
    """
    from .models import EvidenceRecord

    leaves = []
    # 批次 / 报告 / 证 三者的存证摘要
    for biz_type, obj_id in (
        (EvidenceRecord.BizType.BATCH, cert.batch_id),
        (EvidenceRecord.BizType.REPORT, cert.report_id),
        (EvidenceRecord.BizType.CERTIFICATE, cert.id),
    ):
        # ★ 只取当前版本（B3 追加式版本）：历史版本是归档留痕，不参与证据树汇聚
        rec = EvidenceRecord.objects.filter(
            biz_type=biz_type, biz_id=obj_id, is_current=True).first()
        if rec and rec.data_hash:
            leaves.append(rec.data_hash)

    # 关联检查记录的结论哈希（若有）
    for insp in cert.inspections.filter(is_anchored=True).order_by("id"):
        if insp.record_hash:
            leaves.append(insp.record_hash)

    return merkle_root(leaves)


def merkle_proof(leaves: list, index: int) -> list:
    """
    生成第 index 个叶子的 Merkle 证明（从叶到根路径上的兄弟哈希 + 方向）。

    返回 [{hash, position: 'left'|'right'}...]，用于证明「该叶子确实在树中」。
    """
    if index < 0 or index >= len(leaves):
        raise IndexError("index 越界")
    level = [_h(str(x).encode("utf-8")) for x in leaves]
    proof = []
    idx = index
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        sibling_idx = idx ^ 1          # 0<->1, 2<->3 ...
        if sibling_idx < len(level):
            proof.append({
                "hash": level[sibling_idx].hex(),
                "position": "left" if sibling_idx < idx else "right",
            })
        idx //= 2
        nxt = []
        for i in range(0, len(level), 2):
            nxt.append(_h(level[i] + level[i + 1]))
        level = nxt
    return proof


def verify_merkle_proof(root_hex: str, leaf_hex: str, proof: list) -> bool:
    """
    用 proof 从叶子反算根哈希，与给定的 root 比对，验证叶子确在树中。

    参数：
      root_hex  Merkle 根（hex）
      leaf_hex  叶子节点的哈希（hex）—— 即 merkle_root 里 _h(leaf) 的结果
      proof     由 merkle_proof 生成的兄弟路径
    返回 True/False。
    """
    # 叶子哈希是树的叶子层节点，直接作为起点（不再二次哈希）
    cur = bytes.fromhex(leaf_hex)
    for step in proof:
        sibling = bytes.fromhex(step["hash"])
        if step["position"] == "left":
            cur = _h(sibling + cur)
        else:
            cur = _h(cur + sibling)
    return cur.hex() == root_hex