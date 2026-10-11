"""
初始化演示数据：python manage.py init_data
- 创建四类角色账号（含第二个种植主体账号，用于演示数据隔离）
- 创建生产主体、批次、检测报告、合格证（自动存证）
- 创建产销供需信息 + 产销意向单
- 创建监管现场监督检查记录（执法闭环）
- 产生操作日志（审计演示）

幂等：已有业务数据时不删库，只做增量补全（_top_up）。

★ 安全加固（2026-10-11 审计 Medium-High）：
  1. **生产环境默认拒绝执行**（DEBUG=False 且未显式确认时直接退出）。
     演示账号是固定口令，若在生产误跑且未改密，等于留了后门。
     确需在生产初始化演示数据，须显式加 `--force` 并自行承担风险。
  2. 演示口令**不再硬编码在源码里**，改为：
     - 优先读环境变量 `DEMO_PASSWORD`；
     - 未设置时生成**随机口令**并打印到控制台（一次性），
       同时标记 `must_change_password`，强制首次登录修改。
  3. 随机口令只在「用户新建」时使用；已存在用户不动（幂等）。
"""
import os
import secrets
from datetime import date, datetime, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.traceability import certno, services
from apps.traceability.models import (Certificate, InspectionRecord, MatchIntent,
                                      OperationLog, Producer, ProductBatch,
                                      SupplyDemand, TestReport)

User = get_user_model()

# ★ 演示账号清单：口令不再写死，见 _resolve_demo_password()
USERS = [
    ("admin", None, "admin", "系统管理员", "平台运营中心"),
    ("regulator", None, "regulator", "监管人员", "县农业农村局"),
    ("producer", None, "producer", "种植主体", "橙心柑橘种植合作社"),
    ("producer2", None, "producer", "种植主体", "甜蜜蜜家庭农场"),
    ("buyer", None, "buyer", "收购商", "鲜果汇供应链"),
]

# 行政区划代码（编号前 6 位）
REGION_CODES = {
    "橙心柑橘种植合作社": "511402",   # 四川省眉山市东坡区
    "甜蜜蜜家庭农场": "511424",       # 四川省眉山市丹棱县
}


class Command(BaseCommand):
    help = "初始化演示数据"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force", action="store_true",
            help="在非 DEBUG 环境也允许执行（危险：会创建演示账号）",
        )

    def handle(self, *args, **options):
        # ★ 生产环境闸门：DEBUG=False 且未 --force 时拒绝执行
        if not settings.DEBUG and not options.get("force"):
            raise CommandError(
                "当前为生产环境（DEBUG=False），init_data 会创建固定角色的演示账号，"
                "存在被直接登录的风险，已拒绝执行。\n"
                "  如确需执行，请显式加 --force，并务必随后修改所有演示账号口令。"
            )
        if not settings.DEBUG:
            self.stdout.write(self.style.WARNING(
                "⚠️  正在生产环境执行演示数据初始化（--force），请务必尽快修改演示账号口令！"))

        self._demo_password = self._resolve_demo_password()
        self._create_users()
        if Certificate.objects.exists():
            # 已有业务数据：不清库，只做幂等补全（适配老数据升级到新功能）
            self.stdout.write("  业务数据已存在，执行增量补全（不删除原数据）")
            self._top_up()
        else:
            self._create_business_data()
        self.stdout.write(self.style.SUCCESS("演示数据初始化完成！"))
        self.stdout.write("默认账号：admin / regulator / producer / producer2 / buyer")
        self.stdout.write(self.style.WARNING(
            "口令请见上面输出的演示口令（或环境变量 DEMO_PASSWORD）；"
            "首次登录后请立即修改。"))

    def _top_up(self):
        """
        幂等补全：让已有数据库也能演示「角色隔离 / 审计日志 / 产销匹配 / 监管检查」。

        老数据的问题是两个主体都挂在同一个 producer 账号下，
        角色隔离演示不出效果 —— 这里把第二个主体划给 producer2。
        """
        producer_user = User.objects.filter(username="producer").first()
        producer2_user = User.objects.filter(username="producer2").first()
        regulator_user = User.objects.filter(username="regulator").first()
        buyer_user = User.objects.filter(username="buyer").first()

        # 0) 补行政区划代码（编号规则需要）
        self._top_up_region_code()

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

        # 4) 补监管现场检查记录（若没有）
        if not InspectionRecord.objects.exists():
            self._create_inspections(regulator_user)

        # 4.5) ★ 补一张草稿合格证：演示「草稿不占号 → 提交送审 → 签发分配编号」全流程
        #      没有草稿时，主体端的「提交」按钮不会出现（按钮条件是 status=draft），
        #      答辩现场就无法演示这条主线。这里幂等补一张。
        #      注意：提交送审要求先关联检测报告，所以给该批次也补一份报告，
        #      否则「提交」会因缺报告而失败，流程仍演示不通。
        if not Certificate.objects.filter(status=Certificate.Status.DRAFT).exists():
            pb = (ProductBatch.objects.filter(producer=p2).first()
                  or ProductBatch.objects.order_by("-id").first())
            if pb:
                rpt = TestReport.objects.filter(batch=pb).first()
                if not rpt:
                    rpt = TestReport.objects.create(
                        report_no=f"RPT-{pb.batch_no.split('-')[-1]}-D",
                        batch=pb, test_org="眉山市丹棱县农产品质量检测中心",
                        test_date=date.today(), conclusion=TestReport.Conclusion.QUALIFIED,
                        pesticide_residue="未检出（<0.01mg/kg）",
                        heavy_metal="符合 GB 2762 标准",
                        remark="用于演示「建档 → 提交送审 → 签发分配编号」流程",
                    )
                    self.stdout.write(f"  已补充检测报告 {rpt.report_no}（草稿提交前置条件）")
                Certificate.objects.create(
                    batch=pb, report=rpt, producer_name=pb.producer.name,
                    product_name="柑橘", quantity_kg=3000,
                    status=Certificate.Status.DRAFT, issuer=regulator_user,
                    remark="待提交审核（演示编号分配流程）",
                )
                self.stdout.write("  已补充草稿合格证 1 张（用于演示提交送审 → 签发分配编号）")

        # 5) 补操作日志（若没有）
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

    def _top_up_region_code(self):
        """给老主体补行政区划代码；给老合格证补 19 位编号（CERT-xxx 旧格式）。"""
        for p in Producer.objects.all():
            if p.region_code:
                continue
            code = REGION_CODES.get(p.name, "")
            if not code and p.region:
                # 从 region 文本里没有标准码，统一兜底
                code = "000000"
            if code:
                p.region_code = code
                p.save(update_fields=["region_code", "updated_at"])
                self.stdout.write(f"  已补主体区域码 {p.name} -> {code}")

        # 老合格证编号（CERT-2026-1001）不符合 19 位规则：
        # 保留编号不动（已上链，改了哈希会失效），仅补签发日期，
        # 保证 certno.parse_cert_no 能优雅返回 valid=False 而不报错。
        for c in Certificate.objects.filter(status=Certificate.Status.ISSUED):
            if certno.is_valid_cert_no(c.cert_no):
                continue
            if not c.issue_date:
                c.issue_date = c.created_at.date() if c.created_at else date.today()
                c.save(update_fields=["issue_date", "updated_at"])
        legacy = Certificate.objects.exclude(
            status=Certificate.Status.ISSUED).count()
        if legacy:
            self.stdout.write(f"  历史合格证 {legacy} 张处于非签发状态（保留原编号）")

    def _create_inspections(self, regulator_user):
        """
        创建监督检查演示数据。

        三条记录覆盖三种典型情形：
          ① 合格 —— 闭环完成，无整改
          ② 不合格 + 已整改 —— 复查通过，原批次合格证作废，需重新申领
          ③ 不合格 + 逾期未整改 —— 合格证被暂停，超期告警
        这是「执法闭环」的完整演示。
        """
        certs = list(Certificate.objects.filter(
            status__in=[Certificate.Status.ISSUED,
                        Certificate.Status.SUSPENDED]).order_by("id"))
        if not certs:
            return
        c1 = certs[0]
        c2 = certs[1] if len(certs) > 1 else certs[0]

        # ① 日常检查合格
        r1 = InspectionRecord.objects.create(
            certificate=c1, inspector=regulator_user, inspector_name="赵国强",
            inspector_no="ZJ5114020012", department="眉山市东坡区农业农村局执法大队",
            check_time=datetime.now() - timedelta(days=6),
            check_location="东坡区万亩柑橘产业园冷库",
            check_type=InspectionRecord.CheckType.ROUTINE,
            check_result=InspectionRecord.CheckResult.QUALIFIED,
            check_score=96,
            problem_description="未发现质量问题，生产记录完整，承诺达标合格证使用规范。",
            rectify_status=InspectionRecord.RectifyStatus.NOT_REQUIRED,
            follow_up_person="赵国强",
        )
        services.anchor_inspection(r1)

        # ② 专项检查不合格 -> 已整改闭环
        r2 = InspectionRecord.objects.create(
            certificate=c2, inspector=regulator_user, inspector_name="孙丽娟",
            inspector_no="ZJ5114240008", department="眉山市丹棱县农业农村局执法大队",
            check_time=datetime.now() - timedelta(days=12),
            check_location="丹棱县不知火柑橘基地分选车间",
            check_type=InspectionRecord.CheckType.SPECIAL,
            check_result=InspectionRecord.CheckResult.UNQUALIFIED,
            check_score=68,
            problem_description="分选车间未按规定记录农药使用间隔期，农事档案缺 2 个批次记录；"
                                "包装区与成品区未有效隔离。",
            rectify_requirement="1. 7 日内补全农事记录台账；2. 完成包装区物理隔离改造；"
                                "3. 提交整改报告并接受复查。",
            rectify_deadline=date.today() - timedelta(days=5),
            rectify_status=InspectionRecord.RectifyStatus.DONE,
            rectify_time=datetime.now() - timedelta(days=6),
            rectify_note="已完成农事台账补录，包装区加装隔离挡板并通过现场复查。",
            follow_up_person="孙丽娟",
            penalty_amount=2000,
            penalty_reason="未按规定建立农产品生产记录",
            legal_basis="《农产品质量安全法》第六十五条",
        )
        services.anchor_inspection(r2)

        # ③ 投诉核查不合格 -> 逾期未整改（合格证被暂停）
        r3 = InspectionRecord.objects.create(
            certificate=c2, inspector=regulator_user, inspector_name="孙丽娟",
            inspector_no="ZJ5114240008", department="眉山市丹棱县农业农村局执法大队",
            check_time=datetime.now() - timedelta(days=20),
            check_location="丹棱县不知火柑橘基地",
            check_type=InspectionRecord.CheckType.COMPLAINT,
            check_result=InspectionRecord.CheckResult.UNQUALIFIED,
            check_score=55,
            problem_description="接到消费者投诉后核查，发现 1 个批次未按规定开具承诺达标合格证"
                                "即对外销售，且未建立销售台账。",
            rectify_requirement="立即停止该批次销售，10 日内建立销售台账并补充开具合格证。",
            rectify_deadline=date.today() - timedelta(days=3),
            rectify_status=InspectionRecord.RectifyStatus.OVERDUE,
            follow_up_person="孙丽娟",
            penalty_amount=5000,
            penalty_reason="未按规定开具承诺达标合格证",
            legal_basis="《农产品质量安全承诺达标合格证管理办法》第二十一条",
        )
        services.anchor_inspection(r3)

        # 逾期检查 -> 关联合格证置为「暂停使用」
        if r3.certificate.status != Certificate.Status.REVOKED:
            r3.certificate.status = Certificate.Status.SUSPENDED
            r3.certificate.suspend_reason = (
                f"监督检查不合格且逾期未整改（检查#{r3.id}）")
            r3.certificate.save(update_fields=["status", "suspend_reason", "updated_at"])

        self.stdout.write("  已补充监督检查记录 3 条（合格1 / 已整改1 / 逾期1，"
                          "并联动暂停 1 张合格证）")

    def _resolve_demo_password(self) -> str:
        """
        决定演示账号口令（安全加固）：
          - 优先读环境变量 DEMO_PASSWORD（便于答辩/演示时统一指定）；
          - 未设置则生成随机强口令，并**只打印这一次**。
        ★ 不再把口令硬编码在源码里 —— 源码/文档中不再出现任何可用口令。
        """
        pw = os.environ.get("DEMO_PASSWORD", "").strip()
        if pw:
            self.stdout.write(f"  使用环境变量 DEMO_PASSWORD 指定的演示口令")
            return pw
        pw = secrets.token_urlsafe(12)
        self.stdout.write(self.style.WARNING(
            f"  ⚠️ 未设置 DEMO_PASSWORD，已生成随机演示口令（仅显示本次）：{pw}"))
        return pw

    def _create_users(self):
        demo_pw = getattr(self, "_demo_password", None) or self._resolve_demo_password()
        created_any = False
        rotated = []
        for username, _pw, role, _, org in USERS:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"role": role, "org_name": org,
                          "first_name": org, "is_staff": role == "admin",
                          "is_superuser": role == "admin"},
            )
            if created:
                user.set_password(demo_pw)
                # ★ 标记强制首次修改口令（User 模型已提供该字段）
                if hasattr(user, "must_change_password"):
                    user.must_change_password = True
                user.save()
                created_any = True
                self.stdout.write(f"  创建用户 {username} ({role})")
            else:
                # ★ 续审修复：**已有**演示账号也要轮换口令。
                #   原实现只在 created 时设密码 → 老库里的账号口令永远是当初那个，
                #   「不再硬编码」只对新库有效，线上旧账号仍留着重口令风险。
                #   这里对**已存在的演示账号**统一重置为本次口令并标记强制改密。
                user.set_password(demo_pw)
                fields = ["password"]
                if hasattr(user, "must_change_password"):
                    user.must_change_password = True
                    fields.append("must_change_password")
                user.save(update_fields=fields)
                rotated.append(username)
        if created_any:
            self.stdout.write(self.style.WARNING(
                "  ⚠️ 演示账号已创建，请首次登录后立即修改口令！"))
        if rotated:
            self.stdout.write(self.style.WARNING(
                "  ⚠️ 已重置以下**已存在**演示账号的口令为本次口令（并标记强制改密）："
                + "、".join(rotated)))

    def _create_business_data(self):
        producer_user = User.objects.filter(username="producer").first()
        producer2_user = User.objects.filter(username="producer2").first()
        regulator_user = User.objects.filter(username="regulator").first()
        buyer_user = User.objects.filter(username="buyer").first()

        # 生产主体 —— ★ 分属两个不同账号，用于演示「数据级权限隔离」
        p1 = Producer.objects.create(
            name="橙心柑橘种植合作社", credit_code="91510100MA6XXXXX1A",
            contact="王建国", phone="13800001111", region="四川省眉山市东坡区",
            region_code="511402",
            address="东坡区万亩柑橘产业园", audit_status=Producer.AuditStatus.APPROVED,
            owner=producer_user,
        )
        p2 = Producer.objects.create(
            name="甜蜜蜜家庭农场", credit_code="91510100MA6XXXXX2B",
            contact="李秀兰", phone="13800002222", region="四川省眉山市丹棱县",
            region_code="511424",
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

        # 合格证（★ 编号按 19 位规则自动分配 + 自动存证）
        # 注意：先建对象（不落库）-> 填业务字段 -> save_with_cert_no 取号落库
        c1 = Certificate(
            batch=b1, report=r1,
            producer_name=p1.name, product_name="柑橘", quantity_kg=5000,
            issue_date=date.today() - timedelta(days=2),
            valid_until=date.today() + timedelta(days=28),
            status=Certificate.Status.ISSUED, issuer=regulator_user,
        )
        certno.save_with_cert_no(c1, p1)
        services.anchor_certificate(c1)

        c2 = Certificate(
            batch=b2, report=r2,
            producer_name=p1.name, product_name="柑橘", quantity_kg=8000,
            issue_date=date.today() - timedelta(days=1),
            valid_until=date.today() + timedelta(days=29),
            status=Certificate.Status.ISSUED, issuer=regulator_user,
        )
        certno.save_with_cert_no(c2, p1)
        services.anchor_certificate(c2)

        self.stdout.write(f"  合格证编号（19位规则）：{c1.cert_no} / {c2.cert_no}")

        # ★ 一张草稿合格证：演示「草稿不占号 -> 提交 -> 签发分配编号」的流程
        Certificate.objects.create(
            batch=b3, producer_name=p2.name, product_name="柑橘", quantity_kg=3000,
            status=Certificate.Status.DRAFT, issuer=regulator_user,
            remark="待补充检测报告后提交审核（演示编号分配流程）",
        )

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

        # ★ 监管现场监督检查（执法闭环演示）
        self._create_inspections(regulator_user)

        # ★ 操作日志（审计演示：把初始化动作也留痕）
        OperationLog.objects.bulk_create([
            OperationLog(operator=regulator_user, operator_name="regulator",
                         operator_role="regulator", action="issue",
                         target_type="合格证", target_no=c1.cert_no,
                         detail=f"签发合格证 {c1.cert_no}", ip="127.0.0.1"),
            OperationLog(operator=regulator_user, operator_name="regulator",
                         operator_role="regulator", action="issue",
                         target_type="合格证", target_no=c2.cert_no,
                         detail=f"签发合格证 {c2.cert_no}", ip="127.0.0.1"),
            OperationLog(operator=producer_user, operator_name="producer",
                         operator_role="producer", action="anchor",
                         target_type="生产批次", target_no="BATCH-2026-1001",
                         detail="批次存证 BATCH-2026-1001", ip="127.0.0.1"),
            OperationLog(operator=buyer_user, operator_name="buyer",
                         operator_role="buyer", action="match",
                         target_type="产销意向单", target_no="供应春见耙耙柑8000kg（附合格证）",
                         detail=f"发起对接意向（匹配度 {r['score']}）", ip="127.0.0.1"),
        ])
        self.stdout.write("  创建生产主体2个、批次3个、报告2份、合格证2张+1草稿、"
                          "供需4条、意向1条、检查3条、日志4条")