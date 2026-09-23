# -*- coding: utf-8 -*-
"""
链上隐患 1 修复：把历史监督检查记录的链上 biz_no 从 cert_no 迁移为 INS-<id>

背景
----
合约是 `records[bizType][bizNo]` 单值映射，同一 bizNo 后写覆盖先写。
早期 `anchor_inspection` 用 `cert.cert_no` 当链上 biz_no，导致同一张合格证上的
多次检查只有最后一次能在链上核验，先前记录被**永久覆盖**。代码已修（改用
`INS-<id>`），但**存量数据仍挂在旧的 cert_no 键上**，必须重新 anchor 一次，
把每条检查记录写到自己的 INS-<id> 键下。

本脚本做三件事（幂等、可重复执行）：
  1. 打印当前本地检查记录与链上 biz_no 的映射关系（修复前快照）
  2. 对每条检查记录重新 anchor（写 INS-<id> 键）
  3. 逐条从链上读回核验 → 断言全部 True

⚠️ 只动 INSPECTION 类型，不碰 certificate / batch / report。
"""
import django
import os
import sys

sys.path.insert(0, "/home/ubuntu/citrus/backend")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.traceability import chain, services          # noqa: E402
from apps.traceability.models import EvidenceRecord, InspectionRecord  # noqa: E402

BT = EvidenceRecord.BizType.INSPECTION

print("=" * 66)
print("链上隐患 1 修复：inspection biz_no 迁移为 INS-<id>")
print("=" * 66)

records = list(InspectionRecord.objects.select_related("certificate").all())
print(f"\n共 {len(records)} 条监督检查记录\n")

print("--- 修复前：本地记录 -> 链上 biz_no ---")
for r in records:
    ev = EvidenceRecord.objects.filter(biz_type=BT, biz_id=r.id).first()
    old_no = ev.biz_no if ev else "(无存证记录)"
    print(f"  id={r.id:<3} cert={r.certificate.cert_no or '-':<18} "
          f"链上biz_no={old_no:<18} 应改为={services.inspection_biz_no(r)}")

print("\n--- 重新 anchor（写各自的 INS-<id> 键）---")
for r in records:
    ev = services.anchor_inspection(r)
    print(f"  id={r.id:<3} biz_no={ev.biz_no:<12} block={ev.block_height:<6} "
          f"tx={ev.tx_hash[:20]}…  status={ev.chain_status}")

print("\n--- 修复后：逐条从链上读回核验 ---")
# ⚠️ chain.verify_on_chain 返回键是 matched（不是 verified），
#    首次跑本脚本时误读 verified 导致全部误判为失败 —— 断言写错别先怀疑代码。
all_ok = True
for r in records:
    ev = EvidenceRecord.objects.filter(biz_type=BT, biz_id=r.id).first()
    res = chain.verify_on_chain(BT, ev.biz_no, ev.data_hash)
    ok = bool(res.get("ok")) and bool(res.get("matched")) and bool(res.get("exists"))
    all_ok = all_ok and ok
    print(f"  id={r.id:<3} biz_no={ev.biz_no:<12} "
          f"exists={res.get('exists')!s:<5} matched={res.get('matched')!s:<5} "
          f"{'✅' if ok else '❌'}  {res.get('message', '')}")

print("\n--- 同证多检查场景（原先互相覆盖的地方）---")
same_cert = {}
for r in records:
    same_cert.setdefault(r.certificate.cert_no, []).append(r.id)
covered = False
for cert_no, ids in same_cert.items():
    if len(ids) > 1:
        covered = True
        # ★ 修复前的失败判据：这几条原先共用一个 cert_no 键，只能活下来一条
        print(f"  {cert_no}: 检查 {ids} —— 原先只保留最后一条，现已各自独立")
if not covered:
    print("  （本次数据中没有同证多检查的情形）")
check_note = ("同证多检查的链上记录全部独立可核验" if covered
              else "全部检查记录链上可核验")
print(f"  判定：{check_note}")

print("\n" + "=" * 66)
print("全部链上核验通过 ✅" if all_ok else "存在核验失败 ❌")
print("=" * 66)
sys.exit(0 if all_ok else 1)