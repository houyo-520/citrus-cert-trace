"""
全量重算存证哈希并重新上链：python manage.py rehash_all

用途：
  引入「加盐哈希（Salted Hash）」后，所有存证摘要的算法发生变化，
  旧存证记录（未加盐）与当前重算（加盐）不再一致，验真会误判「已篡改」。
  本命令遍历所有已存证的业务对象，按新算法重新计算哈希并重新上链，
  使链上、库内、当前数据三方一致。

幂等：可重复执行；对未上链（is_anchored=False / 无 tx_hash）的对象也能补齐存证。
安全：不删任何历史记录，只覆盖 data_hash / payload / tx_hash / block_height。

用法：
  python manage.py rehash_all          # 全量重算（默认）
  python manage.py rehash_all --dry-run  # 只统计不动数据
"""
from django.core.management.base import BaseCommand

from apps.traceability import services
from apps.traceability.models import (Certificate, EvidenceRecord,
                                      InspectionRecord, ProductBatch, TestReport)


class Command(BaseCommand):
    help = "全量重算存证哈希并重新上链（用于加盐哈希迁移）"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="只统计，不修改/不上链")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        salt = services._salt() or "(空盐)"
        self.stdout.write(f"存证盐值：{salt}")

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