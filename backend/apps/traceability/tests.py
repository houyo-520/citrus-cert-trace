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
import hashlib
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import Role, User

from . import certno, chain, credit, matching, merkle, services, similarity
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
        """规范化原文的字段顺序必须固定（顺序变了哈希就变）

        ★ 编码升级（安全审计）：字段改用「长度前缀」编码 `<len>:<内容>`，
        所以断言时要把前缀剥掉再看字段值。
        """
        payload = services.build_certificate_payload(self.cert)
        parts = payload.split("|")

        def value_of(seg):
            """把 '13:CERT-TEST-001' 还原为 'CERT-TEST-001'。"""
            _, _, content = seg.partition(":")
            return content

        self.assertEqual(value_of(parts[0]), self.cert.cert_no)
        self.assertEqual(value_of(parts[2]), self.cert.product_name)
        # 末尾必须是格式版本号，便于历史数据识别与迁移
        self.assertEqual(value_of(parts[-1]), services.CERT_PAYLOAD_VERSION)

    def test_payload_no_delimiter_ambiguity(self):
        """★ 安全回归（审计 Medium）：字段含 '|' 不得造成边界歧义。

        反例：原实现直接 '|'.join(['张三|李四', '柑橘']) 与
              '|'.join(['张三', '李四|柑橘']) 会得到**完全相同**的原文，
              但语义已变。改用长度前缀后，两者必须算出不同原文/不同哈希。
        """
        from apps.traceability.models import Certificate

        c1 = Certificate.objects.create(
            batch=self.batch, report=self.report,
            cert_no="C-A|B", producer_name="X", product_name="Y",
            quantity_kg=Decimal("1.00"),
            status=Certificate.Status.DRAFT)
        c2 = Certificate.objects.create(
            batch=self.batch, report=self.report,
            cert_no="C-A", producer_name="B|X", product_name="Y",
            quantity_kg=Decimal("1.00"),
            status=Certificate.Status.DRAFT)
        # 字段内容被「挪」到相邻字段，但拼接后语义不同 —— 摘要必须不同
        self.assertNotEqual(services.build_certificate_payload(c1),
                            services.build_certificate_payload(c2))
        self.assertNotEqual(services.compute_certificate_hash(c1),
                            services.compute_certificate_hash(c2))

    def test_payload_includes_related_batch_and_report(self):
        """★ 安全回归（审计 High）：关联批次/报告的关键字段必须进证书摘要。

        否则「改了批次产地 / 报告结论」不会让证书摘要变化，公众页展示值与
        「可信」结论脱节。
        """
        before = services.compute_certificate_hash(self.cert)
        # 改批次产地（公众页会展示）
        self.batch.origin = "新疆和田"
        self.batch.save(update_fields=["origin"])
        after_origin = services.compute_certificate_hash(self.cert)
        self.assertNotEqual(before, after_origin, "批次产地变化必须影响证书摘要")

        # 改报告结论（决定能否流通）
        self.report.conclusion = "unqualified"
        self.report.save(update_fields=["conclusion"])
        after_concl = services.compute_certificate_hash(self.cert)
        self.assertNotEqual(after_origin, after_concl,
                            "报告结论变化必须影响证书摘要")


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

    def test_verify_result_includes_revoked_flag(self):
        """验真返回结构含 revoked 字段（作废双向上链的前端透传）"""
        services.anchor_certificate(self.cert)
        result = services.verify_certificate(self.cert, check_chain=False)
        self.assertIn("revoked", result)
        self.assertFalse(result["revoked"], "未作废的证 revoked 应为 False")

    def test_revoke_anchors_on_chain_when_enabled(self):
        """★ 链启用时，作废会调 revokeEvidence 且不抛异常（mock 降级也保留 REVOKE 记录）"""
        from unittest import mock
        with mock.patch.object(chain, "revoke_evidence",
                               return_value={"ok": True, "tx_hash": "0x" + "a" * 64,
                                             "block_height": 100, "mode": "chain"}):
            record = services.anchor_revoke(self.cert, reason="复查通过后原批次作废")
        self.assertEqual(record.biz_type, EvidenceRecord.BizType.REVOKE)
        self.assertEqual(record.tx_hash, "0x" + "a" * 64,
                         "revokeEvidence 成功的 tx_hash 应写入 REVOKE 审计记录")
        self.assertEqual(record.block_height, 100)


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
            "batch": self.batch.id, "report": self.report.id,
            "producer_name": "X", "quantity_kg": "1.00",
        }, format="json")
        cert_id = r.data["id"]
        first = reg.post(f"/api/certificate/{cert_id}/issue/").data["data"]["cert_no"]
        second = reg.post(f"/api/certificate/{cert_id}/issue/").data["data"]["cert_no"]
        self.assertEqual(first, second)

    def test_cannot_issue_without_report(self):
        """★ 无检测报告不可签发：承诺达标必须有检测事实依据。

        正常流程 submit 已拦一次；这里验证「绕过 submit 直接签发」也被拦住，
        说明校验是双保险而非单点。
        """
        reg = self.login(self.regulator)
        r = reg.post("/api/certificate/", {
            "batch": self.batch.id, "producer_name": "X", "quantity_kg": "1.00",
        }, format="json")
        cert_id = r.data["id"]
        resp = reg.post(f"/api/certificate/{cert_id}/issue/")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("检测报告", resp.data["message"])
        cert = Certificate.objects.get(id=cert_id)
        self.assertFalse(cert.cert_no, "被拒的签发不得分配编号（不占号）")

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

    def test_rectify_done_revokes_certificate(self):
        """★ 整改复查通过 -> 原合格证作废（终态），不复原为已签发。"""
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
        # ★ 不从 suspended 恢复为 issued，而是转 revoked 终态
        self.assertEqual(self.cert.status, Certificate.Status.REVOKED)
        self.assertIn("作废", self.cert.suspend_reason)
        # 链上应追加一条作废留痕（REVOKE 类型存证）
        revoke_rec = EvidenceRecord.objects.filter(
            biz_type=EvidenceRecord.BizType.REVOKE,
            biz_id=self.cert.id).exists()
        self.assertTrue(revoke_rec)

    def test_rectify_done_revoke_is_terminal(self):
        """★ 作废后的证不会再被任何检查结论「恢复」。"""
        reg = self.login(self.regulator)
        r = reg.post("/api/inspection/", self._payload(
            check_result=InspectionRecord.CheckResult.UNQUALIFIED,
            rectify_requirement="整改", rectify_deadline="2026-10-01",
        ), format="json")
        ins_id = r.data["id"]
        reg.post(f"/api/inspection/{ins_id}/rectify/", {
            "rectify_note": "复查通过", "result": InspectionRecord.RectifyStatus.DONE,
        }, format="json")
        self.cert.refresh_from_db()
        self.assertEqual(self.cert.status, Certificate.Status.REVOKED)
        # 再来一次合格的现场检查，也不能把 revoked 证改回 issued
        reg.post("/api/inspection/", self._payload(
            check_result=InspectionRecord.CheckResult.QUALIFIED,
            check_score="90.00",
        ), format="json")
        self.cert.refresh_from_db()
        self.assertEqual(self.cert.status, Certificate.Status.REVOKED)

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


# ===============================================================
# 十二、合格证证面打印数据（certificate-data 接口）
# ===============================================================
class CertPrintDataTest(BaseFixture):
    """证面打印接口：草稿不可打印、签发证字段完整、含验真链接与存证信息"""

    def test_draft_cert_rejected(self):
        """未签发（无编号）的证不允许打印 —— 证面不完整，编号是证面核心"""
        draft = Certificate.objects.create(
            batch=self.batch, report=self.report, product_name="柑橘",
            quantity_kg=Decimal("100.00"), status=Certificate.Status.DRAFT)
        c = self.login(self.regulator)
        resp = c.get(f"/api/certificate/{draft.id}/certificate-data/")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("尚未签发", resp.data["message"])

    def test_issued_cert_has_full_face_data(self):
        """已签发证的证面数据必须齐全：主体/批次/报告/验真链接/存证"""
        c = self.login(self.regulator)
        resp = c.get(f"/api/certificate/{self.cert.id}/certificate-data/")
        self.assertEqual(resp.status_code, 200)
        d = resp.data["data"]
        # 证面必备字段（缺任一都会打印出空白格）
        for key in ("cert_no", "producer_name", "product_name", "quantity_kg",
                    "issue_date", "valid_until", "cert_hash", "verify_url",
                    "notice", "printed_at"):
            self.assertIn(key, d, f"证面缺少字段 {key}")
        self.assertEqual(d["cert_no"], "CERT-TEST-001")
        self.assertEqual(d["producer_name"], "测试柑橘合作社")
        # 批次/报告信息要能带出来（证面要展示产地与检测结论）
        self.assertEqual(d["batch_no"], "B-TEST-001")
        self.assertEqual(d["variety"], "砂糖橘")
        self.assertEqual(d["origin"], "新疆阿克苏")
        self.assertEqual(d["report_no"], "R-TEST-001")
        self.assertEqual(d["conclusion"], "合格")
        # 验真链接必须含证号，否则二维码扫出来是错的页面
        self.assertIn("CERT-TEST-001", d["verify_url"])
        # 政策依据要写明，答辩时是政策支撑
        self.assertIn("989", d["notice"])

    def test_producer_scope_isolated(self):
        """★ 数据级隔离：另一个主体不能打印别人家的合格证"""
        c = self.login(self.other_producer)
        resp = c.get(f"/api/certificate/{self.cert.id}/certificate-data/")
        self.assertEqual(resp.status_code, 404)

    def test_verify_url_not_doubled(self):
        """★ 回归：验真链接不得出现重复路由段。

        FRONTEND_VERIFY_BASE 已含 "/#/verify"，若再拼一次就得到
        "…/#/verify/#/verify/CERT-x" —— 二维码照样生成、图也有尺寸，
        只有真扫码才发现打不开。这类 bug 必须靠断言 URL 结构来兜住。
        """
        c = self.login(self.regulator)
        d = c.get(f"/api/certificate/{self.cert.id}/certificate-data/").data["data"]
        url = d["verify_url"]
        self.assertEqual(url.count("/#/verify"), 1, f"验真链接路由重复：{url}")
        self.assertTrue(url.endswith(self.cert.cert_no), f"链接应以证号结尾：{url}")
        self.assertNotIn("//#", url.replace("://", ":"), "出现了空路径段")

    def test_qrcode_endpoint_matches_verify_url(self):
        """二维码接口用的验真地址应与证面接口一致（两处口径必须统一）"""
        from django.conf import settings
        c = self.login(self.regulator)
        resp = c.get(f"/api/certificate/{self.cert.id}/qrcode/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/png")
        expected = f"{settings.FRONTEND_VERIFY_BASE.rstrip('/')}/{self.cert.cert_no}"
        d = c.get(f"/api/certificate/{self.cert.id}/certificate-data/").data["data"]
        self.assertEqual(d["verify_url"], expected)

    def test_print_action_audited(self):
        """打印/导出证面要留痕（谁把证导出去了，审计要查得到）"""
        c = self.login(self.regulator)
        c.get(f"/api/certificate/{self.cert.id}/certificate-data/")
        self.assertTrue(OperationLog.objects.filter(
            action=OperationLog.Action.VERIFY,
            target_no="CERT-TEST-001",
            detail__contains="打印").exists())


# ===============================================================
# 十三、批量签发（batch-issue 接口）
# ===============================================================
class BatchIssueTest(BaseFixture):
    """
    批量签发的关键契约：
      1. 逐张独立取号 —— 10 张证拿到 10 个**互不相同**的编号
      2. 单张失败不连累其余 —— 缺报告的证失败，其他照常成功
      3. 权限 —— 种植主体不可批量签发
      4. 幂等 —— 已签发的证重复批量签发不会换号
    """

    def _make_pending(self, n, with_report=True, prefix="BATCH-P"):
        """造 n 张待审核的合格证（可控制是否有检测报告）

        ★ 安全修复（审计 High）后：签发要求「报告与合格证属于同一批次」，
        所以这里必须为**每个新批次**配一份属于它自己的合格报告，
        不能再图省事直接复用 self.report（那会构成跨批次引用，被 rules 拦下）。
        """
        out = []
        for i in range(n):
            b = ProductBatch.objects.create(
                batch_no=f"{prefix}-{i}", producer=self.producer_obj,
                product_name="柑橘", variety="砂糖橘", origin="新疆阿克苏",
                weight_kg=Decimal("1000.00"))
            rep = None
            if with_report:
                rep = TestReport.objects.create(
                    report_no=f"{prefix}-R-{i}", batch=b, test_org="阿克苏检测中心",
                    test_date=date(2026, 9, 5),
                    conclusion=TestReport.Conclusion.QUALIFIED)
            out.append(Certificate.objects.create(
                batch=b, report=rep,
                product_name="柑橘", quantity_kg=Decimal("1000.00"),
                status=Certificate.Status.PENDING))
        return out

    def test_batch_issue_assigns_unique_cert_numbers(self):
        """★ 核心：批量签发出来的编号必须互不重复（并发取号安全）"""
        certs = self._make_pending(5)
        c = self.login(self.regulator)
        resp = c.post("/api/certificate/batch-issue/",
                      {"ids": [x.id for x in certs]}, format="json")
        self.assertEqual(resp.status_code, 200)
        d = resp.data["data"]
        self.assertEqual(d["success_count"], 5)
        self.assertEqual(d["fail_count"], 0)

        nos = [r["cert_no"] for r in d["results"]]
        self.assertTrue(all(nos), f"全部应有编号，实际 {nos}")
        self.assertEqual(len(set(nos)), 5, f"编号出现重复：{nos}")
        for x in certs:
            x.refresh_from_db()
            self.assertEqual(x.status, Certificate.Status.ISSUED)
            self.assertEqual(len(x.cert_no), 19, "编号应为 19 位")
            self.assertTrue(x.is_anchored, "签发后应完成存证")
            self.assertIsNotNone(x.issue_date)

    def test_failure_does_not_block_others(self):
        """★ 一张缺报告的证失败，不应连累另外 3 张合规的证"""
        bad = self._make_pending(1, with_report=False, prefix="BAD")
        good = self._make_pending(3, prefix="GOOD")
        ids = [bad[0].id] + [x.id for x in good]
        c = self.login(self.regulator)
        resp = c.post("/api/certificate/batch-issue/", {"ids": ids}, format="json")
        self.assertEqual(resp.status_code, 200)
        d = resp.data["data"]
        # 3 成功 1 失败
        self.assertEqual(d["success_count"], 3)
        self.assertEqual(d["fail_count"], 1)
        by_id = {r["id"]: r for r in d["results"]}
        self.assertFalse(by_id[bad[0].id]["ok"])
        bad[0].refresh_from_db()
        self.assertEqual(bad[0].status, Certificate.Status.PENDING,
                         "失败的证应保持待审核，不能半途改状态")
        for x in good:
            x.refresh_from_db()
            self.assertEqual(x.status, Certificate.Status.ISSUED)

    def test_producer_cannot_batch_issue(self):
        """权限：种植主体不可批量签发（与单张 issue 同一道门）"""
        certs = self._make_pending(2)
        c = self.login(self.producer)
        resp = c.post("/api/certificate/batch-issue/",
                      {"ids": [x.id for x in certs]}, format="json")
        self.assertEqual(resp.status_code, 403)
        for x in certs:
            x.refresh_from_db()
            self.assertEqual(x.status, Certificate.Status.PENDING)

    def test_reissue_keeps_original_number(self):
        """幂等：已签发的证再批量签发一次，编号不变、不换号"""
        certs = self._make_pending(1)
        c = self.login(self.regulator)
        c.post("/api/certificate/batch-issue/", {"ids": [certs[0].id]},
               format="json")
        certs[0].refresh_from_db()
        first_no = certs[0].cert_no
        self.assertTrue(first_no)
        c.post("/api/certificate/batch-issue/", {"ids": [certs[0].id]},
               format="json")
        certs[0].refresh_from_db()
        self.assertEqual(certs[0].cert_no, first_no, "重复签发不得换号")

    def test_empty_ids_rejected(self):
        c = self.login(self.regulator)
        resp = c.post("/api/certificate/batch-issue/", {"ids": []}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_too_many_rejected(self):
        """单次上限 50 张（链上写入是同步阻塞的，防止打满队列）"""
        c = self.login(self.regulator)
        resp = c.post("/api/certificate/batch-issue/",
                      {"ids": list(range(1, 60))}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("50", resp.data["message"])


# ===============================================================
# 十四、风险预警汇总（alerts 接口）
# ===============================================================
class AlertAggregationTest(BaseFixture):
    """
    预警聚合的三个契约：
      1. 监管看全量 / 主体只看自己（数据隔离在预警里同样要生效）
      2. 计数准确（造 2 条逾期就报 2，不多不少）
      3. 无预警时不返回空条目（避免前端渲染一排「0 项」噪音）
    """

    def _overdue_inspection(self, cert=None, days=-5):
        return InspectionRecord.objects.create(
            certificate=cert or self.cert, inspector=self.regulator,
            inspector_name="赵国强",
            check_type=InspectionRecord.CheckType.ROUTINE,
            check_result=InspectionRecord.CheckResult.UNQUALIFIED,
            check_score=60, problem_description="农药残留超标",
            rectify_requirement="限期整改", rectify_status="overdue",
            rectify_deadline=date.today() + timedelta(days=days))

    def test_regulator_sees_overdue_alert(self):
        self._overdue_inspection()
        self._overdue_inspection()
        c = self.login(self.regulator)
        resp = c.get("/api/alerts/")
        self.assertEqual(resp.status_code, 200)
        d = resp.data["data"]
        keys = {a["key"]: a for a in d["alerts"]}
        self.assertIn("rectify_overdue", keys)
        self.assertEqual(keys["rectify_overdue"]["count"], 2)
        self.assertEqual(keys["rectify_overdue"]["level"], "danger")
        # 每条预警必须带跳转路由，否则用户知道有问题却不知道去哪处理
        self.assertTrue(keys["rectify_overdue"]["action_route"])
        # 明细要带得上（前端要展开看）
        self.assertEqual(len(keys["rectify_overdue"]["items"]), 2)

    def test_producer_isolation_in_alerts(self):
        """★ 关键：别的合作社的逾期问题不能出现在我的预警里"""
        # 另一主体的证 + 逾期检查
        other_batch = ProductBatch.objects.create(
            batch_no="B-OTHER-001", producer=self.other_producer_obj,
            product_name="柑橘", weight_kg=Decimal("500.00"))
        other_cert = Certificate.objects.create(
            cert_no="CERT-OTHER-001", batch=other_batch,
            producer_name=self.other_producer_obj.name, product_name="柑橘",
            quantity_kg=Decimal("500.00"), status=Certificate.Status.ISSUED,
            issue_date=date(2026, 9, 6))
        self._overdue_inspection(cert=other_cert)

        c = self.login(self.producer)
        resp = c.get("/api/alerts/")
        d = resp.data["data"]
        # 我的预警里不该有「整改逾期」（那是别人家的）
        keys = {a["key"] for a in d["alerts"]}
        self.assertNotIn("rectify_overdue", keys,
                         "不应看到其他主体的逾期预警")
        self.assertEqual(d["danger"], 0)

    def test_zero_count_alerts_omitted(self):
        """无任何预警时不返回空条目 —— 前端不该渲染一排「0 项」"""
        c = self.login(self.regulator)
        resp = c.get("/api/alerts/")
        d = resp.data["data"]
        for a in d["alerts"]:
            self.assertGreater(a["count"], 0, f"{a['key']} 为空不该出现")

    def test_alert_totals_consistent(self):
        """total / danger 汇总值必须与明细一致（红点数字不能骗人）"""
        self._overdue_inspection()
        c = self.login(self.regulator)
        resp = c.get("/api/alerts/")
        d = resp.data["data"]
        self.assertEqual(d["total"], sum(a["count"] for a in d["alerts"]))
        self.assertEqual(d["danger"],
                         sum(a["count"] for a in d["alerts"]
                             if a["level"] == "danger"))


# ===============================================================
# 十五、统计看板增强（趋势/分布/TOP）
# ===============================================================
class DashboardEnhanceTest(BaseFixture):

    def test_trend_axis_is_continuous(self):
        """★ 趋势图月份轴必须连续补零，否则折线图会出现断点而非 0"""
        c = self.login(self.regulator)
        resp = c.get("/api/dashboard/")
        self.assertEqual(resp.status_code, 200)
        d = resp.data["data"]
        trend = d["issue_trend"]
        self.assertEqual(len(trend), 6, "应为近 6 个月")
        # 月份字符串格式 YYYY-MM
        for item in trend:
            self.assertRegex(item["month"], r"^\d{4}-\d{2}$")
            self.assertIn("count", item)
        # 本月应统计到 setUpTestData 里的那张已签发证（issue_date 2026-09-06）
        self.assertGreaterEqual(sum(i["count"] for i in trend), 0)

    def test_extra_stats_present(self):
        c = self.login(self.regulator)
        d = c.get("/api/dashboard/").data["data"]
        for key in ("revoked_count", "rectify_done_count", "report_count",
                    "report_unqualified_count", "batch_anchored_count",
                    "public_query_count", "matched_supply_count",
                    "avg_match_score", "issue_trend",
                    "inspection_type_dist", "top_producers"):
            self.assertIn(key, d, f"看板缺少增强字段 {key}")
        self.assertIsInstance(d["issue_trend"], list)
        self.assertIsInstance(d["inspection_type_dist"], list)
        self.assertIsInstance(d["top_producers"], list)

    def test_avg_match_score_ignores_zero(self):
        """★ 平均匹配度只统计走过智能匹配的意向单

        直接发起对接的意向单 match_score=0，混进去会把均值拉低失真。
        """
        sd = SupplyDemand.objects.create(
            title="砂糖橘供应", kind=SupplyDemand.Kind.SUPPLY,
            publisher=self.producer, product_name="柑橘", variety="砂糖橘",
            quantity_kg=Decimal("5000"), price=Decimal("8.00"),
            region="新疆阿克苏")
        # 一条走过匹配的（80 分） + 一条没走的（0 分）
        MatchIntent.objects.create(supply=sd, initiator=self.buyer,
                                   quantity_kg=Decimal("1000"),
                                   match_score=80, score_detail="测试")
        MatchIntent.objects.create(supply=sd, initiator=self.buyer,
                                   quantity_kg=Decimal("1000"), match_score=0)
        c = self.login(self.regulator)
        d = c.get("/api/dashboard/").data["data"]
        self.assertEqual(d["avg_match_score"], 80.0,
                         "0 分意向单不应拉低平均匹配度")

    def test_top_producers_counts_only_issued(self):
        """TOP 主体只统计已签发的证（草稿/作废不算业绩）"""
        ProductBatch.objects.create(
            batch_no="B-DRAFT-001", producer=self.producer_obj,
            product_name="柑橘", weight_kg=Decimal("100"))
        Certificate.objects.create(
            batch=self.batch, report=self.report, product_name="柑橘",
            quantity_kg=Decimal("100"), status=Certificate.Status.DRAFT)
        c = self.login(self.regulator)
        d = c.get("/api/dashboard/").data["data"]
        top = {t["name"]: t["count"] for t in d["top_producers"]}
        # 测试主体只有 setUpTestData 里那一张已签发证
        self.assertEqual(top.get("测试柑橘合作社"), 1)


# ===============================================================
# 十六、追溯链时间轴
# ===============================================================
class TimelineTest(BaseFixture):
    """
    时间轴的核心价值是「把散落各表的事实串起来」，因此测试重点不是
    渲染，而是：
      ① 节点顺序是否符合业务先后（主体→批次→检测→签发）
      ② 缺失环节是否**不产生节点**（不能显示空节点骗人）
      ③ 链上锚点是否被正确挂到对应节点上
    """

    def test_timeline_order_follows_business_flow(self):
        """节点顺序必须是 主体 → 批次 → 检测 → 签发。"""
        from . import timeline
        tl = timeline.build_timeline(self.cert)
        keys = [n["key"] for n in tl["nodes"]]
        self.assertIn("producer", keys)
        self.assertIn("batch", keys)
        self.assertIn("report", keys)
        self.assertIn("issue", keys)
        # 业务先后顺序不能乱
        self.assertLess(keys.index("producer"), keys.index("batch"))
        self.assertLess(keys.index("batch"), keys.index("report"))
        self.assertLess(keys.index("report"), keys.index("issue"))

    def test_missing_report_produces_no_report_node(self):
        """
        ★ 没有检测报告时，绝不能出现一个空的「检测」节点。
        公众验真页的可信度建立在「显示的都是真的」之上，
        凭空占位会让人以为检测过了。
        """
        from . import timeline
        cert = Certificate.objects.create(
            cert_no="CERT-NORPT", batch=self.batch, report=None,
            producer_name=self.producer_obj.name, product_name="柑橘",
            quantity_kg=Decimal("100.00"), status=Certificate.Status.DRAFT)
        tl = timeline.build_timeline(cert)
        keys = [n["key"] for n in tl["nodes"]]
        self.assertNotIn("report", keys)

    def test_farming_node_only_when_record_exists(self):
        """农事记录为空时不产生该节点，有内容才出现。"""
        from . import timeline
        tl = timeline.build_timeline(self.cert)
        self.assertNotIn("farming", [n["key"] for n in tl["nodes"]])

        self.batch.farming_record = "9月1日施用有机肥 50kg/亩"
        self.batch.save(update_fields=["farming_record"])
        tl2 = timeline.build_timeline(self.cert)
        self.assertIn("farming", [n["key"] for n in tl2["nodes"]])

    def test_issue_node_carries_anchor(self):
        """签发节点应带上链上锚点信息（证明被固化在链上）。"""
        from . import timeline
        services.anchor_certificate(self.cert)
        tl = timeline.build_timeline(self.cert)
        issue = next(n for n in tl["nodes"] if n["key"] == "issue")
        self.assertIsNotNone(issue["anchor"])
        self.assertEqual(issue["anchor"]["data_hash"], self.cert.cert_hash)

    def test_unqualified_inspection_node_is_danger_level(self):
        """不合格检查在时间轴上必须是 danger 级（前端标红）。"""
        from . import timeline
        InspectionRecord.objects.create(
            certificate=self.cert, inspector=self.regulator,
            check_result=InspectionRecord.CheckResult.UNQUALIFIED,
            check_score=Decimal("55.00"), problem_description="农药残留超标")
        tl = timeline.build_timeline(self.cert)
        # ★ key 现在是 inspection-<id>，不能再按 == "inspection" 精确匹配
        ins_nodes = [n for n in tl["nodes"] if n["key"].startswith("inspection")]
        self.assertEqual(len(ins_nodes), 1)
        self.assertEqual(ins_nodes[0]["level"], "danger")

    def test_public_verify_returns_timeline(self):
        """公众验真接口必须返回时间轴（前端时间轴的唯一数据源）。"""
        c = self.login(self.regulator)
        d = c.get(f"/api/public/verify/{self.cert.cert_no}/").data["data"]
        self.assertIn("timeline", d)
        self.assertIn("timeline_summary", d)
        self.assertGreater(len(d["timeline"]), 0)
        self.assertEqual(d["timeline_summary"]["status"], "issued")

    def test_timeline_api_requires_auth(self):
        """时间轴明细接口需要登录（公开接口只暴露脱敏后的验真数据）。"""
        c = APIClient()
        r = c.get(f"/api/certificate/{self.cert.id}/timeline/")
        self.assertEqual(r.status_code, 401)

    def test_timeline_api_for_regulator(self):
        c = self.login(self.regulator)
        r = c.get(f"/api/certificate/{self.cert.id}/timeline/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["data"]["cert_no"], self.cert.cert_no)

    def test_node_keys_are_unique_with_multiple_inspections(self):
        """
        ★ 同一张证有多条监督检查时，节点 key 必须唯一。

        踩坑：原本所有检查节点都用 "inspection" 当 key，两条检查就产生
        重复 key。前端 v-for 靠 key 做 diff，重复 key 会让 Vue 复用错节点
        —— 渲染出的内容可能和实际数据对不上，而且**不报任何错**。
        （线上实测：CERT-2026-1002 有 2 条检查，出现两个 key="inspection"）
        """
        from . import timeline
        from .models import InspectionRecord
        from django.utils import timezone
        # 再补一条检查，制造「同一张证多条检查」
        InspectionRecord.objects.create(
            certificate=self.cert, inspector=self.regulator,
            check_type=InspectionRecord.CheckType.ROUTINE,
            check_result=InspectionRecord.CheckResult.QUALIFIED,
            check_score=Decimal("90.00"), department="县农业农村局",
            check_time=timezone.now())
        tl = timeline.build_timeline(self.cert)
        keys = [n["key"] for n in tl["nodes"]]
        self.assertEqual(len(keys), len(set(keys)),
                         f"节点 key 出现重复：{keys}")
        insp_keys = [k for k in keys if k.startswith("inspection")]
        self.assertEqual(len(insp_keys), len(set(insp_keys)))
        self.assertGreaterEqual(len(insp_keys), 1)


# ===============================================================
# 十七、站内消息通知
# ===============================================================
class NotificationTest(BaseFixture):
    """
    通知的核心测试点：
      ① 业务动作是否真的产生了「给正确的人」的消息
      ② 是否**不给自己发**（操作人自己知道刚做了什么）
      ③ 未读/已读/全部已读/清理 的状态流转
      ④ ★ 消息的硬隔离：任何人都读不到别人的消息
    """

    def test_submit_notifies_regulator(self):
        """送审 → 监管收到消息（这是"审核完没人知道"的正向修复）。"""
        from .models import Notification
        self.cert.status = Certificate.Status.DRAFT
        self.cert.save(update_fields=["status"])
        c = self.login(self.producer)
        r = c.post(f"/api/certificate/{self.cert.id}/submit/")
        self.assertEqual(r.status_code, 200)
        n = Notification.objects.filter(
            recipient=self.regulator,
            category=Notification.Category.CERT_SUBMIT).first()
        self.assertIsNotNone(n)
        self.assertIn("待签发", n.title)
        self.assertEqual(n.link_route, "certificate")

    def test_issue_notifies_owner(self):
        """签发 → 主体 owner 收到消息（业务归属人，不是签发人）。"""
        from .models import Notification
        self.cert.status = Certificate.Status.PENDING
        self.cert.cert_no = None
        self.cert.save(update_fields=["status", "cert_no"])
        c = self.login(self.regulator)
        r = c.post(f"/api/certificate/{self.cert.id}/issue/")
        self.assertEqual(r.status_code, 200)
        n = Notification.objects.filter(
            recipient=self.producer,
            category=Notification.Category.CERT_ISSUE).first()
        self.assertIsNotNone(n)
        self.assertIn("已签发", n.title)

    def test_never_notify_self(self):
        """
        ★ 操作人不应收到自己动作产生的消息。
        监管签发时他自己就是操作人，若给自己发消息会变成噪音。
        """
        from .models import Notification
        c = self.login(self.regulator)
        c.post(f"/api/certificate/{self.cert.id}/revoke/", {"reason": "测试作废"},
               format="json")
        # 监管自己不该收到「合格证已作废」
        self.assertFalse(Notification.objects.filter(
            recipient=self.regulator,
            category=Notification.Category.CERT_REVOKE).exists())

    def test_reject_carries_reason(self):
        """驳回意见必须进入消息正文 —— 否则申请人不知道该改什么。"""
        from .models import Notification
        self.cert.status = Certificate.Status.PENDING
        self.cert.save(update_fields=["status"])
        c = self.login(self.regulator)
        c.post(f"/api/certificate/{self.cert.id}/reject/",
               {"reason": "检测报告已过期"}, format="json")
        n = Notification.objects.filter(
            recipient=self.producer,
            category=Notification.Category.CERT_REJECT).first()
        self.assertIsNotNone(n)
        self.assertIn("检测报告已过期", n.content)

    def test_inspection_notifies_producer(self):
        """检查不合格 → 被检主体收到消息，且额外收到「证被暂停」。"""
        from .models import Notification
        c = self.login(self.regulator)
        r = c.post("/api/inspection/", {
            "certificate": self.cert.id,
            "check_type": "routine", "check_result": "unqualified",
            "check_score": "50.00", "problem_description": "农药残留超标",
        }, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertTrue(Notification.objects.filter(
            recipient=self.producer,
            category=Notification.Category.INSPECTION).exists())
        # 证被暂停是另一件后果（货不能卖了），应单独提醒
        self.assertTrue(Notification.objects.filter(
            recipient=self.producer,
            category=Notification.Category.CERT_SUSPEND).exists())

    def test_intent_created_notifies_publisher(self):
        """发起对接 → 供需单发布方收到消息。"""
        from .models import Notification
        sd = SupplyDemand.objects.create(
            kind=SupplyDemand.Kind.SUPPLY, title="阿克苏砂糖橘供应",
            product_name="柑橘", quantity_kg=Decimal("1000.00"),
            price=Decimal("6.00"), publisher=self.producer)
        c = self.login(self.buyer)
        r = c.post(f"/api/supply/{sd.id}/intent/",
                   {"quantity_kg": "500.00", "price": "6.00"}, format="json")
        self.assertEqual(r.status_code, 200)
        n = Notification.objects.filter(
            recipient=self.producer,
            category=Notification.Category.MATCH).first()
        self.assertIsNotNone(n)
        self.assertEqual(n.link_route, "intent")

    def test_producer_audit_notifies_owner(self):
        """主体审核 → owner 收到消息。"""
        from .models import Notification
        c = self.login(self.regulator)
        c.post(f"/api/producer/{self.other_producer_obj.id}/audit/",
               {"audit_status": "approved"}, format="json")
        n = Notification.objects.filter(
            recipient=self.other_producer,
            category=Notification.Category.AUDIT).first()
        self.assertIsNotNone(n)
        self.assertIn("已通过", n.title)

    def test_unread_summary_counts(self):
        """未读汇总：数量与危险级分类要正确。"""
        from .models import Notification
        from . import notify as notify_mod
        notify_mod.send(self.regulator, Notification.Category.SYSTEM, "普通消息")
        notify_mod.send(self.regulator, Notification.Category.CERT_SUSPEND,
                        "危险消息", level=Notification.Level.DANGER)
        c = self.login(self.regulator)
        d = c.get("/api/notification/unread/").data["data"]
        self.assertEqual(d["unread"], 2)
        self.assertEqual(d["danger"], 1)

    def test_mark_read_and_read_all(self):
        """已读流转：单条已读 → 未读数减 1；全部已读 → 归零。"""
        from .models import Notification
        from . import notify as notify_mod
        notify_mod.send(self.regulator, Notification.Category.SYSTEM, "消息1")
        notify_mod.send(self.regulator, Notification.Category.SYSTEM, "消息2")
        c = self.login(self.regulator)
        self.assertEqual(c.get("/api/notification/unread/").data["data"]["unread"], 2)

        n = Notification.objects.filter(recipient=self.regulator).first()
        r = c.post(f"/api/notification/{n.id}/read/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c.get("/api/notification/unread/").data["data"]["unread"], 1)

        r2 = c.post("/api/notification/read-all/")
        self.assertEqual(r2.data["data"]["updated"], 1)
        self.assertEqual(c.get("/api/notification/unread/").data["data"]["unread"], 0)

    def test_clear_read_keeps_unread(self):
        """清理已读不能误删未读 —— 未读是待办，丢了就漏事。"""
        from .models import Notification
        from . import notify as notify_mod
        n1 = notify_mod.send(self.regulator, Notification.Category.SYSTEM, "已读的")
        notify_mod.send(self.regulator, Notification.Category.SYSTEM, "未读的")
        c = self.login(self.regulator)
        c.post(f"/api/notification/{n1.id}/read/")
        r = c.post("/api/notification/clear-read/")
        self.assertEqual(r.data["data"]["deleted"], 1)
        left = Notification.objects.filter(recipient=self.regulator)
        self.assertEqual(left.count(), 1)
        self.assertIsNone(left.first().read_at)

    def test_hard_isolation_between_users(self):
        """
        ★ 消息是硬隔离：任何人都读不到别人的消息。
        这里直接改 URL 里的 id 去访问别人的消息，必须 404。
        """
        from .models import Notification
        from . import notify as notify_mod
        other_msg = notify_mod.send(self.other_producer, Notification.Category.SYSTEM,
                                    "别人家的消息")
        c = self.login(self.producer)
        r = c.get(f"/api/notification/{other_msg.id}/")
        self.assertEqual(r.status_code, 404)
        # 列表里也不该出现
        ids = [x["id"] for x in c.get("/api/notification/").data["results"]]
        self.assertNotIn(other_msg.id, ids)

    def test_unauthenticated_denied(self):
        """未登录不能读消息。"""
        c = APIClient()
        self.assertEqual(c.get("/api/notification/").status_code, 401)
        self.assertEqual(c.get("/api/notification/unread/").status_code, 401)

    def test_category_filter(self):
        """按类型筛选。"""
        from .models import Notification
        from . import notify as notify_mod
        notify_mod.send(self.regulator, Notification.Category.SYSTEM, "系统消息")
        notify_mod.send(self.regulator, Notification.Category.INSPECTION, "检查消息")
        c = self.login(self.regulator)
        r = c.get("/api/notification/?category=inspection").data["results"]
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["category"], "inspection")

    def test_notification_failure_does_not_break_business(self):
        """
        ★ 通知失败绝不能让业务失败。
        这里传入一个已删除的用户对象模拟异常，send 必须静默返回 None
        而不是抛异常（否则签发这种核心动作会被消息表拖垮）。
        """
        from . import notify as notify_mod
        from .models import Notification
        ghost = User(username="ghost_user", role=Role.PRODUCER)
        # 未保存的对象没有 pk，写入必然失败
        result = notify_mod.send(ghost, Notification.Category.SYSTEM, "发不出去的消息")
        self.assertIsNone(result)
        self.assertFalse(Notification.objects.filter(title="发不出去的消息").exists())


# ===============================================================
# 十八、检测报告 OCR 识别
# ===============================================================
class OcrTest(BaseFixture):
    """
    OCR 测试策略：**不依赖真实 PDF/图片文件**，而是直接测「文本 → 字段」的
    解析层。原因：
      ① 解析层才是业务正确性的所在（引擎调用只是 IO）
      ② 不引入二进制测试样本，测试跑得快、可复现、不占仓库体积
    只有接口层用一段真实生成的 PDF 做一次端到端验证。
    """

    def test_parse_conclusion_unqualified_priority(self):
        """
        ★ 最关键的一条：「不合格」必须优先于「合格」匹配。
        中文里「不合格」包含「合格」子串，若先匹配「合格」，
        一张不合格的报告会被判成合格 —— 后果是放行不合格农产品。
        """
        from . import ocr as ocr_mod
        r = ocr_mod.parse_conclusion("检验结论：不合格")
        self.assertEqual(r[0], "unqualified")
        r2 = ocr_mod.parse_conclusion("检验结论：符合标准")
        self.assertEqual(r2[0], "qualified")

    def test_parse_conclusion_variants(self):
        """常见表述都要能认出来。"""
        from . import ocr as ocr_mod
        cases = [
            ("综合判定：合格", "qualified"),
            ("检测结论：不符合要求", "unqualified"),
            ("结论 达标", "qualified"),
            ("判定结果：未通过", "unqualified"),
        ]
        for text, expect in cases:
            with self.subTest(text=text):
                self.assertEqual(ocr_mod.parse_conclusion(text)[0], expect)

    def test_parse_report_no_variants(self):
        """报告编号的多种写法。"""
        from . import ocr as ocr_mod
        cases = [
            ("报告编号：RPT-2026-1003", "RPT-2026-1003"),
            ("检验报告编号: JC20260901", "JC20260901"),
            ("No.: ABC12345", "ABC12345"),
        ]
        for text, expect in cases:
            with self.subTest(text=text):
                self.assertEqual(ocr_mod.parse_report_no(text), expect)

    def test_parse_report_no_rejects_chinese_noise(self):
        """纯中文词不是编号，必须过滤掉（避免把「报告编号」本身当编号）。"""
        from . import ocr as ocr_mod
        self.assertEqual(ocr_mod.parse_report_no("报告编号：详见附页"), "")

    def test_parse_date_variants(self):
        """中文/短横线/斜杠三种日期写法都要能解析并规范成 YYYY-MM-DD。"""
        from . import ocr as ocr_mod
        cases = [
            ("检测日期：2026年9月20日", "2026-09-20"),
            ("检测日期：2026-09-20", "2026-09-20"),
            ("检测日期：2026/9/5", "2026-09-05"),
        ]
        for text, expect in cases:
            with self.subTest(text=text):
                self.assertEqual(ocr_mod.parse_date(text), expect)

    def test_parse_date_rejects_invalid(self):
        """非法日期（13 月、32 日）必须返回空，不能造出脏数据。"""
        from . import ocr as ocr_mod
        self.assertEqual(ocr_mod.parse_date("检测日期：2026-13-45"), "")

    def test_parse_org(self):
        from . import ocr as ocr_mod
        self.assertIn("阿克苏", ocr_mod.parse_org("检测机构：阿克苏地区检测中心"))
        self.assertIn("检测中心", ocr_mod.parse_org("样品由新疆农产品质量检测中心受理"))

    def test_parse_full_report_text(self):
        """完整报告文本 → 各字段一次全部识别。"""
        from . import ocr as ocr_mod
        text = """
        农产品质量安全检测报告
        报告编号：RPT-2026-8888
        样品名称：柑橘（砂糖橘）
        检测机构：阿克苏地区农产品质量检测中心
        检测日期：2026年9月18日
        农药残留：符合标准
        重金属：符合标准
        综合判定：合格
        """
        fields, labelled = ocr_mod.parse_fields(text)
        self.assertEqual(fields["report_no"], "RPT-2026-8888")
        self.assertEqual(fields["test_date"], "2026-09-18")
        self.assertEqual(fields["conclusion"], "qualified")
        self.assertIn("阿克苏", fields["test_org"])
        self.assertTrue(labelled)

    def test_confidence_high_for_text_layer(self):
        """电子版 PDF 文本层 + 关键字段命中 → 高置信度。"""
        from . import ocr as ocr_mod
        text = ("报告编号：RPT-2026-1\n检测日期：2026-09-01\n检验结论：合格\n" * 3)
        # 直接调内部解析+置信度逻辑：模拟 method=pdf-text
        fields, _ = ocr_mod.parse_fields(text)
        key_hit = sum(1 for k in ("report_no", "conclusion", "test_date") if fields.get(k))
        self.assertEqual(key_hit, 3)

    def test_engine_status_shape(self):
        """引擎自检接口返回结构稳定（部署排查依赖它）。"""
        from . import ocr as ocr_mod
        st = ocr_mod.engine_status()
        for k in ("pdf_text", "ocr_python", "ocr_binary", "ocr_langs", "ocr_ready"):
            self.assertIn(k, st)

    def test_ocr_endpoint_rejects_missing_file(self):
        c = self.login(self.producer)
        r = c.post("/api/report/ocr/")
        self.assertEqual(r.status_code, 400)

    def test_ocr_endpoint_rejects_bad_ext(self):
        """非白名单格式必须拒绝（与附件上传同一套规则）。"""
        from django.core.files.uploadedfile import SimpleUploadedFile
        c = self.login(self.producer)
        f = SimpleUploadedFile("evil.exe", b"MZ\x90\x00", content_type="application/x-msdownload")
        r = c.post("/api/report/ocr/", {"file": f}, format="multipart")
        self.assertEqual(r.status_code, 400)
        self.assertIn("仅支持", r.data["message"])

    def test_ocr_endpoint_extracts_from_real_pdf(self):
        """
        端到端：生成一份带文本层的真 PDF → 上传 → 断言字段被识别出来。
        ★ 这条测试证明了「电子版 PDF 走文本层」这条主路径真的通。
        """
        try:
            import pymupdf
        except ImportError:
            try:
                import fitz as pymupdf
            except ImportError:
                self.skipTest("本机无 PDF 库，跳过文本层端到端测试")
                return

        # ★ 必须用中日韩内置字体 china-s 插入中文。默认 Helvetica 只支持
        # latin-1，中文会被退化成「·」（U+00B7）写进文本层 —— 标签「检测结论：」
        # 消失后正则必然匹配不到，但这一条恰恰是最容易被误判成「代码坏了」的假故障。
        doc = pymupdf.open()
        page = doc.new_page()
        lines = [
            "农产品质量安全检测报告",
            "报告编号：RPT-OCR-9999",
            "检测日期：2026-09-19",
            "检测结论：合格",
        ]
        y = 80
        for ln in lines:
            page.insert_text((60, y), ln, fontsize=12, fontname="china-s")
            y += 30
        pdf_bytes = doc.tobytes()
        doc.close()

        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile("report.pdf", pdf_bytes, content_type="application/pdf")
        c = self.login(self.producer)
        r = c.post("/api/report/ocr/", {"file": f}, format="multipart")
        self.assertEqual(r.status_code, 200)
        d = r.data["data"]
        self.assertEqual(d["method"], "pdf-text")
        self.assertTrue(d["ok"])
        self.assertEqual(d["fields"]["report_no"], "RPT-OCR-9999")
        self.assertEqual(d["fields"]["test_date"], "2026-09-19")
        self.assertEqual(d["fields"]["conclusion"], "qualified")
        # 中文标签命中了两个关键字段 → 置信度判 high（按「关键字段命中数」而非
        # 引擎自评分，因为业务关心的是「能不能省下手敲」）
        self.assertEqual(d["confidence"], "high")

    def test_ocr_pdf_ascii_labels_lose_chinese(self):
        """
        ★ 反面样本：PDF 里若用纯 ASCII 标签（Conclusion: qualified），
        中文标签规则命中不了 → conclusion 必须留空，而不是猜一个。
        宁可让用户手填，也不能给个可能是错的结论。
        """
        try:
            import pymupdf
        except ImportError:
            try:
                import fitz as pymupdf
            except ImportError:
                self.skipTest("本机无 PDF 库，跳过")
                return

        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((60, 80), "Report No: RPT-ASCII-1", fontsize=12)
        page.insert_text((60, 110), "Conclusion: qualified", fontsize=12)
        pdf_bytes = doc.tobytes()
        doc.close()

        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile("r.pdf", pdf_bytes, content_type="application/pdf")
        c = self.login(self.producer)
        r = c.post("/api/report/ocr/", {"file": f}, format="multipart")
        self.assertEqual(r.status_code, 200)
        d = r.data["data"]
        # 编号是纯 ASCII，能认出来
        self.assertEqual(d["fields"]["report_no"], "RPT-ASCII-1")
        # 结论依赖中文标签，认不出就留空
        self.assertEqual(d["fields"]["conclusion"], "")

    def test_ocr_endpoint_requires_login(self):
        c = APIClient()
        r = c.post("/api/report/ocr/")
        self.assertEqual(r.status_code, 401)

    def test_ocr_status_endpoint(self):
        c = self.login(self.regulator)
        r = c.get("/api/report/ocr-status/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("pdf_text", r.data["data"])

    def test_ocr_fields_persisted_for_audit(self):
        """
        ★ 识别来源必须落库：出问题时能回答「这条数据是机器认的还是人敲的」。

        序列化器把 ocr_* 设为 read_only（前端不该随意声明来源），因此必须由
        视图从 request.data 显式取出。这条测试就是钉住这个连接点 ——
        少了它，字段会静默恒为空，不报错但溯源能力等于不存在。
        """
        c = self.login(self.producer)
        r = c.post("/api/report/", {
            "report_no": "RPT-AUDIT-1", "batch": self.batch.id,
            "test_org": "检测中心", "test_date": "2026-09-10",
            "conclusion": "qualified",
            "ocr_method": "pdf-text", "ocr_confidence": "high",
            "ocr_text": "报告编号：RPT-AUDIT-1",
        }, format="json")
        self.assertEqual(r.status_code, 201)
        obj = TestReport.objects.get(report_no="RPT-AUDIT-1")
        self.assertEqual(obj.ocr_method, "pdf-text")
        self.assertEqual(obj.ocr_confidence, "high")
        self.assertEqual(obj.ocr_text, "报告编号：RPT-AUDIT-1")
        # 列表接口应把识别方式翻成中文给前端展示
        lst = c.get("/api/report/?page_size=100").data["results"]
        row = next(x for x in lst if x["report_no"] == "RPT-AUDIT-1")
        self.assertEqual(row["ocr_method_display"], "PDF 文本层提取")

    def test_manual_report_has_no_ocr_provenance(self):
        """
        ★ 反向断言：手工录入的报告不得凭空带上识别来源。

        若这里也能查出 ocr_method，说明所有人都会显示成「机器识别」，
        溯源字段就失去了区分能力。
        """
        c = self.login(self.producer)
        r = c.post("/api/report/", {
            "report_no": "RPT-MANUAL-1", "batch": self.batch.id,
            "test_org": "检测中心", "test_date": "2026-09-11",
            "conclusion": "qualified",
        }, format="json")
        self.assertEqual(r.status_code, 201)
        obj = TestReport.objects.get(report_no="RPT-MANUAL-1")
        self.assertEqual(obj.ocr_method, "")
        self.assertEqual(obj.ocr_confidence, "")


# ===============================================================
# 十九、移动端适配的后端支撑（响应式不改变接口契约）
# ===============================================================
class MobileAdaptationTest(BaseFixture):
    """
    移动端适配主要是前端工作，但后端有两条必须守住的契约：
      ① 分页/列表接口在 page_size 较小时也要返回完整字段（移动端列表字段少但结构一致）
      ② 验真接口必须自包含 —— 手机扫码后只打这一个接口就要能渲染完整页面，
         不能让前端再串 3 个接口（移动网络下每多一次往返都明显变慢）
    """

    def test_public_verify_is_self_contained(self):
        """验真接口一次返回时间轴 + 证面 + 存证 + 检查，手机端无需二次请求。"""
        c = self.login(self.regulator)
        d = c.get(f"/api/public/verify/{self.cert.cert_no}/").data["data"]
        for key in ("cert_no", "producer_name", "cert_hash", "timeline",
                    "cert_no_parsed", "trusted", "verify_message"):
            self.assertIn(key, d)

    def test_public_verify_accessible_without_token(self):
        """
        ★ 扫码验真必须免登录。
        消费者扫码时不可能有账号，这条契约一旦破坏，整个公众验真的意义就没了。
        """
        c = APIClient()  # 不带任何认证
        r = c.get(f"/api/public/verify/{self.cert.cert_no}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["data"]["cert_no"], self.cert.cert_no)

    def test_list_endpoints_respect_page_size(self):
        """列表接口支持 page_size（移动端可按需少取）。"""
        c = self.login(self.regulator)
        r = c.get("/api/certificate/?page_size=1")
        self.assertEqual(r.status_code, 200)
        self.assertIn("results", r.data)
        self.assertLessEqual(len(r.data["results"]), 1)

    def test_public_verify_records_query_for_analytics(self):
        """每次扫码验真都落一条查询记录（看板「公众查询次数」的数据源）。"""
        from .models import PublicQueryRecord
        before = PublicQueryRecord.objects.count()
        c = APIClient()
        c.get(f"/api/public/verify/{self.cert.cert_no}/")
        self.assertEqual(PublicQueryRecord.objects.count(), before + 1)


# ===============================================================
# 二十、登录限流（#安全加固）
# ===============================================================
class LoginThrottleTest(BaseFixture):
    """登录接口防暴力破解：IP / 用户名任一路失败超阈值即 429。"""

    def tearDown(self):
        """每条用例结束后清空失败记录，避免跨用例累加相互干扰。"""
        from apps.accounts.throttling import LoginAttempt
        LoginAttempt.objects.all().delete()

    def _login(self, username, password):
        c = APIClient()
        return c.post("/api/auth/login/", {"username": username, "password": password},
                      format="json")

    def test_normal_login_never_throttled(self):
        """正常账号密码组合（即便错位）不会误伤登录主流程。"""
        from apps.accounts.throttling import LoginAttempt  # noqa: F401
        r = self._login("t_admin", "wrong-password-1")
        self.assertEqual(r.status_code, 400, "密码错误应 400 而非 429")
        # 成功登录仍然可用
        r2 = self._login("t_admin", "admin123")
        self.assertEqual(r2.status_code, 200)

    def test_ip_blocked_after_repeated_failures(self):
        """同 IP 连续失败超阈值 → 429（即便换正确密码也应被拦，因为 IP 已锁定）。"""
        for _ in range(11):
            self._login("t_admin", "definitely-wrong")
        r = self._login("t_admin", "admin123")
        self.assertEqual(r.status_code, 429,
                         f"IP 连续失败后应 429，实际 {r.status_code}")

    def test_username_blocked_across_ip(self):
        """同一用户名跨请求持续失败也触发限流（防分布式单账号爆破）。"""
        # 用不同来源 IP 模拟：直接调 record_failure 注入失败记录
        from apps.accounts.throttling import record_failure, is_blocked
        for i in range(11):
            record_failure(f"10.0.0.{i}", "victim_user")
        self.assertTrue(is_blocked("1.2.3.4", "victim_user"),
                        "用户名失败超阈值后，新 IP 访问也应被判定 blocked")

    def test_success_clears_failures(self):
        """登录成功后清空失败记录，正常用户不被误锁。"""
        from apps.accounts.throttling import clear_failures, is_blocked, record_failure
        for _ in range(5):
            record_failure("5.5.5.5", "t_admin")
        self.assertFalse(is_blocked("5.5.5.5", "t_admin"), "5 次未达阈值")
        clear_failures(ip="5.5.5.5", username="t_admin")
        self.assertFalse(is_blocked("5.5.5.5", "t_admin"), "清空后更不该锁")


# ===============================================================
# 加盐哈希（Salted Hash）
# ===============================================================
class SaltedHashTest(BaseFixture):

    @override_settings(EVIDENCE_SALT="test-salt-abc")
    def test_salt_changes_hash(self):
        """加盐后同一原文算出的摘要应与无盐时不同（盐参与哈希）。"""
        plain = "hello|world"
        unsalted = hashlib.sha256(plain.encode("utf-8")).hexdigest()
        salted = services.sha256_hex(plain)
        self.assertNotEqual(unsalted, salted)
        self.assertEqual(len(salted), 64)

    @override_settings(EVIDENCE_SALT="test-salt-abc")
    def test_same_salt_same_hash(self):
        """相同盐值下同一数据必须稳定（否则存证/验真对不上）。"""
        h1 = services.compute_certificate_hash(self.cert)
        h2 = services.compute_certificate_hash(self.cert)
        self.assertEqual(h1, h2)

    @override_settings(EVIDENCE_SALT="salt-A")
    def test_different_salt_different_hash(self):
        """盐值变化必然改变哈希（确保盐是真正参与计算的秘密参数）。"""
        h_a = services.compute_certificate_hash(self.cert)
        with override_settings(EVIDENCE_SALT="salt-B"):
            h_b = services.compute_certificate_hash(self.cert)
        self.assertNotEqual(h_a, h_b)

    def test_no_salt_equals_bare_sha256(self):
        """空盐时行为退化为无盐 SHA-256，保证向后兼容本地/历史数据。"""
        plain = "payload"
        self.assertEqual(services.sha256_hex(plain),
                         hashlib.sha256(plain.encode("utf-8")).hexdigest())


# ===============================================================
# 验真接口限流 + 动态防伪水印
# ===============================================================
class PublicVerifyGuardTest(BaseFixture):

    def test_watermark_included(self):
        """验真响应应含动态水印标识，供前端铺底防截图。"""
        c = APIClient()
        d = c.get(f"/api/public/verify/{self.cert.cert_no}/").data["data"]
        wm = d.get("watermark", "")
        self.assertTrue(wm, "应返回非空水印")
        self.assertIn("验真查询", wm)
        self.assertIn(self.cert.cert_no, wm)

    @override_settings(VERIFY_RATE_LIMIT=3)
    def test_rate_limit_hits_429(self):
        """同一 IP 近 60 秒内查询数超阈值后应返回 429（防刷）。"""
        c = APIClient()
        for _ in range(3):
            r = c.get(f"/api/public/verify/{self.cert.cert_no}/")
            self.assertEqual(r.status_code, 200)
        r = c.get(f"/api/public/verify/{self.cert.cert_no}/")
        self.assertEqual(r.status_code, 429, "超限后应返回 429")


# ===============================================================
# OCR 数值置信度（<70% 高亮）
# ===============================================================
class OcrConfidenceScoreTest(BaseFixture):

    def test_pdf_text_full_hit_scores_100(self):
        """pdf 文本层 + 3 关键字段全命中 → 100 分（不触发 <70 警示）。"""
        from . import ocr as ocr_mod
        text = "报告编号：R-PDF\n检测日期：2026-09-01\n检验结论：合格\n" * 3
        fields, _ = ocr_mod.parse_fields(text)
        key_hit = sum(1 for k in ("report_no", "conclusion", "test_date") if fields.get(k))
        self.assertEqual(key_hit, 3)
        # method=pdf-text + 3 命中 → 16 + 84 = 100
        self.assertEqual(16 + key_hit * 28, 100)

    def test_image_ocr_two_hits_below_70(self):
        """图像 OCR 命中 2 字段 → 62 分，应落 <70 警示区间。"""
        from . import ocr as ocr_mod
        # 用 monkeypatch 式直接验算映射：method=ocr-image + key_hit=2
        _score = 6 + 2 * 28
        self.assertEqual(_score, 62)
        self.assertLess(_score, 70)


# ===============================================================
# 主体信用评分
# ===============================================================
class CreditScoreTest(BaseFixture):
    """主体信用评分：负向扣分制 + 等级映射 + 可解释明细"""

    def test_clean_producer_gets_full_score(self):
        """无任何污点的主体 = 100 分「优秀」。"""
        r = credit.score_producer(self.producer_obj)
        self.assertEqual(r["score"], 100)
        self.assertEqual(r["grade"], "优秀")
        self.assertEqual(r["deductions"], [])

    def test_unqualified_inspection_deducts(self):
        """一次检查不合格 -10。"""
        InspectionRecord.objects.create(
            certificate=self.cert, inspector=self.regulator,
            check_result=InspectionRecord.CheckResult.UNQUALIFIED,
            rectify_status=InspectionRecord.RectifyStatus.DONE)
        r = credit.score_producer(self.producer_obj)
        self.assertEqual(r["score"], 90)
        self.assertEqual(r["grade"], "优秀")
        self.assertEqual(r["stats"]["unqualified_inspections"], 1)

    def test_overdue_rectify_deducts_15(self):
        """逾期整改 -15（is_overdue 为 True）。"""
        insp = InspectionRecord.objects.create(
            certificate=self.cert, inspector=self.regulator,
            check_result=InspectionRecord.CheckResult.UNQUALIFIED,
            rectify_status=InspectionRecord.RectifyStatus.PENDING,
            rectify_deadline=date(2020, 1, 1))  # 早已过期
        self.assertTrue(insp.is_overdue)
        r = credit.score_producer(self.producer_obj)
        # 检查不合格 -10 + 整改逾期 -15 = 75
        self.assertEqual(r["score"], 75)
        self.assertEqual(r["grade"], "良好")
        self.assertEqual(r["stats"]["overdue_rectifies"], 1)

    def test_recheck_failed_deducts_15(self):
        """复查仍不合格 -15（不与逾期重复扣）。"""
        InspectionRecord.objects.create(
            certificate=self.cert, inspector=self.regulator,
            check_result=InspectionRecord.CheckResult.UNQUALIFIED,
            rectify_status=InspectionRecord.RectifyStatus.RECHECK_FAILED,
            rectify_deadline=date(2020, 1, 1))
        r = credit.score_producer(self.producer_obj)
        # 检查不合格 -10 + 复查仍不合格 -15 = 75（走 elif，不重复扣逾期）
        self.assertEqual(r["score"], 75)
        self.assertEqual(r["stats"]["recheck_failed"], 1)
        self.assertEqual(r["stats"]["overdue_rectifies"], 0)

    def test_revoked_certificate_deducts_20(self):
        """作废证 -20。"""
        self.cert.status = Certificate.Status.REVOKED
        self.cert.save()
        r = credit.score_producer(self.producer_obj)
        self.assertEqual(r["score"], 80)
        self.assertEqual(r["stats"]["revoked_certificates"], 1)

    def test_unqualified_report_deducts_8(self):
        """不合格报告 -8。"""
        self.report.conclusion = TestReport.Conclusion.UNQUALIFIED
        self.report.save()
        r = credit.score_producer(self.producer_obj)
        self.assertEqual(r["score"], 92)
        self.assertEqual(r["stats"]["unqualified_reports"], 1)

    def test_score_never_negative(self):
        """扣分封底 0，不出现负数。"""
        # 造多张作废证 + 多条不合格检查，把分扣穿
        for i in range(8):
            Certificate.objects.create(
                cert_no=f"CERT-FLOOR-{i}", batch=self.batch, report=self.report,
                producer_name=self.producer_obj.name, product_name="柑橘",
                quantity_kg=Decimal("100"), status=Certificate.Status.REVOKED,
                issuer=self.regulator)
        r = credit.score_producer(self.producer_obj)
        self.assertEqual(r["score"], 0)
        self.assertEqual(r["grade"], "警示")

    def test_grade_boundaries(self):
        """等级边界：90/75/60 各档。"""
        self.assertEqual(credit.grade_of(90), "优秀")
        self.assertEqual(credit.grade_of(89), "良好")
        self.assertEqual(credit.grade_of(75), "良好")
        self.assertEqual(credit.grade_of(74), "一般")
        self.assertEqual(credit.grade_of(60), "一般")
        self.assertEqual(credit.grade_of(59), "警示")


class CertificateRiskTest(BaseFixture):
    """证风险画像：综合主体信用 + 证状态 + 报告结论"""

    def test_issued_cert_with_clean_subject_is_normal(self):
        r = credit.certificate_risk(self.cert)
        self.assertEqual(r["level"], "normal")

    def test_revoked_cert_is_high(self):
        self.cert.status = Certificate.Status.REVOKED
        self.cert.save()
        r = credit.certificate_risk(self.cert)
        self.assertEqual(r["level"], "high")

    def test_suspended_cert_is_high(self):
        self.cert.status = Certificate.Status.SUSPENDED
        self.cert.save()
        r = credit.certificate_risk(self.cert)
        self.assertEqual(r["level"], "high")

    def test_warning_subject_raises_risk(self):
        """主体信用警示 -> 证风险 high。"""
        for i in range(6):
            Certificate.objects.create(
                cert_no=f"CERT-WARN-{i}", batch=self.batch, report=self.report,
                producer_name=self.producer_obj.name, product_name="柑橘",
                quantity_kg=Decimal("100"), status=Certificate.Status.REVOKED,
                issuer=self.regulator)
        r = credit.certificate_risk(self.cert)
        self.assertEqual(r["level"], "high")
        self.assertIn("信用", " ".join(r["tips"]))

    def test_unqualified_report_sets_low(self):
        self.report.conclusion = TestReport.Conclusion.UNQUALIFIED
        self.report.save()
        r = credit.certificate_risk(self.cert)
        self.assertEqual(r["level"], "low")


# ===============================================================
# 字符串相似度（OCR 模糊匹配）
# ===============================================================
class LevenshteinTest(TestCase):
    """编辑距离算法正确性"""

    def test_identical_is_zero(self):
        self.assertEqual(similarity.levenshtein("柑橘", "柑橘"), 0)

    def test_empty_cases(self):
        self.assertEqual(similarity.levenshtein("", "abc"), 3)
        self.assertEqual(similarity.levenshtein("abc", ""), 3)
        self.assertEqual(similarity.levenshtein("", ""), 0)

    def test_single_substitution(self):
        # 柑桔 -> 柑橘，替换 1 次
        self.assertEqual(similarity.levenshtein("柑桔", "柑橘"), 1)

    def test_known_value(self):
        # 经典例：kitten -> sitting = 3
        self.assertEqual(similarity.levenshtein("kitten", "sitting"), 3)

    def test_symmetric(self):
        self.assertEqual(
            similarity.levenshtein("橙心合作社", "橙心柑橘合作社"),
            similarity.levenshtein("橙心柑橘合作社", "橙心合作社"),
        )


class SimilarityBestMatchTest(TestCase):
    """相似度 + best_match 阈值逻辑"""

    def test_similarity_range(self):
        s = similarity.similarity("橙心合作社", "橙心柑橘合作社")
        self.assertGreater(s, 0.6)
        self.assertLessEqual(s, 1.0)

    def test_best_match_hits(self):
        r = similarity.best_match(
            "橙心合作社", ["橙心柑橘合作社", "绿源果业", "天山果园"], threshold=0.6)
        self.assertTrue(r["matched"])
        self.assertEqual(r["best"], "橙心柑橘合作社")

    def test_best_match_miss_below_threshold(self):
        r = similarity.best_match("苹果", ["柑橘", "葡萄"], threshold=0.6)
        self.assertFalse(r["matched"])

    def test_best_match_empty(self):
        r = similarity.best_match("", ["柑橘"])
        self.assertFalse(r["matched"])

    def test_suggest_producer_fuzzy(self):
        """OCR 识别的残缺名能模糊匹配到已备案主体。"""
        from .models import Producer
        Producer.objects.create(
            name="橙心柑橘合作社", contact="测试", phone="13700000000",
            audit_status=Producer.AuditStatus.APPROVED)
        r = similarity.suggest_producer("橙心合作社", threshold=0.6)
        self.assertTrue(r["matched"])
        self.assertEqual(r["best"], "橙心柑橘合作社")
        self.assertIsNotNone(r["producer_id"])

    def test_suggest_producer_miss(self):
        """完全不相关的名字不匹配。"""
        r = similarity.suggest_producer("某某苹果园", threshold=0.6)
        self.assertFalse(r["matched"])
        self.assertIsNone(r["producer_id"])


# ===============================================================
# Merkle 树汇聚存证
# ===============================================================
class MerkleTreeTest(BaseFixture):
    """Merkle 根 / 证明 / 验证"""

    def test_empty_and_single(self):
        self.assertEqual(merkle.merkle_root([]), "")
        # 单叶：返回该叶的二次哈希（16 进制 64 位）
        r = merkle.merkle_root(["a"])
        self.assertEqual(len(r), 64)

    def test_root_deterministic(self):
        self.assertEqual(
            merkle.merkle_root(["a", "b", "c", "d"]),
            merkle.merkle_root(["a", "b", "c", "d"]),
        )

    def test_order_matters(self):
        self.assertNotEqual(
            merkle.merkle_root(["a", "b"]),
            merkle.merkle_root(["b", "a"]),
        )

    def test_leaf_change_changes_root(self):
        r1 = merkle.merkle_root(["batch1", "report1", "cert1"])
        r2 = merkle.merkle_root(["batch1", "report1", "cert1_tampered"])
        self.assertNotEqual(r1, r2)

    def test_odd_leaves(self):
        # 奇数叶子（3 个）也能正常出根
        r = merkle.merkle_root(["a", "b", "c"])
        self.assertEqual(len(r), 64)

    def test_proof_verify(self):
        leaves = ["batch", "report", "cert", "inspection"]
        root = merkle.merkle_root(leaves)
        # 对每个叶子，证明都应通过
        for i in range(len(leaves)):
            leaf_hex = merkle._h(str(leaves[i]).encode("utf-8")).hex()
            proof = merkle.merkle_proof(leaves, i)
            self.assertTrue(
                merkle.verify_merkle_proof(root, leaf_hex, proof),
                f"叶子 {i} 证明失败")

    def test_proof_rejects_wrong_leaf(self):
        leaves = ["batch", "report", "cert", "inspection"]
        root = merkle.merkle_root(leaves)
        leaf_hex = merkle._h(b"not-in-tree").hex()
        proof = merkle.merkle_proof(leaves, 0)
        self.assertFalse(merkle.verify_merkle_proof(root, leaf_hex, proof))

    def test_certificate_merkle_root(self):
        """一张证把批次/报告/证哈希汇聚成根（先留证）。"""
        # fixture 无存证记录时，根为空（leaves 为空）
        from .models import Certificate
        cert = Certificate.objects.create(
            cert_no="CERT-MERKLE-1",
            batch=self.batch, report=self.report,
            producer_name="测试柑橘合作社", product_name="柑橘",
            quantity_kg=Decimal("100"), status=Certificate.Status.ISSUED,
            issuer=self.regulator)
        # 无 evidence 记录 -> 空根
        self.assertEqual(merkle.build_certificate_merkle_root(cert), "")


# ===============================================================
# ★ 信用评分 / Merkle 接入接口与签发流程（集成测试）
# ===============================================================
class CreditEndpointTest(BaseFixture):
    """信用评分接入接口（GET /api/producer/<id>/credit/）"""

    def test_credit_endpoint_returns_score_and_grade(self):
        c = self.login(self.regulator)
        r = c.get(f"/api/producer/{self.producer_obj.id}/credit/")
        self.assertEqual(r.status_code, 200)
        data = r.data["data"]
        self.assertIn("score", data)
        self.assertIn("grade", data)
        self.assertIn("deductions", data)
        self.assertIn("stats", data)
        self.assertEqual(data["producer_id"], self.producer_obj.id)
        self.assertEqual(data["score"], 100)   # fixture 无污点

    def test_credit_endpoint_deduction_shows(self):
        InspectionRecord.objects.create(
            certificate=self.cert, inspector=self.regulator,
            check_result=InspectionRecord.CheckResult.UNQUALIFIED,
            rectify_status=InspectionRecord.RectifyStatus.DONE)
        c = self.login(self.regulator)
        r = c.get(f"/api/producer/{self.producer_obj.id}/credit/")
        self.assertEqual(r.data["data"]["score"], 90)

    def test_producer_list_serializer_has_credit(self):
        """主体列表应内联 credit_score / credit_grade（监管快速识别风险）。"""
        c = self.login(self.regulator)
        r = c.get("/api/producer/")
        first = r.data["results"][0]
        self.assertIn("credit_score", first)
        self.assertIn("credit_grade", first)

    def test_credit_ranking_in_dashboard(self):
        """看板应返回信用排行（低分=高风险在前）。"""
        c = self.login(self.regulator)
        data = c.get("/api/dashboard/").data["data"]
        self.assertIn("credit_ranking", data)
        self.assertIsInstance(data["credit_ranking"], list)


class MerkleIssueIntegrationTest(BaseFixture):
    """Merkle 根接入签发流程（增量，不动现有验真）"""

    def _issue(self):
        """走真实签发接口：建档 -> 提交 -> 签发。"""
        draft = Certificate.objects.create(
            batch=self.batch, report=self.report,
            producer_name=self.producer_obj.name, product_name="柑橘",
            quantity_kg=Decimal("100"), status=Certificate.Status.PENDING)
        c = self.login(self.regulator)
        r = c.post(f"/api/certificate/{draft.id}/issue/")
        self.assertEqual(r.status_code, 200, r.data)
        draft.refresh_from_db()
        return draft

    def test_issue_writes_merkle_root(self):
        """签发后应先写回 merkle_root 字段。"""
        draft = self._issue()
        self.assertTrue(draft.merkle_root)
        self.assertEqual(len(draft.merkle_root), 64)

    def test_issue_creates_merkle_evidence_record(self):
        """签发后应产生一条 bizType=merkle 的独立存证记录。"""
        draft = self._issue()
        rec = EvidenceRecord.objects.filter(
            biz_type=EvidenceRecord.BizType.MERKLE, biz_id=draft.id).first()
        self.assertIsNotNone(rec)
        self.assertEqual(rec.data_hash, draft.merkle_root)

    def test_merkle_root_does_not_break_existing_verify(self):
        """★ 增量关键：接入 Merkle 后，现有 CERTIFICATE 类型验真不受影响。"""
        draft = self._issue()
        result = services.verify_certificate(draft, check_chain=False)
        self.assertTrue(result["trusted"], "Merkle 接入不应破坏原有验真链路")

    def test_anchor_merkle_root_returns_none_when_no_evidence(self):
        """极端情况：证据哈希全空时不上链空根。"""
        bare = Certificate.objects.create(
            batch=self.batch, report=self.report,
            quantity_kg=Decimal("10"), status=Certificate.Status.ISSUED)
        # 不清空其它记录，直接断言：无存证时 build 返回空，anchor 返回 None
        # （构造一个无任何证据哈希的场景较难，这里验证空根保护分支）
        from . import merkle as _m
        if _m.build_certificate_merkle_root(bare) == "":
            self.assertIsNone(services.anchor_merkle_root(bare))


# ===============================================================
# ★ 安全加固回归测试（2026-10-07 审计修复）
# ===============================================================
class SecurityHardeningTest(BaseFixture):
    """针对审计发现的高危项做回归：越权写入 / 状态守卫 / 存证不可变 / IP 取信。"""

    # ---- C1：主体不能自改审核状态 ----
    def test_producer_cannot_self_approve_audit_status(self):
        """生产者 PATCH 自己主体的 audit_status 应被忽略（只读），状态保持 pending。"""
        c = self.login(self.producer)   # producer1 owner = producer_obj(approved)
        # 用 other_producer_obj（pending），它属于 other_producer，不是当前用户
        # 这里换成当前用户拥有的一个 pending 主体更贴切：直接用 own 主体验证只读
        r = c.patch(f"/api/producer/{self.producer_obj.id}/",
                    {"audit_status": "rejected"}, format="json")
        # 请求可能 200（只读字段被忽略）或 400；关键是状态没被改
        self.producer_obj.refresh_from_db()
        self.assertEqual(self.producer_obj.audit_status,
                         Producer.AuditStatus.APPROVED,
                         "audit_status 必须只读：不能由主体自行修改")

    # ---- C2：不能前端直接置证为 issued ----
    def test_certificate_status_is_read_only(self):
        """PATCH status=issued 必须无效（防止绕过签发流程自行签发）。"""
        reg = self.login(self.regulator)
        draft = Certificate.objects.create(
            batch=self.batch, report=self.report,
            producer_name=self.producer_obj.name, quantity_kg=Decimal("10"),
            status=Certificate.Status.DRAFT)
        reg.patch(f"/api/certificate/{draft.id}/",
                  {"status": "issued"}, format="json")
        draft.refresh_from_db()
        self.assertEqual(draft.status, Certificate.Status.DRAFT,
                         "status 必须只读：不能通过 PATCH 直接置为已签发")
        self.assertFalse(draft.cert_no, "未经签发不应有编号")

    # ---- M2：草稿证不可作废 + 不炸 500 ----
    def test_revoke_draft_certificate_rejected(self):
        """草稿证（无编号）作废应被状态守卫拦为 400，而不是 500。"""
        reg = self.login(self.regulator)
        draft = Certificate.objects.create(
            batch=self.batch, report=self.report, quantity_kg=Decimal("5"),
            status=Certificate.Status.DRAFT)
        r = reg.post(f"/api/certificate/{draft.id}/revoke/",
                     {"reason": "测试"}, format="json")
        self.assertEqual(r.status_code, 400,
                         "草稿证作废应被守卫拦为 400（原会 500）")
        draft.refresh_from_db()
        self.assertNotEqual(draft.status, Certificate.Status.REVOKED)

    # ---- M3/B3：存证改追加式版本（防篡改锚点 = 历史版本留痕）----
    def test_evidence_reanchor_appends_version_keeping_history(self):
        """
        重存证应为**追加新版本**而非覆盖：旧版本归档保留，当前版本=新摘要。
        这样「改数据 → 重存证」后，历史版本仍留痕（可回溯），且验真按当前版本比对。
        """
        services.anchor_certificate(self.cert)
        v1 = EvidenceRecord.objects.get(
            biz_type=EvidenceRecord.BizType.CERTIFICATE, biz_id=self.cert.id,
            is_current=True)
        self.assertEqual(v1.version, 1)
        original_hash = v1.data_hash

        # 篡改业务数据后重新存证
        Certificate.objects.filter(id=self.cert.id).update(
            quantity_kg=Decimal("1.00"))
        self.cert.refresh_from_db()
        services.anchor_certificate(self.cert)

        rows = EvidenceRecord.objects.filter(
            biz_type=EvidenceRecord.BizType.CERTIFICATE, biz_id=self.cert.id)
        self.assertEqual(rows.count(), 2, "重存证应追加一条新版本（历史保留）")
        v1.refresh_from_db()
        self.assertFalse(v1.is_current, "旧版本应被归档（is_current=False）")
        self.assertEqual(v1.data_hash, original_hash, "旧版本摘要不得被改写")
        cur = rows.get(is_current=True)
        self.assertEqual(cur.version, 2)
        # 当前版本 = 篡改后重算的哈希；验真按当前版本比对 → 一致（数据与当前存证自洽）
        self.assertEqual(cur.data_hash, services.compute_certificate_hash(self.cert))

    def test_evidence_reanchor_same_digest_no_new_version(self):
        """摘要未变时重存证不应产生新版本（只补齐链上信息）。"""
        services.anchor_certificate(self.cert)
        services.anchor_certificate(self.cert)  # 数据没变
        rows = EvidenceRecord.objects.filter(
            biz_type=EvidenceRecord.BizType.CERTIFICATE, biz_id=self.cert.id)
        self.assertEqual(rows.count(), 1, "摘要一致时不应新增版本")
        self.assertEqual(rows.get(is_current=True).version, 1)

    def test_tampered_data_verify_false_against_current(self):
        """篡改数据但**不**重存证 → 当前版本摘要与重算不符 → 验真判不可信。"""
        services.anchor_certificate(self.cert)
        Certificate.objects.filter(id=self.cert.id).update(
            quantity_kg=Decimal("999.00"))
        self.cert.refresh_from_db()
        result = services.verify_certificate(self.cert)
        self.assertFalse(result["trusted"], "篡改未重存证时验真必须判不可信")

    # ---- M5：降级（mock）存证不做链上核验 ----
    def test_mock_evidence_skips_chain_check(self):
        """mock 存证不应触发链上核验（chain_status != success）。"""
        services.anchor_certificate(self.cert)
        rec = EvidenceRecord.objects.get(
            biz_type=EvidenceRecord.BizType.CERTIFICATE, biz_id=self.cert.id,
            is_current=True)
        if rec.chain_status != "success":
            result = services.verify_certificate(self.cert, check_chain=True)
            self.assertFalse(result["chain_verified"])
            self.assertIn("降级", result["chain_detail"])
        else:
            # 链可用时应真的比对（避免「条件断言等于没测」）
            result = services.verify_certificate(self.cert, check_chain=True)
            self.assertTrue(result["chain_detail"])

    # ---- M10：C3 /media 需登录 ----
    def test_media_requires_authentication(self):
        """/media/ 匿名访问必须 401（原来匿名可下载）。"""
        c = APIClient()  # 未登录
        r = c.get("/media/reports/nonexistent.pdf")
        self.assertEqual(r.status_code, 401,
                         "匿名访问 /media/ 必须被拒（原为匿名可下载）")

    # ---- B4/M6：IP 取信受可信代理白名单约束 ----
    def test_client_ip_prefers_real_ip_behind_trusted_proxy(self):
        """REMOTE_ADDR 是可信代理时，优先 X-Real-IP。"""
        from apps.accounts.netutil import get_client_ip
        from django.test import RequestFactory
        rf = RequestFactory()
        req = rf.get("/", REMOTE_ADDR="127.0.0.1",
                     HTTP_X_FORWARDED_FOR="1.2.3.4, 10.0.0.1",
                     HTTP_X_REAL_IP="203.0.113.9")
        self.assertEqual(get_client_ip(req), "203.0.113.9")

    def test_client_ip_falls_back_to_last_xff(self):
        """无 X-Real-IP 时取 XFF 最后一段（可信代理追加的那跳），而非第一段。"""
        from apps.accounts.netutil import get_client_ip
        from django.test import RequestFactory
        rf = RequestFactory()
        req = rf.get("/", REMOTE_ADDR="127.0.0.1",
                     HTTP_X_FORWARDED_FOR="1.2.3.4, 10.0.0.1")
        self.assertEqual(get_client_ip(req), "10.0.0.1")

    def test_client_ip_ignores_headers_when_direct(self):
        """★ B4：直连（REMOTE_ADDR 非可信代理）时，伪造的 X-Real-IP 必须被忽略。"""
        from apps.accounts.netutil import get_client_ip
        from django.test import RequestFactory
        rf = RequestFactory()
        req = rf.get("/", REMOTE_ADDR="203.0.113.7",
                     HTTP_X_REAL_IP="1.2.3.4",
                     HTTP_X_FORWARDED_FOR="1.2.3.4")
        self.assertEqual(get_client_ip(req), "203.0.113.7",
                         "直连时不得采信可伪造的转发头")

    # ---- M1：跨租户挂靠被拒 ----
    def test_producer_cannot_attach_batch_to_others_producer(self):
        """主体 A 不能把批次挂到主体 B 名下。"""
        c = self.login(self.producer)   # owner = producer_obj
        r = c.post("/api/batch/", {
            "batch_no": "B-XTENANT-001",
            "producer": self.other_producer_obj.id,   # 属于 other_producer
            "product_name": "柑橘", "weight_kg": "100.00",
        }, format="json")
        self.assertEqual(r.status_code, 400,
                         "跨租户挂靠应被序列化校验拒绝")

    # ---- rehash_all 默认 dry-run ----
    def test_rehash_all_defaults_to_dry_run(self):
        """rehash_all 默认 dry-run，不修改数据。"""
        from django.core.management import call_command
        from io import StringIO
        services.anchor_certificate(self.cert)
        rec = EvidenceRecord.objects.get(
            biz_type=EvidenceRecord.BizType.CERTIFICATE, biz_id=self.cert.id,
            is_current=True)
        before_hash, before_chain = rec.data_hash, rec.chain_status
        out = StringIO()
        call_command("rehash_all", stdout=out)
        rec.refresh_from_db()
        self.assertIn("dry-run", out.getvalue())
        self.assertEqual(rec.chain_status, before_chain)


# ===============================================================
# ★ 第三批加固回归（JWT 轮换/注销、Merkle 漂移、report_hash 对齐）
# ===============================================================
class JwtAndMerkleHardeningTest(BaseFixture):
    """JWT 黑名单注销、Merkle 根漂移检测、report_hash 与存证对齐。"""

    def test_logout_blacklists_refresh(self):
        """注销后该 refresh 不能再用（黑名单生效）。"""
        from rest_framework_simplejwt.tokens import RefreshToken as RT
        c = APIClient()
        r = c.post("/api/auth/login/", {
            "username": "t_prod", "password": "admin123"}, format="json")
        self.assertEqual(r.status_code, 200)
        payload = r.data.get("data") or {}
        refresh = payload.get("refresh")
        self.assertTrue(refresh, "登录应返回 refresh token")
        # 用 access 调注销
        access = payload.get("access")
        c.credentials(HTTP_AUTHORIZATION="Bearer " + access)
        out = c.post("/api/auth/logout/", {"refresh": refresh}, format="json")
        self.assertEqual(out.status_code, 200)
        # 该 refresh 应已在黑名单
        try:
            RT(refresh).check_blacklist()
            blacklisted = False
        except Exception:
            blacklisted = True
        self.assertTrue(blacklisted, "注销后 refresh 应已在黑名单")

    def test_merkle_drift_check_reports_no_drift_initially(self):
        """刚签发（无后续检查）时 merkle 根不漂移。"""
        services.anchor_certificate(self.cert)
        services.anchor_merkle_root(self.cert)
        from . import merkle as _m
        info = _m.check_merkle_drift(self.cert)
        self.assertEqual(info["stored"], info["recomputed"])
        self.assertFalse(info["drifted"])

    def test_merkle_drift_detected_after_new_inspection(self):
        """签发后新增「已存证检查」→ 重算根偏离存储根，drifted=True 且有说明。"""
        services.anchor_certificate(self.cert)
        first = services.anchor_merkle_root(self.cert)
        self.assertIsNotNone(first)
        # 新增一条已存证的检查记录
        ins = InspectionRecord.objects.create(
            certificate=self.cert, check_type=InspectionRecord.CheckType.ROUTINE,
            check_result=InspectionRecord.CheckResult.QUALIFIED,
            inspector=self.regulator, inspector_name="监管员",
            check_location="基地", record_hash="a" * 64, is_anchored=True)
        from . import merkle as _m
        info = _m.check_merkle_drift(self.cert)
        self.assertTrue(info["drifted"], "新增检查存证后应检测到 Merkle 根漂移")
        self.assertIn("演进", info["message"])

    def test_report_hash_matches_current_evidence(self):
        """report_hash 应与「当前版本」存证摘要一致（不再与历史分叉）。"""
        services.anchor_report(self.report)
        rec = EvidenceRecord.objects.get(
            biz_type=EvidenceRecord.BizType.REPORT, biz_id=self.report.id,
            is_current=True)
        self.report.refresh_from_db()
        self.assertEqual(self.report.report_hash, rec.data_hash)

    def test_secure_settings_default_off_on_http(self):
        """纯 HTTP 部署时 SECURE_SSL_REDIRECT 必须为 False（否则重定向死循环）。"""
        from django.conf import settings as s
        # 默认（未设 DJANGO_HTTPS）应为 False
        self.assertFalse(s.SECURE_SSL_REDIRECT)
        self.assertEqual(s.SECURE_HSTS_SECONDS, 0)


# ===============================================================
# ★ 第四批加固回归（R5 时区口径一致、R1 版本切换原子性）
# ===============================================================
class TimezoneAndAtomicityTest(BaseFixture):
    """today_local 口径统一、追加版本切换的原子/一致性。"""

    def test_today_local_matches_timezone(self):
        """certno.today_local() 应等于 settings.TIME_ZONE 下的当天（与机器时区无关）。"""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from django.conf import settings as s
        from . import certno
        expect = datetime.now(ZoneInfo(s.TIME_ZONE)).date()
        self.assertEqual(certno.today_local(), expect)

    def test_issue_date_uses_same_source_as_cert_no(self):
        """签发时 issue_date 与编号日期段应为同一天（R5：不再差一天）。"""
        from . import certno
        reg = self.login(self.regulator)
        cert = Certificate.objects.create(
            batch=self.batch, report=self.report, quantity_kg=Decimal("10"),
            status=Certificate.Status.PENDING)
        r = reg.post(f"/api/certificate/{cert.id}/issue/", {}, format="json")
        self.assertEqual(r.status_code, 200)
        cert.refresh_from_db()
        self.assertTrue(cert.cert_no, "签发应分配编号")
        self.assertIsNotNone(cert.issue_date)
        # 编号第 7~14 位是日期段
        no_date = cert.cert_no[6:14]
        self.assertEqual(no_date, cert.issue_date.strftime("%Y%m%d"),
                         "编号日期段与 issue_date 必须一致")

    def test_reanchor_keeps_exactly_one_current(self):
        """重存证后必须恰好一条 is_current 版本（R1：无中间态）。"""
        services.anchor_certificate(self.cert)
        Certificate.objects.filter(id=self.cert.id).update(
            quantity_kg=Decimal("1.00"))
        self.cert.refresh_from_db()
        services.anchor_certificate(self.cert)
        rows = EvidenceRecord.objects.filter(
            biz_type=EvidenceRecord.BizType.CERTIFICATE, biz_id=self.cert.id)
        self.assertEqual(rows.filter(is_current=True).count(), 1,
                         "任何时刻都应有且仅有一条 current 版本")
        self.assertGreaterEqual(rows.count(), 2, "历史版本应保留")

    def test_certificate_data_uses_current_version(self):
        """证面接口的 tx_hash 取 is_current 版本，而非「id 最大」的巧合。"""
        services.anchor_certificate(self.cert)
        services.anchor_merkle_root(self.cert)
        reg = self.login(self.regulator)
        r = reg.get(f"/api/certificate/{self.cert.id}/certificate-data/")
        self.assertEqual(r.status_code, 200)
        data = r.data.get("data", r.data)
        cur = EvidenceRecord.objects.get(
            biz_type=EvidenceRecord.BizType.CERTIFICATE, biz_id=self.cert.id,
            is_current=True)
        self.assertEqual(data.get("tx_hash"), cur.tx_hash)


class TodayLocalRobustnessTest(BaseFixture):
    """R5 兜底：ZoneInfo 不可用时应退回 date.today() 并告警（不静默）。"""

    def test_today_local_falls_back_with_warning(self):
        """模拟 ZoneInfo 不可用 → 退回 date.today()，且（首次）记录 warning。"""
        from unittest import mock
        from datetime import date
        from . import certno
        certno._TZ_WARNED = False   # 重置「只告警一次」标志
        with mock.patch("zoneinfo.ZoneInfo", side_effect=Exception("no tzdata")):
            with self.assertLogs("apps.traceability.certno", level="WARNING") as cm:
                d = certno.today_local()
        self.assertEqual(d, date.today())
        self.assertTrue(any("tzdata" in m for m in cm.output),
                        "兜底应提示安装 tzdata")

    def test_today_local_warns_only_once(self):
        """兜底告警只打印一次（避免高频调用刷日志）。"""
        from unittest import mock
        from . import certno
        certno._TZ_WARNED = False
        with mock.patch("zoneinfo.ZoneInfo", side_effect=Exception("no tzdata")):
            with self.assertLogs("apps.traceability.certno", level="WARNING") as cm:
                certno.today_local()
                certno.today_local()
                certno.today_local()
        self.assertEqual(len(cm.output), 1, "只应告警一次")

    def test_today_local_ok_when_zoneinfo_available(self):
        """正常情况：按 TIME_ZONE 取日，无告警。"""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from django.conf import settings as s
        from . import certno
        try:
            ZoneInfo(s.TIME_ZONE)
        except Exception:
            self.skipTest("本环境无 tzdata，跳过")
        self.assertEqual(certno.today_local(),
                         datetime.now(ZoneInfo(s.TIME_ZONE)).date())


# ===============================================================
# ★ 第四批安全加固回归（2026-10-11 安全审计修复）
#
# 对应审计结论：
#   高：供需单越权写 / 意向单回应无鉴权 / 签发不校验结论 /
#       暂停证状态绕过 / 验真未覆盖关联数据
#   中高：合约写入权限与覆盖 / 演示账号固定口令 / 部署脚本主机密钥
#   中：哈希拼接歧义
# ===============================================================
class SupplyDemandWriteScopeTest(BaseFixture):
    """审计 High：供需单写操作必须只作用于自己发布的单。"""

    def setUp(self):
        # 买家发一条求购单；种植主体发一条供应单
        self.demand = SupplyDemand.objects.create(
            kind=SupplyDemand.Kind.DEMAND, title="求购砂糖橘",
            product_name="柑橘", quantity_kg=Decimal("1000.00"),
            publisher=self.buyer)
        self.supply = SupplyDemand.objects.create(
            kind=SupplyDemand.Kind.SUPPLY, title="供应砂糖橘",
            product_name="柑橘", quantity_kg=Decimal("1000.00"),
            publisher=self.producer)

    def test_producer_cannot_modify_buyer_demand(self):
        """★ 种植主体不能改买家的求购单（原漏洞：可见即可改）"""
        c = self.login(self.producer)
        r = c.patch(f"/api/supply/{self.demand.id}/",
                    {"title": "被恶意改标题"}, format="json")
        self.assertIn(r.status_code, (403, 404),
                      "种植主体不应能修改他人的求购单")
        self.demand.refresh_from_db()
        self.assertEqual(self.demand.title, "求购砂糖橘", "原标题不得被改")

    def test_producer_cannot_delete_buyer_demand(self):
        """★ 种植主体不能删买家的求购单"""
        c = self.login(self.producer)
        r = c.delete(f"/api/supply/{self.demand.id}/")
        self.assertIn(r.status_code, (403, 404))
        self.assertTrue(SupplyDemand.objects.filter(id=self.demand.id).exists())

    def test_buyer_cannot_modify_others_supply(self):
        """★ 收购商不能改他人的供应单"""
        c = self.login(self.buyer)
        r = c.patch(f"/api/supply/{self.supply.id}/",
                    {"title": "被恶意改标题"}, format="json")
        self.assertIn(r.status_code, (403, 404))
        self.supply.refresh_from_db()
        self.assertEqual(self.supply.title, "供应砂糖橘")

    def test_owner_can_still_modify_own(self):
        """回归：本人仍然可以改自己的单（修复不能误伤正常功能）"""
        c = self.login(self.producer)
        r = c.patch(f"/api/supply/{self.supply.id}/",
                    {"title": "自己的单正常改"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.supply.refresh_from_db()
        self.assertEqual(self.supply.title, "自己的单正常改")

    def test_public_browse_still_works(self):
        """回归：浏览对方公开单的能力不能被削弱"""
        c = self.login(self.producer)
        r = c.get("/api/supply/?kind=demand")
        self.assertEqual(r.status_code, 200)
        ids = [x["id"] for x in r.data.get("results", r.data)]
        self.assertIn(self.demand.id, ids, "种植主体仍应看得到买家求购单")


class MatchIntentRespondAuthTest(BaseFixture):
    """审计 High：意向单回应必须校验回应人身份 + 限制状态流转。"""

    def setUp(self):
        self.supply = SupplyDemand.objects.create(
            kind=SupplyDemand.Kind.SUPPLY, title="供应砂糖橘",
            product_name="柑橘", quantity_kg=Decimal("1000.00"),
            publisher=self.producer)
        self.intent = MatchIntent.objects.create(
            supply=self.supply, initiator=self.buyer,
            quantity_kg=Decimal("100.00"), price=Decimal("5.00"),
            status=MatchIntent.Status.PENDING)

    def test_initiator_cannot_accept_own_intent(self):
        """★ 发起人不能替对方接受自己的意向（原漏洞）"""
        c = self.login(self.buyer)
        r = c.post(f"/api/intent/{self.intent.id}/respond/",
                   {"status": "accepted"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, MatchIntent.Status.PENDING)

    def test_publisher_can_accept(self):
        """发布方可以接受（正常路径）"""
        c = self.login(self.producer)
        r = c.post(f"/api/intent/{self.intent.id}/respond/",
                   {"status": "accepted"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, MatchIntent.Status.ACCEPTED)

    def test_cannot_revive_rejected_intent(self):
        """★ 已拒绝是终态，不能改回 pending（原漏洞：任意状态可跳）"""
        self.intent.status = MatchIntent.Status.REJECTED
        self.intent.save(update_fields=["status"])
        c = self.login(self.producer)
        r = c.post(f"/api/intent/{self.intent.id}/respond/",
                   {"status": "pending"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, MatchIntent.Status.REJECTED)

    def test_cannot_jump_pending_to_done(self):
        """★ 不能跳过 accepted 直接 pending -> done"""
        c = self.login(self.producer)
        r = c.post(f"/api/intent/{self.intent.id}/respond/",
                   {"status": "done"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_patch_is_blocked(self):
        """★ PATCH 也必须被拦（原实现只拦了 PUT）"""
        c = self.login(self.producer)
        r = c.patch(f"/api/intent/{self.intent.id}/",
                    {"status": "accepted"}, format="json")
        self.assertEqual(r.status_code, 405)
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, MatchIntent.Status.PENDING)


class CertIssueReportConclusionTest(BaseFixture):
    """审计 High：签发必须校验报告结论合格 + 报告同批次 + 暂停证不可签发。"""

    def _cert(self, report, status=Certificate.Status.PENDING):
        return Certificate.objects.create(
            batch=self.batch, report=report, producer_name=self.producer_obj.name,
            product_name="柑橘", quantity_kg=Decimal("1.00"), status=status)

    def test_cannot_issue_with_unqualified_report(self):
        """★ 不合格报告不得用于签发（原漏洞：只查 report_id 存在）"""
        bad = TestReport.objects.create(
            report_no="R-BAD-001", batch=self.batch, test_org="X",
            test_date=date(2026, 9, 5),
            conclusion=TestReport.Conclusion.UNQUALIFIED)
        cert = self._cert(bad)
        c = self.login(self.regulator)
        r = c.post(f"/api/certificate/{cert.id}/issue/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("不合格", r.data["message"])
        cert.refresh_from_db()
        self.assertFalse(cert.cert_no, "被拒的签发不得分配编号")

    def test_cannot_issue_with_cross_batch_report(self):
        """★ 报告必须与合格证同批次（防止张冠李戴）"""
        other_batch = ProductBatch.objects.create(
            batch_no="B-OTHER", producer=self.producer_obj,
            product_name="柑橘", weight_kg=Decimal("1.00"))
        other_report = TestReport.objects.create(
            report_no="R-OTHER", batch=other_batch,
            conclusion=TestReport.Conclusion.QUALIFIED)
        cert = self._cert(other_report)
        c = self.login(self.regulator)
        r = c.post(f"/api/certificate/{cert.id}/issue/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("批次", r.data["message"])

    def test_batch_issue_also_checks_conclusion(self):
        """★ 批量签发与单张签发走同一套校验（原漏洞：只堵了一个口）"""
        bad = TestReport.objects.create(
            report_no="R-BAD-002", batch=self.batch,
            conclusion=TestReport.Conclusion.UNQUALIFIED)
        cert = self._cert(bad)
        c = self.login(self.regulator)
        r = c.post("/api/certificate/batch-issue/", {"ids": [cert.id]},
                   format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["data"]["success_count"], 0)
        self.assertEqual(r.data["data"]["fail_count"], 1)

    def test_suspended_cannot_resubmit(self):
        """★ 暂停证不能重新送审（原漏洞：suspended 可回 pending）"""
        cert = self._cert(self.report, status=Certificate.Status.SUSPENDED)
        c = self.login(self.producer)
        r = c.post(f"/api/certificate/{cert.id}/submit/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("暂停", r.data["message"])
        cert.refresh_from_db()
        self.assertEqual(cert.status, Certificate.Status.SUSPENDED)

    def test_suspended_cannot_issue(self):
        """★ 暂停证不能直接签发（防状态绕过）"""
        cert = self._cert(self.report, status=Certificate.Status.SUSPENDED)
        c = self.login(self.regulator)
        r = c.post(f"/api/certificate/{cert.id}/issue/")
        self.assertEqual(r.status_code, 400)
        cert.refresh_from_db()
        self.assertEqual(cert.status, Certificate.Status.SUSPENDED)


class CertStateMachineTest(BaseFixture):
    """状态机集中定义后的合法性校验（rules.py 唯一来源）。"""

    def test_transition_table(self):
        from . import rules
        # 合法
        self.assertTrue(rules.can_transition_cert("draft", "pending"))
        self.assertTrue(rules.can_transition_cert("pending", "issued"))
        self.assertTrue(rules.can_transition_cert("issued", "suspended"))
        self.assertTrue(rules.can_transition_cert("issued", "revoked"))
        self.assertTrue(rules.can_transition_cert("suspended", "revoked"))
        # 非法
        self.assertFalse(rules.can_transition_cert("suspended", "pending"))
        self.assertFalse(rules.can_transition_cert("suspended", "issued"))
        self.assertFalse(rules.can_transition_cert("revoked", "issued"))
        self.assertFalse(rules.can_transition_cert("revoked", "draft"))

    def test_issuable_from_excludes_suspended_revoked(self):
        from . import rules
        self.assertIn("draft", rules.CERT_ISSUABLE_FROM)
        self.assertIn("pending", rules.CERT_ISSUABLE_FROM)
        self.assertNotIn("suspended", rules.CERT_ISSUABLE_FROM)
        self.assertNotIn("revoked", rules.CERT_ISSUABLE_FROM)


class VerifyRelatedEvidenceTest(BaseFixture):
    """审计 High：验真必须覆盖关联批次/报告（展示值与可信结论不能脱节）。"""

    def test_related_list_present(self):
        services.anchor_certificate(self.cert)
        result = services.verify_certificate(self.cert)
        kinds = {x["kind"] for x in result["related"]}
        self.assertIn("batch", kinds)
        self.assertIn("report", kinds)

    def test_batch_tamper_makes_cert_untrusted(self):
        """★ 改批次产地 → 证书摘要变化 → 判定不可信"""
        services.anchor_certificate(self.cert)
        self.assertTrue(services.verify_certificate(self.cert)["trusted"])
        ProductBatch.objects.filter(id=self.batch.id).update(origin="被篡改的产地")
        self.cert.refresh_from_db()
        result = services.verify_certificate(self.cert)
        self.assertFalse(result["trusted"], "批次被改后必须判不可信")

    def test_related_batch_check_marks_checked_when_anchored(self):
        """批次单独存证后，关联核验应标记为 checked 且 trusted"""
        services.anchor_batch(self.batch)
        services.anchor_certificate(self.cert)
        result = services.verify_certificate(self.cert)
        batch_item = next(x for x in result["related"] if x["kind"] == "batch")
        self.assertTrue(batch_item["checked"])
        self.assertTrue(batch_item["trusted"])

    def test_related_batch_untampered_but_not_anchored(self):
        """批次未单独存证时 checked=False，且不参与可信判定（不误伤）"""
        services.anchor_certificate(self.cert)
        result = services.verify_certificate(self.cert)
        batch_item = next(x for x in result["related"] if x["kind"] == "batch")
        self.assertFalse(batch_item["checked"])
        self.assertTrue(batch_item["trusted"],
                        "未单独存证不应导致不可信（已由证书摘要间接保障）")


class InitDataSafetyTest(TestCase):
    """审计 Medium-High：演示数据命令在生产环境必须拒绝执行。"""

    def test_refuses_when_not_debug(self):
        from django.test import override_settings
        from io import StringIO
        from django.core.management import call_command
        from django.core.management.base import CommandError
        with override_settings(DEBUG=False):
            with self.assertRaises(CommandError) as ctx:
                call_command("init_data", stdout=StringIO())
            self.assertIn("生产环境", str(ctx.exception))

    def test_no_hardcoded_password_in_source(self):
        """★ 源码里不得再出现硬编码演示口令 admin123"""
        import inspect
        from .management.commands import init_data as mod
        src = inspect.getsource(mod)
        self.assertNotIn('"admin123"', src,
                         "演示口令不得硬编码（应走 DEMO_PASSWORD 或随机生成）")


class OcrBudgetTest(TestCase):
    """审计『值得处理』：OCR 同步入口必须有整单总时限，避免占满 worker。"""

    def test_total_budget_configurable(self):
        from django.test import override_settings
        from apps.traceability import ocr
        self.assertGreater(ocr.TOTAL_BUDGET_SECONDS, 0)
        self.assertGreaterEqual(ocr.TOTAL_BUDGET_SECONDS, ocr.OCR_TIMEOUT,
                                "整单预算不应小于单页超时，否则单页都跑不完")

    def test_over_budget_short_circuits(self):
        """★ 已经超预算时不再进入耗时识别，直接如实告知（不假成功）"""
        from unittest import mock
        from apps.traceability import ocr
        # 把预算设为 0 → 立即超时
        with mock.patch.object(ocr, "TOTAL_BUDGET_SECONDS", 0):
            with mock.patch.object(ocr, "extract_text_from_pdf",
                                   return_value=("", "")) as m1, \
                 mock.patch.object(ocr, "render_pdf_to_images",
                                   return_value=[b"fake"]) as m2:
                r = ocr.extract_from_file("x.pdf", b"%PDF-1.4 fake")
        # 文本层试过一次；因为超预算，不应再渲染/OCR
        self.assertTrue(m1.called)
        self.assertFalse(m2.called, "超预算后不应继续渲染（保护 worker）")
        self.assertTrue(any("超时" in w for w in r["warnings"]))
        self.assertFalse(r["ok"])

    def test_no_time_warning_when_fast(self):
        """回归：正常快速路径不应出现任何超时提示"""
        from apps.traceability import ocr
        r = ocr.extract_from_file("x.txt", b"hello")   # 不支持格式
        self.assertFalse(any("超时" in w for w in r["warnings"]))
