"""
全量重算存证哈希并重新上链：python manage.py rehash_all

用途：
  引入「加盐哈希（Salted Hash）」后，所有存证摘要的算法发生变化，
  旧存证记录（未加盐）与当前重算（加盐）不再一致，验真会误判「已篡改」。
  本命令遍历所有已存证的业务对象，按新算法重新计算哈希并重新上链，
  使链上、库内、当前数据三方一致。

幂等：可重复执行；对未上链（is_anchored=False / 无 tx_hash）的对象也能补齐存证。
安全：不删任何历史记录。★ 采用**追加式版本**（B3）：摘要变化时新增一条
      version+1 / is_current=True 的存证，旧版本归档（is_current=False）保留留痕，
      因此「重存证 / 换盐迁移」与「防篡改」不再互斥 —— 验真按当前版本比对，
      与链上单值槽天然一致。链上摘要会被最新一次覆盖（合约单槽语义）。

★ 安全默认（原 M8 修复）：默认**只统计（dry-run）**，必须显式加 `--apply`
  才真正改写数据。原实现「默认即写」—— 一次误触就会全量重写历史存证
  （配合 _save_record 的覆盖语义，风险更高）。破坏性操作必须显式确认。

用法：
  python manage.py rehash_all             # 默认：只统计，不改数据（dry-run）
  python manage.py rehash_all --apply     # 真正执行重算 + 重新上链
  python manage.py rehash_all --dry-run   # 与默认同义（保留兼容）
"""
from django.core.management.base import BaseCommand

from apps.traceability import services
from apps.traceability.models import (Certificate, EvidenceRecord,
                                      InspectionRecord, ProductBatch, TestReport)


class Command(BaseCommand):
    help = "全量重算存证哈希并重新上链（默认为 dry-run，需 --apply 才写入）"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="只统计，不修改/不上链（默认行为）")
        parser.add_argument("--apply", action="store_true",
                            help="★ 真正执行重算并重新上链（破坏性，需显式指定）")

    def handle(self, *args, **options):
        # ★ 默认 dry-run：只有显式 --apply 才写。--dry-run 优先于 --apply。
        dry_run = not options["apply"]
        if options["dry_run"]:
            dry_run = True

        salt = services._salt() or "(空盐)"
        self.stdout.write(f"存证盐值：{salt}")
        if dry_run:
            self.stdout.write(self.style.WARNING(
                "模式：dry-run（只统计，不改数据）。要真正执行请加 --apply"))
        else:
            self.stdout.write(self.style.ERROR(
                "模式：APPLY（将全量重写存证哈希并重新上链！）"))

        stats = {"certificate": 0, "batch": 0, "report": 0, "inspection": 0}

        # ---- 1. 合格证 ----
        for cert in Certificate.objects.filter(cert_no__isnull=False).exclude(cert_no=""):
            if dry_run:
                stats["certificate"] += 1
                continue
            record = services.anchor_certificate(cert)
            stats["certificate"] += 1

        # ---- 2. 生产批次 ----
        for batch in ProductBatch.objects.filter(batch_no__isnull=False).exclude(batch_no=""):
            if dry_run:
                stats["batch"] += 1
                continue
            services.anchor_batch(batch)
            stats["batch"] += 1

        # ---- 3. 检测报告 ----
        for report in TestReport.objects.filter(report_no__isnull=False).exclude(report_no=""):
            if dry_run:
                stats["report"] += 1
                continue
            services.anchor_report(report)
            stats["report"] += 1

        # ---- 4. 监督检查 ----
        for inspection in InspectionRecord.objects.all():
            if dry_run:
                stats["inspection"] += 1
                continue
            services.anchor_inspection(inspection)
            stats["inspection"] += 1

        mode = "（dry-run，未修改任何数据）" if dry_run else ""
        self.stdout.write(self.style.SUCCESS(
            f"完成{mode}：合格证 {stats['certificate']}、"
            f"批次 {stats['batch']}、报告 {stats['report']}、"
            f"检查 {stats['inspection']}"
        ))

        if not dry_run:
            total = EvidenceRecord.objects.count()
            self.stdout.write(f"当前存证记录总数：{total}")