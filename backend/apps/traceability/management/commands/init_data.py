"""
初始化演示数据：python manage.py init_data
- 创建四类角色账号（含第二个种植主体账号，用于演示数据隔离）
- 创建生产主体、批次、检测报告、合格证（自动存证）
- 创建产销供需信息 + 产销意向单
- 产生操作日志（审计演示）
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.traceability import services
from apps.traceability.models import (Certificate, MatchIntent, OperationLog,
                                      Producer, ProductBatch, SupplyDemand,
                                      TestReport)

User = get_user_model()

USERS = [
    ("admin", "admin123", "admin", "系统管理员", "平台运营中心"),
    ("regulator", "admin123", "regulator", "监管人员", "县农业农村局"),
    ("producer", "admin123", "producer", "种植主体", "橙心柑橘种植合作社"),
    ("producer2", "admin123", "producer", "种植主体", "甜蜜蜜家庭农场"),
    ("buyer", "admin123", "buyer", "收购商", "鲜果汇供应链"),
]


class Command(BaseCommand):
    help = "初始化演示数据"

    def handle(self, *args, **options):
        self._create_users()
        if Certificate.objects.exists():
            # 已有业务数据：不清库，只做幂等补全（适配老数据升级到新功能）
            self.stdout.write("  业务数据已存在，执行增量补全（不删除原数据）")
            self._top_up()
        else:
            self._create_business_data()
        self.stdout.write(self.style.SUCCESS("演示数据初始化完成！"))
        self.stdout.write("默认账号：admin / regulator / producer / producer2 / buyer，"
                          "密码均为 admin123")

    def _top_up(self):
        """
        幂等补全：让已有数据库也能演示「角色隔离 / 审计日志 / 产销匹配」。

        老数据的问题是两个主体都挂在同一个 producer 账号下，
        角色隔离演示不出效果 —— 这里把第二个主体划给 producer2。
        """
        producer_user = User.objects.filter(username="producer").first()
        producer2_user = User.objects.filter(username="producer2").first()
        regulator_user = User.objects.filter(username="regulator").first()
        buyer_user = User.objects.filter(username="buyer").first()

        # 1) 第二个主体改挂 producer2，使数据隔离可演示
        p2 = Producer.objects.filter(name="甜蜜蜜家庭农场").first()
        if p2 and producer2_user and p2.owner_id != producer2_user.id:
            p2.owner = producer2_user
            p2.save(update_fields=["owner", "updated_at"])
            self.stdout.write("  已将「甜蜜蜜家庭农场」划归 producer2 账号")

        # 2) 给 p2 补一个批次（若没有）
        b3 = None
        if p2:
            b3 = ProductBatch.objects.filter(producer=p2).first()
            if not b3:
                b3 = ProductBatch.objects.create(
                    batch_no="BATCH-2026-2001", producer=p2, product_name="柑橘",
                    variety="不知火丑橘", origin="四川省眉山市丹棱县",
                    harvest_date=date.today() - timedelta(days=1), weight_kg=3000,
                    plant_area_mu=80, farming_record="丹棱不知火核心产区，生态种植",
                )
                services.anchor_batch(b3)
                self.stdout.write("  已补充批次 BATCH-2026-2001")

        # 3) 补产销意向单（若没有）
        if not MatchIntent.objects.exists():
            sd = SupplyDemand.objects.filter(
                kind=SupplyDemand.Kind.SUPPLY).order_by("-id").first()
            if sd:
                from apps.traceability import matching
                dm = SupplyDemand.objects.filter(
                    kind=SupplyDemand.Kind.DEMAND).first()
                score, detail = 0, ""
                if dm:
                    r = matching.score_match(sd, dm)
                    score, detail = r["score"], r["detail"]
                MatchIntent.objects.create(
                    supply=sd, initiator=buyer_user,
                    quantity_kg=sd.quantity_kg, price=sd.price,
                    match_score=score, score_detail=detail,
                    message="希望长期合作，可先小批量试单")
                self.stdout.write(f"  已补充产销意向单（匹配度 {score}）")

        # 4) 补操作日志（若没有）
        if not OperationLog.objects.exists():
            rows = []
            for c in Certificate.objects.all()[:2]:
                rows.append(OperationLog(
                    operator=regulator_user, operator_name="regulator",
                    operator_role="regulator", action="issue",
                    target_type="合格证", target_no=c.cert_no,
                    detail=f"签发合格证 {c.cert_no}", ip="127.0.0.1"))
            for b in ProductBatch.objects.all()[:1]:
                rows.append(OperationLog(
                    operator=producer_user, operator_name="producer",
                    operator_role="producer", action="anchor",
                    target_type="生产批次", target_no=b.batch_no,
                    detail=f"批次存证 {b.batch_no}", ip="127.0.0.1"))
            if rows:
                OperationLog.objects.bulk_create(rows)
                self.stdout.write(f"  已补充 {len(rows)} 条操作日志")

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
        producer_user = User.objects.filter(username="producer").first()
        producer2_user = User.objects.filter(username="producer2").first()
        regulator_user = User.objects.filter(username="regulator").first()
        buyer_user = User.objects.filter(username="buyer").first()

        # 生产主体 —— ★ 分属两个不同账号，用于演示「数据级权限隔离」
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
            owner=producer2_user,
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
        # ★ 第二个主体的批次（用于演示数据隔离：producer2 登录只看得到这条）
        b3 = ProductBatch.objects.create(
            batch_no="BATCH-2026-2001", producer=p2, product_name="柑橘",
            variety="不知火丑橘", origin="四川省眉山市丹棱县",
            harvest_date=date.today() - timedelta(days=1), weight_kg=3000,
            plant_area_mu=80, farming_record="丹棱不知火核心产区，生态种植",
        )
        services.anchor_batch(b1)
        services.anchor_batch(b2)
        services.anchor_batch(b3)

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
        sd1 = SupplyDemand.objects.create(
            kind=SupplyDemand.Kind.SUPPLY, title="供应爱媛38号柑橘5000kg（附合格证）",
            product_name="柑橘", variety="爱媛38号", quantity_kg=5000, price=6.5,
            region="四川省眉山市东坡区", contact="王建国", phone="13800001111",
            certificate=c1, batch=b1, publisher=producer_user,
            description="已通过质量检测，持承诺达标合格证，可扫码验真。支持批量采购。",
        )
        sd2 = SupplyDemand.objects.create(
            kind=SupplyDemand.Kind.SUPPLY, title="供应春见耙耙柑8000kg（附合格证）",
            product_name="柑橘", variety="春见耙耙柑", quantity_kg=8000, price=5.8,
            region="四川省眉山市东坡区", contact="王建国", phone="13800001111",
            certificate=c2, batch=b2, publisher=producer_user,
            description="果径75mm以上，糖度12度以上，可溯源。",
        )
        sd3 = SupplyDemand.objects.create(
            kind=SupplyDemand.Kind.DEMAND, title="求购柑橘10000kg（需可追溯）",
            product_name="柑橘", variety="不限", quantity_kg=10000, price=6.0,
            region="四川省成都市", contact="张经理", phone="13900003333",
            publisher=buyer_user,
            description="长期收购，优先选择持合格证、可扫码追溯的货源。",
        )
        SupplyDemand.objects.create(
            kind=SupplyDemand.Kind.SUPPLY, title="供应不知火丑橘3000kg",
            product_name="柑橘", variety="不知火丑橘", quantity_kg=3000, price=7.2,
            region="四川省眉山市丹棱县", contact="李秀兰", phone="13800002222",
            batch=b3, publisher=producer2_user,
            description="丹棱不知火，脆甜化渣，暂无合格证（待审核）。",
        )

        # ★ 产销意向单（演示对接留痕与匹配度评分）
        from apps.traceability import matching
        r = matching.score_match(sd2, sd3)
        MatchIntent.objects.create(
            supply=sd2, initiator=buyer_user,
            quantity_kg=8000, price=5.8, match_score=r["score"],
            score_detail=r["detail"], message="希望长期合作，可先小批量试单",
        )

        # ★ 操作日志（审计演示：把初始化动作也留痕）
        OperationLog.objects.bulk_create([
            OperationLog(operator=regulator_user, operator_name="regulator",
                         operator_role="regulator", action="issue",
                         target_type="合格证", target_no="CERT-2026-1001",
                         detail="签发合格证 CERT-2026-1001", ip="127.0.0.1"),
            OperationLog(operator=regulator_user, operator_name="regulator",
                         operator_role="regulator", action="issue",
                         target_type="合格证", target_no="CERT-2026-1002",
                         detail="签发合格证 CERT-2026-1002", ip="127.0.0.1"),
            OperationLog(operator=producer_user, operator_name="producer",
                         operator_role="producer", action="anchor",
                         target_type="生产批次", target_no="BATCH-2026-1001",
                         detail="批次存证 BATCH-2026-1001", ip="127.0.0.1"),
            OperationLog(operator=buyer_user, operator_name="buyer",
                         operator_role="buyer", action="match",
                         target_type="产销意向单", target_no="供应春见耙耙柑8000kg（附合格证）",
                         detail=f"发起对接意向（匹配度 {r['score']}）", ip="127.0.0.1"),
        ])
        self.stdout.write("  创建生产主体2个、批次3个、报告2份、合格证2张、供需4条、意向1条、日志4条")