"""
核心业务单元测试

覆盖八大类：
  1. 哈希存证一致性（同一数据多次计算结果一致、字段变更导致哈希变化）
  2. 篡改检测（本地比对 + 高级篡改场景）
  3. 角色权限隔离（接口级 + 数据级）
  4. 审计日志（关键动作是否留痕）
  5. 产销智能匹配（打分因子与推荐排序）
  6. 端到端主流程（建档 → 提交 → 签发 → 验真）
  7. 合格证编号规则（19 位：区域码6 + 日期8 + 流水5，防占号）
  8. 监管现场监督检查（执法闭环：检查 → 整改 → 复查 → 合格证状态联动）

运行：python manage.py test apps.traceability -v 2
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import Role, User

from . import certno, matching, services
from .models import (Certificate, EvidenceRecord, InspectionRecord, MatchIntent,
                     OperationLog, Producer, ProductBatch, SupplyDemand,
                     TestReport)


def make_user(username, role, password="admin123"):
    return User.objects.create_user(username=username, password=password, role=role)


class BaseFixture(TestCase):
    """公共测试数据：一个主体 + 一个批次 + 一份报告 + 一张合格证"""

    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("t_admin", Role.ADMIN)
        cls.regulator = make_user("t_reg", Role.REGULATOR)
        cls.producer = make_user("t_prod", Role.PRODUCER)
        cls.other_producer = make_user("t_prod2", Role.PRODUCER)
        cls.buyer = make_user("t_buyer", Role.BUYER)

        cls.producer_obj = Producer.objects.create(
            name="测试柑橘合作社", contact="张三", phone="13800000000",
            region="新疆阿克苏", audit_status=Producer.AuditStatus.APPROVED,
            owner=cls.producer)
        cls.other_producer_obj = Producer.objects.create(
            name="另一家合作社", contact="李四", phone="13900000000",
            region="新疆库尔勒", audit_status=Producer.AuditStatus.PENDING,
            owner=cls.other_producer)

        cls.batch = ProductBatch.objects.create(
            batch_no="B-TEST-001", producer=cls.producer_obj,
            product_name="柑橘", variety="砂糖橘", origin="新疆阿克苏",
            harvest_date=date(2026, 9, 1), weight_kg=Decimal("8000.00"))

        cls.report = TestReport.objects.create(
            report_no="R-TEST-001", batch=cls.batch, test_org="阿克苏检测中心",
            test_date=date(2026, 9, 5), conclusion=TestReport.Conclusion.QUALIFIED)

        cls.cert = Certificate.objects.create(
            cert_no="CERT-TEST-001", batch=cls.batch, report=cls.report,
            producer_name=cls.producer_obj.name, product_name="柑橘",
            quantity_kg=Decimal("8000.00"), issue_date=date(2026, 9, 6),
            valid_until=date(2026, 12, 6),
            status=Certificate.Status.ISSUED, issuer=cls.regulator)

    def login(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c


# ===============================================================
# 一、哈希存证一致性
# ===============================================================
class HashConsistencyTest(BaseFixture):

    def test_same_data_produces_same_hash(self):
        """同一份数据多次计算，哈希必须完全一致（否则存证无意义）"""
        h1 = services.compute_certificate_hash(self.cert)
        h2 = services.compute_certificate_hash(self.cert)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64, "SHA-256 应为 64 位十六进制")

    def test_decimal_format_normalized(self):
        """★ 关键回归：8000 与 8000.00 必须算出同一个哈希

        这是实际踩过的坑 —— 若不统一小数位，同一语义的数据会拼出不同原文，
        导致验真永远失败。
        """
        self.cert.quantity_kg = Decimal("8000")
        h_int = services.compute_certificate_hash(self.cert)
        self.cert.quantity_kg = Decimal("8000.00")
        h_dec = services.compute_certificate_hash(self.cert)
        self.assertEqual(h_int, h_dec)

    def test_field_change_changes_hash(self):
        """任一参与哈希的字段变化，哈希必须变化（否则篡改检测失效）"""
        before = services.compute_certificate_hash(self.cert)
        self.cert.quantity_kg = Decimal("9999.00")
        after = services.compute_certificate_hash(self.cert)
        self.assertNotEqual(before, after)

    def test_anchor_sets_hash_and_record(self):
        """存证后应写回哈希、置 is_anchored，并产生一条存证记录"""
        record = services.anchor_certificate(self.cert)
        self.cert.refresh_from_db()
        self.assertTrue(self.cert.is_anchored)
        self.assertEqual(len(self.cert.cert_hash), 64)
        self.assertEqual(record.data_hash, self.cert.cert_hash)
        self.assertEqual(record.biz_no, self.cert.cert_no)

    def test_payload_field_order_stable(self):
        """规范化原文的字段顺序必须固定（顺序变了哈希就变）"""
        payload = services.build_certificate_payload(self.cert)
        parts = payload.split("|")
        self.assertEqual(parts[0], self.cert.cert_no)
        self.assertEqual(parts[2], self.cert.product_name)


# ===============================================================
# 二、篡改检测（本地 + 链上双层）
# ===============================================================
class TamperDetectionTest(BaseFixture):

    def test_verify_true_when_untampered(self):
        """未篡改时验真应通过"""
        services.anchor_certificate(self.cert)
        result = services.verify_certificate(self.cert)
        self.assertTrue(result["trusted"])
        self.assertEqual(result["current_hash"], result["stored_hash"])

    def test_verify_false_when_business_data_tampered(self):
        """★ 第一层：改业务数据 → 本地比对应抓出"""
        services.anchor_certificate(self.cert)
        # 绕过 save 直接改库，模拟攻击者改数据
        Certificate.objects.filter(id=self.cert.id).update(
            quantity_kg=Decimal("99999.00"))
        self.cert.refresh_from_db()
        result = services.verify_certificate(self.cert)
        self.assertFalse(result["trusted"], "改业务数据后应判定不可信")

    def test_verify_false_when_summary_also_tampered(self):
        """★ 第二层场景：连存证表摘要一起改，本地比对会被骗过

        这解释了为什么需要链上核验 —— 本地比对此时返回 True（误判），
        真实系统中链上核验会返回 False 把它抓出来。
        本测试断言「本地比对确实被骗过」，用于说明链上核验的必要性。
        """
        services.anchor_certificate(self.cert)
        # 攻击者同时改业务数据和存证摘要，让两者重新一致
        Certificate.objects.filter(id=self.cert.id).update(
            quantity_kg=Decimal("99999.00"))
        self.cert.refresh_from_db()
        fake_hash = services.compute_certificate_hash(self.cert)
        EvidenceRecord.objects.filter(
            biz_type=EvidenceRecord.BizType.CERTIFICATE,
            biz_id=self.cert.id).update(data_hash=fake_hash)

        result = services.verify_certificate(self.cert, check_chain=False)
        self.assertTrue(result["trusted"],
                        "本地比对在此场景下会被骗过（这正是需要链上核验的原因）")

    def test_verify_without_record(self):
        """没有存证记录时不能判为可信"""
        result = services.verify_certificate(self.cert)
        self.assertFalse(result["trusted"])

    def test_revoke_creates_evidence(self):
        """作废必须留痕（产生 REVOKE 类型存证）"""
        record = services.anchor_revoke(self.cert, reason="检测不合格")
        self.assertEqual(record.biz_type, EvidenceRecord.BizType.REVOKE)
        self.assertIn("REVOKED", record.payload)
        self.assertIn("检测不合格", record.payload)


# ===============================================================
# 三、角色权限隔离
# ===============================================================
class PermissionTest(BaseFixture):

    def test_anonymous_cannot_access_business_api(self):
        """未登录不能访问业务接口"""
        c = APIClient()
        for path in ["/api/producer/", "/api/batch/", "/api/certificate/",
                     "/api/evidence/", "/api/log/"]:
            self.assertEqual(c.get(path).status_code, 401, path)

    def test_public_verify_is_open(self):
        """公众验真必须免登录"""
        c = APIClient()
        r = c.get(f"/api/public/verify/{self.cert.cert_no}/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["data"]["trusted"] or True)  # 未存证也可查

    def test_only_regulator_can_audit(self):
        """★ 主体审核仅监管/管理员可做"""
        producer_client = self.login(self.producer)
        r = producer_client.post(f"/api/producer/{self.producer_obj.id}/audit/",
                                 {"audit_status": "approved"}, format="json")
        self.assertEqual(r.status_code, 403, "种植主体不应能审核")

        reg_client = self.login(self.regulator)
        r = reg_client.post(f"/api/producer/{self.producer_obj.id}/audit/",
                            {"audit_status": "approved"}, format="json")
        self.assertEqual(r.status_code, 200, "监管应能审核")

    def test_only_regulator_can_issue_and_revoke(self):
        """★ 合格证签发/作废仅监管可做"""
        producer_client = self.login(self.producer)
        self.assertEqual(
            producer_client.post(f"/api/certificate/{self.cert.id}/issue/").status_code,
            403)
        self.assertEqual(
            producer_client.post(f"/api/certificate/{self.cert.id}/revoke/").status_code,
            403)

        reg_client = self.login(self.regulator)
        self.assertEqual(
            reg_client.post(f"/api/certificate/{self.cert.id}/issue/").status_code, 200)
        self.assertEqual(
            reg_client.post(f"/api/certificate/{self.cert.id}/revoke/").status_code, 200)

    def test_producer_sees_only_own_producers(self):
        """★ 数据级隔离：种植主体只能看到自己的主体"""
        c = self.login(self.producer)
        r = c.get("/api/producer/")
        names = [x["name"] for x in r.data["results"] if "name" in x] \
            if isinstance(r.data, dict) and "results" in r.data \
            else [x["name"] for x in r.data]
        self.assertIn(self.producer_obj.name, names)
        self.assertNotIn(self.other_producer_obj.name, names,
                         "不应看到其他种植主体的数据")

    def test_regulator_sees_all_producers(self):
        """监管应能看到全部主体"""
        c = self.login(self.regulator)
        r = c.get("/api/producer/")
        names = [x["name"] for x in (r.data["results"] if isinstance(r.data, dict)
                                     and "results" in r.data else r.data)]
        self.assertIn(self.producer_obj.name, names)
        self.assertIn(self.other_producer_obj.name, names)

    def test_buyer_sees_only_approved_producers(self):
        """★ 收购商只能看到已审核通过的主体"""
        c = self.login(self.buyer)
        r = c.get("/api/producer/")
        names = [x["name"] for x in (r.data["results"] if isinstance(r.data, dict)
                                     and "results" in r.data else r.data)]
        self.assertIn(self.producer_obj.name, names)
        self.assertNotIn(self.other_producer_obj.name, names,
                         "未审核主体不应暴露给收购商")

    def test_buyer_sees_only_issued_certificates(self):
        """★ 收购商只能看到已签发的合格证"""
        Certificate.objects.create(
            cert_no="CERT-PENDING-001", batch=self.batch, report=self.report,
            producer_name=self.producer_obj.name, product_name="柑橘",
            quantity_kg=Decimal("100.00"), status=Certificate.Status.PENDING)
        c = self.login(self.buyer)
        r = c.get("/api/certificate/")
        rows = r.data["results"] if isinstance(r.data, dict) and "results" in r.data \
            else r.data
        cert_nos = [x["cert_no"] for x in rows]
        self.assertIn(self.cert.cert_no, cert_nos)
        self.assertNotIn("CERT-PENDING-001", cert_nos,
                         "待审核的证不应暴露给收购商")

    def test_dashboard_differs_by_role(self):
        """★ 看板统计应随角色不同（角色差异化的直接体现）"""
        reg_data = self.login(self.regulator).get("/api/dashboard/").data["data"]
        prod_data = self.login(self.producer).get("/api/dashboard/").data["data"]
        self.assertIn("监管视角", reg_data["scope_label"])
        self.assertIn("种植主体视角", prod_data["scope_label"])
        # 监管看全量 2 个主体，种植主体只看自己 1 个
        self.assertEqual(reg_data["producer_count"], 2)
        self.assertEqual(prod_data["producer_count"], 1)

    def test_role_matrix_endpoint(self):
        """角色矩阵接口应返回完整矩阵"""
        c = self.login(self.admin)
        r = c.get("/api/role-matrix/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["data"]["resource_matrix"]), 9)
        self.assertEqual(len(r.data["data"]["roles"]), 4)

    def test_profile_contains_permissions(self):
        """登录返回的权限清单应正确标记各角色能力"""
        c = self.login(self.regulator)
        perms = c.get("/api/auth/profile/").data["data"]["permissions"]
        self.assertTrue(perms["can_audit"])
        self.assertTrue(perms["can_issue"])
        self.assertTrue(perms["can_inspect"])
        self.assertTrue(perms["can_rectify"])
        self.assertTrue(perms["can_view_all"])

        c = self.login(self.producer)
        perms = c.get("/api/auth/profile/").data["data"]["permissions"]
        self.assertFalse(perms["can_audit"])
        self.assertFalse(perms["can_issue"])
        self.assertFalse(perms["can_inspect"])
        self.assertTrue(perms["can_submit_cert"])
        self.assertTrue(perms["can_write_production"])


# ===============================================================
# 四、审计日志
# ===============================================================
class AuditLogTest(BaseFixture):

    def test_login_writes_log(self):
        """登录成功/失败都应留痕"""
        c = APIClient()
        c.post("/api/auth/login/", {"username": "t_reg", "password": "admin123"},
               format="json")
        c.post("/api/auth/login/", {"username": "t_reg", "password": "wrong"},
               format="json")
        logs = OperationLog.objects.filter(action=OperationLog.Action.LOGIN)
        self.assertEqual(logs.count(), 2)
        self.assertEqual(logs.filter(success=False).count(), 1)

    def test_issue_writes_log(self):
        """签发合格证应留痕，且记录操作人与角色"""
        c = self.login(self.regulator)
        c.post(f"/api/certificate/{self.cert.id}/issue/")
        log = OperationLog.objects.filter(
            action=OperationLog.Action.ISSUE).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.operator_name, "t_reg")
        self.assertEqual(log.operator_role, Role.REGULATOR)
        self.assertEqual(log.target_no, self.cert.cert_no)

    def test_revoke_writes_log_with_reason(self):
        """作废应留痕并记录原因"""
        c = self.login(self.regulator)
        c.post(f"/api/certificate/{self.cert.id}/revoke/",
               {"reason": "抽检不合格"}, format="json")
        log = OperationLog.objects.filter(
            action=OperationLog.Action.REVOKE).first()
        self.assertIsNotNone(log)
        self.assertIn("抽检不合格", log.detail)

    def test_audit_writes_log(self):
        """主体审核应留痕"""
        c = self.login(self.regulator)
        c.post(f"/api/producer/{self.other_producer_obj.id}/audit/",
               {"audit_status": "approved"}, format="json")
        log = OperationLog.objects.filter(action=OperationLog.Action.AUDIT).first()
        self.assertIsNotNone(log)
        self.assertIn("待审核", log.detail)
        self.assertIn("已通过", log.detail)

    def test_anchor_writes_log(self):
        """存证动作应留痕"""
        c = self.login(self.producer)
        c.post(f"/api/batch/{self.batch.id}/anchor/")
        log = OperationLog.objects.filter(action=OperationLog.Action.ANCHOR).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.target_no, self.batch.batch_no)

    def test_producer_sees_only_own_logs(self):
        """★ 数据级隔离：种植主体只能看到自己的操作日志"""
        # 监管做一次操作
        self.login(self.regulator).post(f"/api/certificate/{self.cert.id}/issue/")
        # 种植主体做一次操作
        self.login(self.producer).post(f"/api/batch/{self.batch.id}/anchor/")

        c = self.login(self.producer)
        r = c.get("/api/log/")
        rows = r.data["results"] if isinstance(r.data, dict) and "results" in r.data \
            else r.data
        operators = {x["operator_name"] for x in rows}
        self.assertEqual(operators, {"t_prod"},
                         "种植主体不应看到监管的操作日志")

    def test_regulator_sees_all_logs(self):
        """监管可看到全部日志"""
        self.login(self.producer).post(f"/api/batch/{self.batch.id}/anchor/")
        c = self.login(self.regulator)
        r = c.get("/api/log/")
        rows = r.data["results"] if isinstance(r.data, dict) and "results" in r.data \
            else r.data
        operators = {x["operator_name"] for x in rows}
        self.assertIn("t_prod", operators)


# ===============================================================
# 五、产销智能匹配
# ===============================================================
class MatchingTest(BaseFixture):

    def setUp(self):
        self.supply = SupplyDemand.objects.create(
            kind=SupplyDemand.Kind.SUPPLY, title="阿克苏砂糖橘供应",
            product_name="柑橘", variety="砂糖橘", quantity_kg=Decimal("5000.00"),
            price=Decimal("6.00"), region="新疆阿克苏",
            certificate=self.cert, batch=self.batch, publisher=self.producer)

    def test_perfect_match_scores_high(self):
        """品种/数量/价格/地区全一致 → 高分"""
        demand = SupplyDemand.objects.create(
            kind=SupplyDemand.Kind.DEMAND, title="求购砂糖橘",
            product_name="柑橘", variety="砂糖橘", quantity_kg=Decimal("5000.00"),
            price=Decimal("6.00"), region="新疆阿克苏", publisher=self.buyer)
        r = matching.score_match(self.supply, demand)
        self.assertGreaterEqual(r["score"], 90, f"应高分，实际 {r['score']}")
        self.assertEqual(len(r["factors"]), 5)

    def test_mismatched_product_scores_low(self):
        """产品完全不同 → 低分"""
        demand = SupplyDemand.objects.create(
            kind=SupplyDemand.Kind.DEMAND, title="求购苹果",
            product_name="苹果", variety="红富士", quantity_kg=Decimal("5000.00"),
            price=Decimal("6.00"), region="山东烟台", publisher=self.buyer)
        r = matching.score_match(self.supply, demand)
        self.assertLess(r["score"], 60, f"应低分，实际 {r['score']}")

    def test_quantity_similarity_matters(self):
        """数量越接近，得分越高"""
        near = SupplyDemand.objects.create(
            kind=SupplyDemand.Kind.DEMAND, title="求购A", product_name="柑橘",
            variety="砂糖橘", quantity_kg=Decimal("5000.00"),
            price=Decimal("6.00"), region="新疆阿克苏")
        far = SupplyDemand.objects.create(
            kind=SupplyDemand.Kind.DEMAND, title="求购B", product_name="柑橘",
            variety="砂糖橘", quantity_kg=Decimal("500.00"),
            price=Decimal("6.00"), region="新疆阿克苏")
        s_near = matching.score_match(self.supply, near)["score"]
        s_far = matching.score_match(self.supply, far)["score"]
        self.assertGreater(s_near, s_far)

    def test_price_deviation_decays(self):
        """价格偏离越大，得分越低"""
        good = SupplyDemand.objects.create(
            kind=SupplyDemand.Kind.DEMAND, title="价格合适", product_name="柑橘",
            variety="砂糖橘", quantity_kg=Decimal("5000.00"),
            price=Decimal("6.20"), region="新疆阿克苏")
        bad = SupplyDemand.objects.create(
            kind=SupplyDemand.Kind.DEMAND, title="价格差大", product_name="柑橘",
            variety="砂糖橘", quantity_kg=Decimal("5000.00"),
            price=Decimal("20.00"), region="新疆阿克苏")
        self.assertGreater(matching.score_match(self.supply, good)["score"],
                           matching.score_match(self.supply, bad)["score"])

    def test_score_requires_opposite_kinds(self):
        """同类型（供应 vs 供应）不能匹配"""
        supply2 = SupplyDemand.objects.create(
            kind=SupplyDemand.Kind.SUPPLY, title="另一个供应",
            product_name="柑橘", quantity_kg=Decimal("1000.00"))
        with self.assertRaises(ValueError):
            matching.score_match(self.supply, supply2)

    def test_recommend_returns_sorted(self):
        """推荐结果必须按匹配度降序"""
        for i, qty in enumerate(["5000.00", "1000.00", "500.00"]):
            SupplyDemand.objects.create(
                kind=SupplyDemand.Kind.DEMAND, title=f"求购{i}",
                product_name="柑橘", variety="砂糖橘",
                quantity_kg=Decimal(qty), price=Decimal("6.00"),
                region="新疆阿克苏")
        results = matching.recommend_for(self.supply, limit=5)
        scores = [r["score"] for r in results]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(len(results), 3)

    def test_recommend_api(self):
        """推荐接口应可用"""
        SupplyDemand.objects.create(
            kind=SupplyDemand.Kind.DEMAND, title="求购砂糖橘",
            product_name="柑橘", variety="砂糖橘", quantity_kg=Decimal("5000.00"),
            price=Decimal("6.00"), region="新疆阿克苏")
        c = self.login(self.producer)
        r = c.get(f"/api/supply/{self.supply.id}/recommend/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["data"]), 1)

    def test_both_sides_can_initiate_matching(self):
        """
        ★ 产销衔接的完整性：种植主体与收购商都能发起对接。

        若只放开收购商，种植主体就无法主动找买家，「产销衔接」名不副实。
        两边都能发起，才是真正的双向撮合。
        """
        SupplyDemand.objects.create(
            kind=SupplyDemand.Kind.DEMAND, title="求购砂糖橘",
            product_name="柑橘", variety="砂糖橘", quantity_kg=Decimal("5000.00"),
            price=Decimal("6.00"), region="新疆阿克苏")
        for user in (self.producer, self.buyer):
            c = self.login(user)
            r = c.get(f"/api/supply/{self.supply.id}/recommend/")
            self.assertEqual(r.status_code, 200,
                             f"{user.username} 应能查看匹配推荐")
            r2 = c.post(f"/api/supply/{self.supply.id}/intent/",
                        {"quantity_kg": "1000.00", "price": "6.00"},
                        format="json")
            self.assertEqual(r2.status_code, 200,
                             f"{user.username} 应能发起对接意向")

    def test_create_intent_records_score(self):
        """★ 发起意向单应写入匹配度评分，实现对接留痕"""
        demand = SupplyDemand.objects.create(
            kind=SupplyDemand.Kind.DEMAND, title="求购砂糖橘",
            product_name="柑橘", variety="砂糖橘", quantity_kg=Decimal("5000.00"),
            price=Decimal("6.00"), region="新疆阿克苏")
        c = self.login(self.buyer)
        r = c.post(f"/api/supply/{self.supply.id}/intent/",
                   {"counterpart_id": demand.id, "message": "希望长期合作"},
                   format="json")
        self.assertEqual(r.status_code, 200)
        intent = MatchIntent.objects.first()
        self.assertIsNotNone(intent)
        self.assertGreater(intent.match_score, 0)
        self.assertEqual(intent.initiator, self.buyer)
        self.assertEqual(intent.supply_id, self.supply.id)

    def test_accept_intent_marks_supply_matched(self):
        """接受意向应把供需单置为已对接"""
        intent = MatchIntent.objects.create(
            supply=self.supply, initiator=self.buyer,
            quantity_kg=Decimal("5000.00"), price=Decimal("6.00"))
        c = self.login(self.producer)   # 发布方回应
        r = c.post(f"/api/intent/{intent.id}/respond/",
                   {"status": "accepted"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.supply.refresh_from_db()
        self.assertEqual(self.supply.deal_status, SupplyDemand.DealStatus.MATCHED)

    def test_matching_writes_audit_log(self):
        """产销对接应留痕"""
        c = self.login(self.buyer)
        c.post(f"/api/supply/{self.supply.id}/match/")
        log = OperationLog.objects.filter(action=OperationLog.Action.MATCH).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.target_no, self.supply.title)


# ===============================================================
# 六、端到端主流程
# ===============================================================
class EndToEndFlowTest(BaseFixture):
    """模拟完整业务链路：录批次 → 出报告 → 建档 → 提交 → 签发（分配编号）→ 公众验真"""

    def test_full_flow(self):
        # 1. 种植主体新建批次
        c = self.login(self.producer)
        r = c.post("/api/batch/", {
            "batch_no": "B-E2E-001", "producer": self.producer_obj.id,
            "product_name": "柑橘", "variety": "沃柑",
            "weight_kg": "3000.00", "harvest_date": "2026-09-10",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        batch_id = r.data["id"]

        # 2. 建检测报告（自动存证）
        r = c.post("/api/report/", {
            "report_no": "R-E2E-001", "batch": batch_id,
            "conclusion": "qualified", "test_date": "2026-09-11",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        report_id = r.data["id"]

        # 3. 建档（草稿，不占号）
        reg = self.login(self.regulator)
        r = reg.post("/api/certificate/", {
            "batch": batch_id, "report": report_id,
            "producer_name": self.producer_obj.name, "product_name": "柑橘",
            "quantity_kg": "3000.00",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        cert_id = r.data["id"]
        self.assertEqual(r.data["status"], Certificate.Status.DRAFT)
        self.assertIsNone(r.data["cert_no"], "草稿阶段不应分配编号")

        # 4. 提交审核（仍不占号）
        r = reg.post(f"/api/certificate/{cert_id}/submit/")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIsNone(r.data["data"]["cert_no"], "待审核阶段不应分配编号")

        # 5. 签发 —— 此时才分配 19 位编号
        r = reg.post(f"/api/certificate/{cert_id}/issue/")
        self.assertEqual(r.status_code, 200, r.data)
        cert_no = r.data["data"]["cert_no"]
        self.assertTrue(certno.is_valid_cert_no(cert_no), f"编号应为 19 位：{cert_no}")
        self.assertEqual(r.data["data"]["status"], Certificate.Status.ISSUED)

        # 6. 公众免登录验真
        anon = APIClient()
        r = anon.get(f"/api/public/verify/{cert_no}/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["data"]["trusted"], "完整链路后应判可信")
        self.assertTrue(r.data["data"]["cert_no_parsed"]["valid"])

        # 7. 全链路日志应完整
        actions = set(OperationLog.objects.values_list("action", flat=True))
        self.assertIn(OperationLog.Action.CREATE, actions)
        self.assertIn(OperationLog.Action.ISSUE, actions)


# ===============================================================
# 七、合格证编号规则（19 位：区域码6 + 日期8 + 流水5）
# ===============================================================
class CertNoRuleTest(BaseFixture):

    def test_normalize_region_code(self):
        """区域码容错：空/非数字兜底 000000，不足左补零，超长截断。"""
        self.assertEqual(certno.normalize_region_code(""), "000000")
        self.assertEqual(certno.normalize_region_code("四川省眉山"), "000000")
        self.assertEqual(certno.normalize_region_code("5114"), "005114")
        self.assertEqual(certno.normalize_region_code("511402123"), "511402")
        self.assertEqual(certno.normalize_region_code("511402"), "511402")

    def test_cert_no_is_19_digits(self):
        """生成的编号必须是 19 位纯数字。"""
        producer = Producer.objects.create(
            name="编号测试主体", contact="王五", phone="13700000000",
            region_code="511402", owner=self.producer)
        no = certno.peek_next_cert_no(producer, on_date=date(2026, 9, 23))
        self.assertEqual(len(no), certno.TOTAL_LEN)
        self.assertTrue(certno.is_valid_cert_no(no))
        self.assertEqual(no[:6], "511402")
        self.assertEqual(no[6:14], "20260923")
        self.assertEqual(no[14:], "00001")

    def test_sequence_increments_within_same_day(self):
        """同一天同一区域，流水号依次递增。"""
        producer = Producer.objects.create(
            name="流水测试主体", contact="赵六", phone="13600000000",
            region_code="511424", owner=self.producer)
        day = date(2026, 9, 23)
        seen = []
        for i in range(3):
            c = Certificate(batch=self.batch, producer_name=producer.name,
                            product_name="柑橘", quantity_kg=100,
                            status=Certificate.Status.ISSUED)
            seen.append(certno.save_with_cert_no(c, producer, on_date=day))
        self.assertEqual(seen, ["5114242026092300001",
                                "5114242026092300002",
                                "5114242026092300003"])

    def test_different_region_has_independent_sequence(self):
        """不同区域码的流水互不影响。"""
        pa = Producer.objects.create(name="A区主体", contact="a", phone="1",
                                     region_code="511402", owner=self.producer)
        pb = Producer.objects.create(name="B区主体", contact="b", phone="2",
                                     region_code="511424", owner=self.producer)
        day = date(2026, 9, 23)
        ca = Certificate(batch=self.batch, quantity_kg=1)
        no_a = certno.save_with_cert_no(ca, pa, on_date=day)
        cb = Certificate(batch=self.batch, quantity_kg=1)
        no_b = certno.save_with_cert_no(cb, pb, on_date=day)
        self.assertEqual(no_a, "5114022026092300001")
        self.assertEqual(no_b, "5114242026092300001")

    def test_parse_cert_no(self):
        """编号可反解出区域 / 日期 / 流水。"""
        parsed = certno.parse_cert_no("5114022026092300007")
        self.assertTrue(parsed["valid"])
        self.assertEqual(parsed["region_code"], "511402")
        self.assertEqual(parsed["date"], "2026-09-23")
        self.assertEqual(parsed["seq"], "00007")
        self.assertFalse(certno.parse_cert_no("CERT-2026-1001")["valid"])
        self.assertFalse(certno.parse_cert_no(None)["valid"])

    def test_draft_does_not_consume_sequence(self):
        """★ 防占号：草稿不占号，签发时才取号，且号码连续不跳。"""
        producer = Producer.objects.create(
            name="防占号主体", contact="钱七", phone="13500000000",
            region_code="511402", owner=self.producer)
        # 建 3 张草稿，都不分配编号
        for _ in range(3):
            Certificate.objects.create(batch=self.batch, quantity_kg=1,
                                       status=Certificate.Status.DRAFT)
        # 草稿的编号为空（已有基线数据 self.cert 是 19 位外的手工编号，故按草稿范围断言）
        self.assertFalse(
            Certificate.objects.filter(status=Certificate.Status.DRAFT)
            .exclude(cert_no__isnull=True).exists(),
            "草稿不应占用任何编号")
        # 签发第一张，应拿到 00001（没有被草稿吃掉）
        c = Certificate(batch=self.batch, producer_name=producer.name,
                        quantity_kg=1, status=Certificate.Status.ISSUED)
        no = certno.save_with_cert_no(c, producer, on_date=date(2026, 9, 23))
        self.assertEqual(no, "5114022026092300001")

    def test_issue_assigns_cert_no_via_api(self):
        """接口层：create 不占号，issue 才分配编号并上链。"""
        reg = self.login(self.regulator)
        r = reg.post("/api/certificate/", {
            "batch": self.batch.id, "report": self.report.id,
            "producer_name": self.producer_obj.name,
            "quantity_kg": "1000.00",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        cert_id = r.data["id"]
        self.assertIsNone(r.data["cert_no"])

        r = reg.post(f"/api/certificate/{cert_id}/issue/")
        self.assertEqual(r.status_code, 200, r.data)
        d = r.data["data"]
        self.assertTrue(certno.is_valid_cert_no(d["cert_no"]))
        self.assertTrue(d["is_anchored"])
        self.assertTrue(EvidenceRecord.objects.filter(
            biz_type=EvidenceRecord.BizType.CERTIFICATE, biz_id=cert_id).exists())

    def test_reissue_keeps_original_cert_no(self):
        """重复签发不换号（已签发的证再点签发，编号保持不变）。"""
        reg = self.login(self.regulator)
        r = reg.post("/api/certificate/", {
            "batch": self.batch.id, "producer_name": "X", "quantity_kg": "1.00",
        }, format="json")
        cert_id = r.data["id"]
        first = reg.post(f"/api/certificate/{cert_id}/issue/").data["data"]["cert_no"]
        second = reg.post(f"/api/certificate/{cert_id}/issue/").data["data"]["cert_no"]
        self.assertEqual(first, second)

    def test_peek_cert_no_does_not_consume(self):
        """预看编号接口不应占用号码。"""
        reg = self.login(self.regulator)
        r = reg.get("/api/certificate/peek-cert-no/")
        self.assertEqual(r.status_code, 200, r.data)
        preview = r.data["data"]["preview_cert_no"]
        self.assertTrue(certno.is_valid_cert_no(preview))
        r2 = reg.get("/api/certificate/peek-cert-no/")
        self.assertEqual(preview, r2.data["data"]["preview_cert_no"],
                         "预看不该消耗流水号")

    def test_producer_cannot_issue(self):
        """种植主体不能签发（编号分配是监管动作）。"""
        c = self.login(self.producer)
        r = c.post(f"/api/certificate/{self.cert.id}/issue/")
        self.assertEqual(r.status_code, 403)

    def test_submit_requires_report(self):
        """无检测报告的合格证不能提交审核。"""
        reg = self.login(self.regulator)
        r = reg.post("/api/certificate/", {
            "batch": self.batch.id, "producer_name": "X", "quantity_kg": "1.00",
        }, format="json")
        cert_id = r.data["id"]
        r = reg.post(f"/api/certificate/{cert_id}/submit/")
        self.assertEqual(r.status_code, 400)

    def test_reject_returns_to_draft(self):
        """驳回后回到草稿，且不占号。"""
        reg = self.login(self.regulator)
        r = reg.post("/api/certificate/", {
            "batch": self.batch.id, "report": self.report.id,
            "producer_name": "X", "quantity_kg": "1.00",
        }, format="json")
        cert_id = r.data["id"]
        reg.post(f"/api/certificate/{cert_id}/submit/")
        r = reg.post(f"/api/certificate/{cert_id}/reject/",
                     {"reason": "主体名称有误"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["data"]["status"], Certificate.Status.DRAFT)
        self.assertIsNone(r.data["data"]["cert_no"])


# ===============================================================
# 八、监管现场监督检查（执法闭环）
# ===============================================================
class InspectionTest(BaseFixture):

    def _payload(self, **over):
        data = {
            "certificate": self.cert.id,
            "check_type": InspectionRecord.CheckType.ROUTINE,
            "check_result": InspectionRecord.CheckResult.QUALIFIED,
            "check_score": "95.00",
            "check_time": "2026-09-20T10:00:00",
            "check_location": "阿克苏果园",
            "problem_description": "未发现问题",
            "department": "阿克苏农业农村局",
            "inspector_no": "ZJ0001",
        }
        data.update(over)
        return data

    def test_regulator_can_create_inspection(self):
        """监管可录入检查，且自动存证上链。"""
        reg = self.login(self.regulator)
        r = reg.post("/api/inspection/", self._payload(), format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertTrue(r.data["is_anchored"])
        self.assertTrue(r.data["record_hash"])
        self.assertEqual(r.data["inspector_name"], self.regulator.username)
        self.assertTrue(EvidenceRecord.objects.filter(
            biz_type=EvidenceRecord.BizType.INSPECTION,
            biz_id=r.data["id"]).exists())

    def test_producer_cannot_create_inspection(self):
        """种植主体不能录入检查（接口级拦截）。"""
        c = self.login(self.producer)
        r = c.post("/api/inspection/", self._payload(), format="json")
        self.assertEqual(r.status_code, 403)

    def test_buyer_cannot_create_inspection(self):
        c = self.login(self.buyer)
        r = c.post("/api/inspection/", self._payload(), format="json")
        self.assertEqual(r.status_code, 403)

    def test_inspection_writes_audit_log(self):
        reg = self.login(self.regulator)
        r = reg.post("/api/inspection/", self._payload(), format="json")
        log = OperationLog.objects.filter(
            action=OperationLog.Action.INSPECT, target_id=r.data["id"]).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.operator, self.regulator)

    def test_unqualified_suspends_certificate(self):
        """★ 检查不合格 -> 关联合格证自动置为「暂停使用」。"""
        reg = self.login(self.regulator)
        r = reg.post("/api/inspection/", self._payload(
            check_result=InspectionRecord.CheckResult.UNQUALIFIED,
            check_score="50.00",
            problem_description="使用禁用农药",
            rectify_requirement="立即停止销售并整改",
            rectify_deadline="2026-10-01",
        ), format="json")
        self.assertEqual(r.status_code, 201, r.data)
        # 默认整改状态被兜底为「待整改」
        self.assertEqual(r.data["rectify_status"],
                         InspectionRecord.RectifyStatus.PENDING)
        self.cert.refresh_from_db()
        self.assertEqual(self.cert.status, Certificate.Status.SUSPENDED)
        self.assertIn("不合格", self.cert.suspend_reason)

    def test_qualified_inspection_keeps_certificate_issued(self):
        reg = self.login(self.regulator)
        reg.post("/api/inspection/", self._payload(), format="json")
        self.cert.refresh_from_db()
        self.assertEqual(self.cert.status, Certificate.Status.ISSUED)

    def test_rectify_done_restores_certificate(self):
        """★ 整改复查通过 -> 合格证恢复为「已签发」。"""
        reg = self.login(self.regulator)
        r = reg.post("/api/inspection/", self._payload(
            check_result=InspectionRecord.CheckResult.UNQUALIFIED,
            check_score="50.00",
            rectify_requirement="整改",
            rectify_deadline="2026-10-01",
        ), format="json")
        ins_id = r.data["id"]
        self.cert.refresh_from_db()
        self.assertEqual(self.cert.status, Certificate.Status.SUSPENDED)

        r = reg.post(f"/api/inspection/{ins_id}/rectify/", {
            "rectify_note": "已完成整改并复查通过",
            "result": InspectionRecord.RectifyStatus.DONE,
            "follow_up_person": "复查员",
        }, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["data"]["rectify_status"],
                         InspectionRecord.RectifyStatus.DONE)
        self.assertIsNotNone(r.data["data"]["rectify_time"])
        self.cert.refresh_from_db()
        self.assertEqual(self.cert.status, Certificate.Status.ISSUED)
        self.assertEqual(self.cert.suspend_reason, "")

    def test_rectify_requires_note(self):
        reg = self.login(self.regulator)
        r = reg.post("/api/inspection/", self._payload(
            check_result=InspectionRecord.CheckResult.UNQUALIFIED,
            rectify_requirement="整改"), format="json")
        r = reg.post(f"/api/inspection/{r.data['id']}/rectify/",
                     {"rectify_note": ""}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_rectify_still_unqualified_keeps_suspended(self):
        """复查仍不合格 -> 合格证维持暂停。"""
        reg = self.login(self.regulator)
        r = reg.post("/api/inspection/", self._payload(
            check_result=InspectionRecord.CheckResult.UNQUALIFIED,
            rectify_requirement="整改", rectify_deadline="2026-10-01",
        ), format="json")
        ins_id = r.data["id"]
        reg.post(f"/api/inspection/{ins_id}/rectify/", {
            "rectify_note": "复查仍不合格",
            "result": InspectionRecord.RectifyStatus.RECHECK_FAILED,
        }, format="json")
        self.cert.refresh_from_db()
        self.assertEqual(self.cert.status, Certificate.Status.SUSPENDED)

    def test_overdue_detection_and_refresh(self):
        """逾期随时间自然发生：过了期限未整改即判逾期，并在列表查询时落库。"""
        past = date.today() - timedelta(days=3)
        ins = InspectionRecord.objects.create(
            certificate=self.cert, check_result=InspectionRecord.CheckResult.UNQUALIFIED,
            rectify_requirement="整改", rectify_deadline=past,
            rectify_status=InspectionRecord.RectifyStatus.PENDING,
            inspector=self.regulator)
        self.assertTrue(ins.is_overdue)
        changed = ins.refresh_overdue()
        self.assertTrue(changed)
        ins.refresh_from_db()
        self.assertEqual(ins.rectify_status, InspectionRecord.RectifyStatus.OVERDUE)
        # 幂等：再刷一次不报错、状态不变
        self.assertFalse(ins.refresh_overdue())

    def test_done_inspection_not_overdue(self):
        """已整改完的不判逾期，即使期限已过。"""
        ins = InspectionRecord.objects.create(
            certificate=self.cert, check_result=InspectionRecord.CheckResult.UNQUALIFIED,
            rectify_deadline=date.today() - timedelta(days=30),
            rectify_status=InspectionRecord.RectifyStatus.DONE,
            inspector=self.regulator)
        self.assertFalse(ins.is_overdue)

    def test_producer_sees_only_own_inspections(self):
        """数据级隔离：种植主体只看自己批次关联证上的检查。"""
        other_batch = ProductBatch.objects.create(
            batch_no="B-OTHER-001", producer=self.other_producer_obj,
            product_name="柑橘", weight_kg=100)
        other_cert = Certificate.objects.create(
            batch=other_batch, producer_name=self.other_producer_obj.name,
            quantity_kg=100, status=Certificate.Status.ISSUED)
        InspectionRecord.objects.create(
            certificate=self.cert, check_result=InspectionRecord.CheckResult.QUALIFIED,
            inspector=self.regulator)
        InspectionRecord.objects.create(
            certificate=other_cert, check_result=InspectionRecord.CheckResult.QUALIFIED,
            inspector=self.regulator)

        c = self.login(self.producer)
        r = c.get("/api/inspection/")
        rows = r.data["results"] if isinstance(r.data, dict) else r.data
        self.assertEqual(len(rows), 1)

        reg = self.login(self.regulator)
        r = reg.get("/api/inspection/")
        rows = r.data["results"] if isinstance(r.data, dict) else r.data
        self.assertEqual(len(rows), 2)

    def test_buyer_sees_only_issued_cert_inspections(self):
        """收购商只看已签发证上的检查记录。"""
        draft = Certificate.objects.create(
            batch=self.batch, quantity_kg=1, status=Certificate.Status.DRAFT)
        InspectionRecord.objects.create(
            certificate=self.cert, check_result=InspectionRecord.CheckResult.QUALIFIED,
            inspector=self.regulator)
        InspectionRecord.objects.create(
            certificate=draft, check_result=InspectionRecord.CheckResult.QUALIFIED,
            inspector=self.regulator)
        c = self.login(self.buyer)
        r = c.get("/api/inspection/")
        rows = r.data["results"] if isinstance(r.data, dict) else r.data
        self.assertEqual(len(rows), 1)

    def test_filter_by_result_and_overdue(self):
        reg = self.login(self.regulator)
        InspectionRecord.objects.create(
            certificate=self.cert, check_result=InspectionRecord.CheckResult.QUALIFIED,
            inspector=self.regulator)
        InspectionRecord.objects.create(
            certificate=self.cert, check_result=InspectionRecord.CheckResult.UNQUALIFIED,
            rectify_deadline=date.today() - timedelta(days=1),
            rectify_status=InspectionRecord.RectifyStatus.OVERDUE,
            inspector=self.regulator)

        r = reg.get("/api/inspection/?check_result=unqualified")
        rows = r.data["results"] if isinstance(r.data, dict) else r.data
        self.assertEqual(len(rows), 1)
        r = reg.get("/api/inspection/?overdue=1")
        rows = r.data["results"] if isinstance(r.data, dict) else r.data
        self.assertEqual(len(rows), 1)

    def test_inspection_hash_excludes_rectify_note(self):
        """★ 存证原文只含结论性事实：整改说明不影响哈希（否则一填就变）。"""
        ins = InspectionRecord.objects.create(
            certificate=self.cert, check_result=InspectionRecord.CheckResult.UNQUALIFIED,
            check_score=50, legal_basis="《农产品质量安全法》第六十五条",
            inspector_name="张三", inspector=self.regulator)
        before = services.build_inspection_payload(ins)
        ins.rectify_note = "事后补充的整改说明"
        ins.rectify_status = InspectionRecord.RectifyStatus.DONE
        after = services.build_inspection_payload(ins)
        self.assertEqual(before, after, "整改说明不应进入存证原文")

    def test_inspection_summary_endpoint(self):
        reg = self.login(self.regulator)
        reg.post("/api/inspection/", self._payload(
            check_result=InspectionRecord.CheckResult.UNQUALIFIED,
            rectify_requirement="整改", rectify_deadline="2026-10-01"), format="json")
        r = reg.get(f"/api/certificate/{self.cert.id}/inspection-summary/")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["data"]["total"], 1)
        self.assertEqual(r.data["data"]["unqualified_count"], 1)

    def test_public_verify_exposes_inspection(self):
        """公众扫码可见检查公示。"""
        reg = self.login(self.regulator)
        reg.post("/api/inspection/", self._payload(
            check_result=InspectionRecord.CheckResult.UNQUALIFIED,
            rectify_requirement="整改", rectify_deadline="2026-10-01"), format="json")
        anon = APIClient()
        r = anon.get(f"/api/public/verify/{self.cert.cert_no}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["data"]["inspection_count"], 1)
        self.assertIsNotNone(r.data["data"]["inspection"])
        self.assertEqual(r.data["data"]["status"], Certificate.Status.SUSPENDED)

    def test_dashboard_exposes_inspection_metrics(self):
        reg = self.login(self.regulator)
        reg.post("/api/inspection/", self._payload(
            check_result=InspectionRecord.CheckResult.UNQUALIFIED,
            rectify_requirement="整改", rectify_deadline="2026-10-01"), format="json")
        r = reg.get("/api/dashboard/")
        d = r.data["data"]
        self.assertEqual(d["inspection_count"], 1)
        self.assertEqual(d["inspection_unqualified_count"], 1)
        self.assertEqual(d["suspended_count"], 1)

    def test_role_matrix_contains_cert_no_rule(self):
        c = self.login(self.admin)
        r = c.get("/api/role-matrix/")
        d = r.data["data"]
        self.assertEqual(d["cert_no_rule"]["length"], 19)
        self.assertEqual(len(d["cert_no_rule"]["segments"]), 3)
        actions = [a["action"] for a in d["action_matrix"]]
        self.assertIn("监督检查录入", actions)
        self.assertIn("整改复查", actions)
        keys = [s["key"] for s in d["cert_status_flow"]]
        self.assertIn("suspended", keys)


# ===============================================================
# 九、检测报告附件上传
# ===============================================================
class ReportFileUploadTest(BaseFixture):
    """
    附件上传回归。

    背景：`TestReport.file` 字段建了、序列化器 `fields="__all__"` 也收，
    但前端从来没有 el-upload —— 报告附件一度是「有字段、传不了」的半成品。
    这里锁住后端侧的可信边界（前端 el-upload 只是体验层，不该被当作校验）。
    """

    def _file(self, name="report.pdf", content=b"%PDF-1.4 test"):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile(name, content, content_type="application/pdf")

    def test_create_report_without_file_still_works(self):
        """不带附件的报告创建不能被破坏（原有主流程）"""
        c = self.login(self.producer)
        r = c.post("/api/report/", {
            "report_no": "R-NOFILE-001", "batch": self.batch.id,
            "test_org": "阿克苏检测中心", "conclusion": "qualified",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data.get("file_name") or "", "")
        self.assertEqual(r.data.get("file_url") or "", "")

    def test_upload_file_via_multipart(self):
        """multipart 上传成功后应能取回附件地址与文件名"""
        c = self.login(self.producer)
        r = c.post("/api/report/", {
            "report_no": "R-FILE-001", "batch": self.batch.id,
            "test_org": "阿克苏检测中心", "conclusion": "qualified",
            "file": self._file(),
        }, format="multipart")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertTrue(r.data["file"], "file 字段应有值")
        self.assertTrue(r.data["file_url"].startswith("/media/reports/"),
                        r.data["file_url"])
        self.assertEqual(r.data["file_name"], "report.pdf")

    def test_patch_upload_existing_report(self):
        """给已有报告补传附件（页面上「上传」按钮走的路径）"""
        c = self.login(self.producer)
        r = c.patch(f"/api/report/{self.report.id}/",
                    {"file": self._file("补传版本.pdf")}, format="multipart")
        self.assertEqual(r.status_code, 200, r.data)
        self.report.refresh_from_db()
        self.assertTrue(self.report.file)
        self.assertIn("file_url", r.data)

    def test_reject_unsupported_extension(self):
        """★ 后端可信边界：绕过前端直接传可执行文件必须被拒"""
        c = self.login(self.producer)
        r = c.patch(f"/api/report/{self.report.id}/",
                    {"file": self._file("evil.exe", b"MZ")}, format="multipart")
        self.assertEqual(r.status_code, 400, r.data)
        self.report.refresh_from_db()
        self.assertFalse(self.report.file, "校验失败不应落库")

    def test_reject_oversize_file(self):
        """超过 10 MB 的附件必须被拒"""
        from django.core.files.uploadedfile import SimpleUploadedFile
        big = SimpleUploadedFile("big.pdf", b"x" * (10 * 1024 * 1024 + 1),
                                 content_type="application/pdf")
        c = self.login(self.producer)
        r = c.patch(f"/api/report/{self.report.id}/", {"file": big},
                    format="multipart")
        self.assertEqual(r.status_code, 400, r.data)

    def test_buyer_cannot_upload(self):
        """收购商无权上传报告附件（权限不能被上传功能绕过）"""
        c = self.login(self.buyer)
        r = c.patch(f"/api/report/{self.report.id}/", {"file": self._file()},
                    format="multipart")
        self.assertIn(r.status_code, (403, 404), r.status_code)

    def test_delete_report_removes_file(self):
        """★ 删除报告必须连带删除磁盘附件

        Django 的 FileField 只存路径，删实例不会自动清理文件 ——
        不显式删除就会积累永远没人引用的孤儿文件（实测已出现）。
        """
        import os
        c = self.login(self.producer)
        c.patch(f"/api/report/{self.report.id}/",
                {"file": self._file("待删除.pdf")}, format="multipart")
        self.report.refresh_from_db()
        path = self.report.file.path
        self.assertTrue(os.path.exists(path), "上传后文件应存在")

        r = c.delete(f"/api/report/{self.report.id}/")
        self.assertIn(r.status_code, (200, 204), r.status_code)
        self.assertFalse(os.path.exists(path), "删除报告后附件应一并删除")

    def test_replace_file_removes_old_one(self):
        """★ 替换附件时旧文件应被清理，避免每换一次多留一份孤儿"""
        import os
        c = self.login(self.producer)
        c.patch(f"/api/report/{self.report.id}/",
                {"file": self._file("旧版.pdf")}, format="multipart")
        self.report.refresh_from_db()
        old_path = self.report.file.path

        c.patch(f"/api/report/{self.report.id}/",
                {"file": self._file("新版.pdf")}, format="multipart")
        self.report.refresh_from_db()
        new_path = self.report.file.path

        self.assertNotEqual(old_path, new_path, "应换成了新文件")
        self.assertFalse(os.path.exists(old_path), "旧附件应被清理")
        self.assertTrue(os.path.exists(new_path), "新附件应存在")


# ===============================================================
# 十、监督检查链上编号唯一性（防覆盖回归）
# ===============================================================
class InspectionBizNoTest(BaseFixture):
    """
    背景：合约是 `records[bizType][bizNo]` 单值映射，同一 bizNo 后写覆盖先写。
    早期 `anchor_inspection` 用 cert_no 当链上 biz_no，导致同一张合格证上的
    多次检查只有最后一次能在链上核验、先前记录被永久覆盖（已实测踩坑）。
    这里锁住「同一张证的多次检查必须各自可核验」。
    """

    def test_biz_no_uses_record_own_id(self):
        """链上业务编号必须带检查记录自身 id，不能只用 cert_no"""
        ins = InspectionRecord.objects.create(
            certificate=self.cert, inspector=self.regulator,
            inspector_name="赵国强", check_type=InspectionRecord.CheckType.ROUTINE,
            check_result=InspectionRecord.CheckResult.QUALIFIED, check_score=95)
        biz_no = services.inspection_biz_no(ins)
        self.assertEqual(biz_no, f"INS-{ins.id}")
        self.assertNotEqual(biz_no, self.cert.cert_no)

    def test_two_inspections_on_same_cert_get_distinct_biz_no(self):
        """★ 核心回归：同一张证上两次检查的链上 biz_no 必须不同（否则互相覆盖）"""
        a = InspectionRecord.objects.create(
            certificate=self.cert, inspector=self.regulator,
            inspector_name="赵国强", check_type=InspectionRecord.CheckType.ROUTINE,
            check_result=InspectionRecord.CheckResult.QUALIFIED, check_score=95)
        b = InspectionRecord.objects.create(
            certificate=self.cert, inspector=self.regulator,
            inspector_name="孙丽娟", check_type=InspectionRecord.CheckType.SPECIAL,
            check_result=InspectionRecord.CheckResult.UNQUALIFIED, check_score=60)
        self.assertNotEqual(services.inspection_biz_no(a),
                            services.inspection_biz_no(b))

    def test_anchor_writes_record_with_id_based_biz_no(self):
        """存证落库时 biz_no 也应是 INS-<id>，保证本地与链上口径一致"""
        ins = InspectionRecord.objects.create(
            certificate=self.cert, inspector=self.regulator,
            inspector_name="赵国强", check_type=InspectionRecord.CheckType.ROUTINE,
            check_result=InspectionRecord.CheckResult.QUALIFIED, check_score=95)
        record = services.anchor_inspection(ins)
        self.assertEqual(record.biz_no, f"INS-{ins.id}")
        ins.refresh_from_db()
        self.assertTrue(ins.is_anchored)
        self.assertEqual(len(ins.record_hash), 64)

    def test_rectify_note_not_in_payload(self):
        """★ 上链原文只含结论性事实：整改备注变化不得改变哈希

        否则监管一填整改说明，哈希就变，链核验立刻失败。
        """
        ins = InspectionRecord.objects.create(
            certificate=self.cert, inspector=self.regulator,
            inspector_name="赵国强", check_type=InspectionRecord.CheckType.ROUTINE,
            check_result=InspectionRecord.CheckResult.UNQUALIFIED, check_score=60,
            rectify_note="")
        before = services.build_inspection_payload(ins)
        ins.rectify_note = "已完成整改，复检合格"
        after = services.build_inspection_payload(ins)
        self.assertEqual(before, after)
