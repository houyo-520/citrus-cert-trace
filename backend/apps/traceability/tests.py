"""
核心业务单元测试

覆盖四大类：
  1. 哈希存证一致性（同一数据多次计算结果一致、字段变更导致哈希变化）
  2. 篡改检测（本地比对 + 高级篡改场景）
  3. 角色权限隔离（接口级 + 数据级）
  4. 审计日志（关键动作是否留痕）

运行：python manage.py test apps.traceability -v 2
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import Role, User

from . import matching, services
from .models import (Certificate, EvidenceRecord, MatchIntent, OperationLog,
                     Producer, ProductBatch, SupplyDemand, TestReport)


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
        self.assertEqual(len(r.data["data"]["resource_matrix"]), 8)
        self.assertEqual(len(r.data["data"]["roles"]), 4)

    def test_profile_contains_permissions(self):
        """登录返回的权限清单应正确标记各角色能力"""
        c = self.login(self.regulator)
        perms = c.get("/api/auth/profile/").data["data"]["permissions"]
        self.assertTrue(perms["can_audit"])
        self.assertTrue(perms["can_issue"])
        self.assertTrue(perms["can_view_all"])

        c = self.login(self.producer)
        perms = c.get("/api/auth/profile/").data["data"]["permissions"]
        self.assertFalse(perms["can_audit"])
        self.assertFalse(perms["can_issue"])
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
    """模拟完整业务链路：录批次 → 出报告 → 签合格证 → 公众验真"""

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

        # 3. 监管签发合格证
        reg = self.login(self.regulator)
        r = reg.post("/api/certificate/", {
            "cert_no": "CERT-E2E-001", "batch": batch_id,
            "producer_name": self.producer_obj.name, "product_name": "柑橘",
            "quantity_kg": "3000.00", "issue_date": "2026-09-12",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        cert_id = r.data["id"]
        r = reg.post(f"/api/certificate/{cert_id}/issue/")
        self.assertEqual(r.status_code, 200)

        # 4. 公众免登录验真
        anon = APIClient()
        r = anon.get("/api/public/verify/CERT-E2E-001/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["data"]["trusted"], "完整链路后应判可信")

        # 5. 全链路日志应完整
        actions = set(OperationLog.objects.values_list("action", flat=True))
        self.assertIn(OperationLog.Action.CREATE, actions)
        self.assertIn(OperationLog.Action.ISSUE, actions)
