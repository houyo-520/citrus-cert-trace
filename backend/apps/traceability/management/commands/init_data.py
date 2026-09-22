"""
初始化演示数据：python manage.py init_data
- 创建四类角色账号
- 创建生产主体、批次、检测报告、合格证（自动存证）
- 创建产销供需信息
"""
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from apps.traceability.models import (Producer, ProductBatch, TestReport,
                                      Certificate, SupplyDemand)
from apps.traceability import services

User = get_user_model()

USERS = [
    ("admin", "admin123", "admin", "系统管理员", "平台运营中心"),
    ("regulator", "admin123", "regulator", "监管人员", "县农业农村局"),
    ("producer", "admin123", "producer", "种植主体", "橙心柑橘种植合作社"),
    ("buyer", "admin123", "buyer", "收购商", "鲜果汇供应链"),
]


class Command(BaseCommand):
    help = "初始化演示数据"

    def handle(self, *args, **options):
        self._create_users()
        self._create_business_data()
        self.stdout.write(self.style.SUCCESS("演示数据初始化完成！"))
        self.stdout.write("默认账号：admin / regulator / producer / buyer，密码均为 admin123")

    def _create_users(self):
        for username, password, role, _, org in USERS:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"role": role, "org_name": org,
                          "first_name": org, "is_staff": role == "admin",
                          "is_superuser": role == "admin"},
            )
            if created:
                user.set_password(password)
                user.save()
                self.stdout.write(f"  创建用户 {username} ({role})")

    def _create_business_data(self):
        if Certificate.objects.exists():
            self.stdout.write("  业务数据已存在，跳过")
            return

        producer_user = User.objects.filter(username="producer").first()
        regulator_user = User.objects.filter(username="regulator").first()

        # 生产主体
        p1 = Producer.objects.create(
            name="橙心柑橘种植合作社", credit_code="91510100MA6XXXXX1A",
            contact="王建国", phone="13800001111", region="四川省眉山市东坡区",
            address="东坡区万亩柑橘产业园", audit_status=Producer.AuditStatus.APPROVED,
            owner=producer_user,
        )
        p2 = Producer.objects.create(
            name="甜蜜蜜家庭农场", credit_code="91510100MA6XXXXX2B",
            contact="李秀兰", phone="13800002222", region="四川省眉山市丹棱县",
            address="丹棱县不知火柑橘基地", audit_status=Producer.AuditStatus.PENDING,
            owner=producer_user,
        )

        # 生产批次
        b1 = ProductBatch.objects.create(
            batch_no="BATCH-2026-1001", producer=p1, product_name="柑橘",
            variety="爱媛38号", origin="四川省眉山市东坡区",
            harvest_date=date.today() - timedelta(days=5), weight_kg=5000,
            plant_area_mu=120, farming_record="3月施有机肥，5月生物防治，未使用高毒农药",
        )
        b2 = ProductBatch.objects.create(
            batch_no="BATCH-2026-1002", producer=p1, product_name="柑橘",
            variety="春见耙耙柑", origin="四川省眉山市东坡区",
            harvest_date=date.today() - timedelta(days=2), weight_kg=8000,
            plant_area_mu=200, farming_record="标准化果园管理，绿色防控",
        )
        services.anchor_batch(b1)
        services.anchor_batch(b2)

        # 检测报告
        r1 = TestReport.objects.create(
            report_no="RPT-2026-1001", batch=b1, test_org="眉山市农产品质量检测中心",
            test_date=date.today() - timedelta(days=3),
            conclusion=TestReport.Conclusion.QUALIFIED,
            pesticide_residue="未检出（符合GB 2763）", heavy_metal="符合标准",
        )
        r2 = TestReport.objects.create(
            report_no="RPT-2026-1002", batch=b2, test_org="眉山市农产品质量检测中心",
            test_date=date.today() - timedelta(days=1),
            conclusion=TestReport.Conclusion.QUALIFIED,
            pesticide_residue="未检出（符合GB 2763）", heavy_metal="符合标准",
        )
        services.anchor_report(r1)
        services.anchor_report(r2)

        # 合格证（自动存证）
        c1 = Certificate.objects.create(
            cert_no="CERT-2026-1001", batch=b1, report=r1,
            producer_name=p1.name, product_name="柑橘", quantity_kg=5000,
            issue_date=date.today() - timedelta(days=2),
            valid_until=date.today() + timedelta(days=28),
            status=Certificate.Status.ISSUED, issuer=regulator_user,
        )
        services.anchor_certificate(c1)
        c2 = Certificate.objects.create(
            cert_no="CERT-2026-1002", batch=b2, report=r2,
            producer_name=p1.name, product_name="柑橘", quantity_kg=8000,
            issue_date=date.today() - timedelta(days=1),
            valid_until=date.today() + timedelta(days=29),
            status=Certificate.Status.ISSUED, issuer=regulator_user,
        )
        services.anchor_certificate(c2)

        # 产销供需（差异化亮点）
        SupplyDemand.objects.create(
            kind=SupplyDemand.Kind.SUPPLY, title="供应爱媛38号柑橘5000kg（附合格证）",
            product_name="柑橘", variety="爱媛38号", quantity_kg=5000, price=6.5,
            region="四川省眉山市东坡区", contact="王建国", phone="13800001111",
            certificate=c1, batch=b1, publisher=producer_user,
            description="已通过质量检测，持承诺达标合格证，可扫码验真。支持批量采购。",
        )
        SupplyDemand.objects.create(
            kind=SupplyDemand.Kind.SUPPLY, title="供应春见耙耙柑8000kg（附合格证）",
            product_name="柑橘", variety="春见耙耙柑", quantity_kg=8000, price=5.8,
            region="四川省眉山市东坡区", contact="王建国", phone="13800001111",
            certificate=c2, batch=b2, publisher=producer_user,
            description="果径75mm以上，糖度12度以上，可溯源。",
        )
        SupplyDemand.objects.create(
            kind=SupplyDemand.Kind.DEMAND, title="求购柑橘10000kg（需可追溯）",
            product_name="柑橘", variety="不限", quantity_kg=10000, price=6.0,
            region="四川省成都市", contact="张经理", phone="13900003333",
            description="长期收购，优先选择持合格证、可扫码追溯的货源。",
        )
        self.stdout.write("  创建生产主体2个、批次2个、报告2份、合格证2张、供需3条")