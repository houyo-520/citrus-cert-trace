"""
核心业务模型：生产主体 / 生产批次 / 检测报告 / 合格证 / 存证记录 / 产销供需

数据主线：
  生产主体 → 生产批次(harvest batch) → 检测报告 → 合格证(含 hash) → 存证记录 → 二维码验真
  产销供需 独立挂在生产主体/批次上，实现"产销衔接"
"""
from django.conf import settings
from django.db import models


class BaseModel(models.Model):
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        abstract = True


# ---------------------------------------------------------------
# 1. 生产主体（种植户 / 合作社 / 企业）
# ---------------------------------------------------------------
class Producer(BaseModel):
    class AuditStatus(models.TextChoices):
        PENDING = "pending", "待审核"
        APPROVED = "approved", "已通过"
        REJECTED = "rejected", "已驳回"

    name = models.CharField("主体名称", max_length=100)
    credit_code = models.CharField("统一社会信用代码", max_length=30, blank=True, default="")
    contact = models.CharField("联系人", max_length=50)
    phone = models.CharField("联系电话", max_length=20)
    address = models.CharField("产地地址", max_length=200, blank=True, default="")
    region = models.CharField("所属区域", max_length=100, blank=True, default="")
    audit_status = models.CharField("审核状态", max_length=20,
                                    choices=AuditStatus.choices,
                                    default=AuditStatus.PENDING)
    remark = models.CharField("备注", max_length=255, blank=True, default="")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                              null=True, blank=True, verbose_name="关联用户",
                              related_name="producers")

    class Meta:
        db_table = "biz_producer"
        verbose_name = "生产主体"
        verbose_name_plural = "生产主体"
        ordering = ["-id"]

    def __str__(self):
        return self.name


# ---------------------------------------------------------------
# 2. 生产批次
# ---------------------------------------------------------------
class ProductBatch(BaseModel):
    batch_no = models.CharField("批次编号", max_length=40, unique=True)
    producer = models.ForeignKey(Producer, on_delete=models.CASCADE,
                                 verbose_name="生产主体", related_name="batches")
    product_name = models.CharField("产品名称", max_length=80, default="柑橘")
    variety = models.CharField("品种", max_length=80, blank=True, default="")
    origin = models.CharField("产地", max_length=120, blank=True, default="")
    harvest_date = models.DateField("采收日期", null=True, blank=True)
    weight_kg = models.DecimalField("批次重量(kg)", max_digits=12, decimal_places=2,
                                    default=0)
    plant_area_mu = models.DecimalField("种植面积(亩)", max_digits=10, decimal_places=2,
                                        default=0)
    # 农事记录（施肥用药等）
    farming_record = models.TextField("农事记录", blank=True, default="")
    status = models.CharField("批次状态", max_length=20, default="normal")

    class Meta:
        db_table = "biz_product_batch"
        verbose_name = "生产批次"
        verbose_name_plural = "生产批次"
        ordering = ["-id"]

    def __str__(self):
        return self.batch_no


# ---------------------------------------------------------------
# 3. 检测报告
# ---------------------------------------------------------------
class TestReport(BaseModel):
    class Conclusion(models.TextChoices):
        QUALIFIED = "qualified", "合格"
        UNQUALIFIED = "unqualified", "不合格"

    report_no = models.CharField("报告编号", max_length=40, unique=True)
    batch = models.ForeignKey(ProductBatch, on_delete=models.CASCADE,
                              verbose_name="所属批次", related_name="reports")
    test_org = models.CharField("检测机构", max_length=100, blank=True, default="")
    test_date = models.DateField("检测日期", null=True, blank=True)
    conclusion = models.CharField("检测结论", max_length=20,
                                  choices=Conclusion.choices,
                                  default=Conclusion.QUALIFIED)
    pesticide_residue = models.CharField("农药残留结果", max_length=100,
                                         blank=True, default="符合标准")
    heavy_metal = models.CharField("重金属结果", max_length=100,
                                   blank=True, default="符合标准")
    file = models.FileField("报告附件", upload_to="reports/", null=True, blank=True)
    report_hash = models.CharField("报告哈希", max_length=64, blank=True, default="")
    remark = models.CharField("备注", max_length=255, blank=True, default="")

    class Meta:
        db_table = "biz_test_report"
        verbose_name = "检测报告"
        verbose_name_plural = "检测报告"
        ordering = ["-id"]

    def __str__(self):
        return self.report_no


# ---------------------------------------------------------------
# 4. 合格证（核心！含可信存证哈希）
# ---------------------------------------------------------------
class Certificate(BaseModel):
    class Status(models.TextChoices):
        PENDING = "pending", "待审核"
        ISSUED = "issued", "已签发"
        REVOKED = "revoked", "已作废"

    cert_no = models.CharField("合格证编号", max_length=40, unique=True)
    batch = models.ForeignKey(ProductBatch, on_delete=models.CASCADE,
                              verbose_name="生产批次", related_name="certificates")
    report = models.ForeignKey(TestReport, on_delete=models.SET_NULL, null=True, blank=True,
                               verbose_name="检测报告", related_name="certificates")
    producer_name = models.CharField("主体名称", max_length=100, blank=True, default="")
    product_name = models.CharField("产品名称", max_length=80, default="柑橘")
    quantity_kg = models.DecimalField("开具数量(kg)", max_digits=12, decimal_places=2,
                                      default=0)
    issue_date = models.DateField("签发日期", null=True, blank=True)
    valid_until = models.DateField("有效期至", null=True, blank=True)
    status = models.CharField("状态", max_length=20, choices=Status.choices,
                              default=Status.PENDING)

    # ---- 可信存证核心字段 ----
    cert_hash = models.CharField("合格证哈希(SHA-256)", max_length=64, blank=True, default="")
    is_anchored = models.BooleanField("是否已存证", default=False)
    anchored_at = models.DateTimeField("存证时间", null=True, blank=True)

    issuer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                               null=True, blank=True, verbose_name="签发人",
                               related_name="issued_certs")
    remark = models.CharField("备注", max_length=255, blank=True, default="")

    class Meta:
        db_table = "biz_certificate"
        verbose_name = "合格证"
        verbose_name_plural = "合格证"
        ordering = ["-id"]

    def __str__(self):
        return self.cert_no


# ---------------------------------------------------------------
# 5. 存证记录（哈希上链/入库留痕）
# ---------------------------------------------------------------
class EvidenceRecord(BaseModel):
    class BizType(models.TextChoices):
        BATCH = "batch", "生产批次"
        REPORT = "report", "检测报告"
        CERTIFICATE = "certificate", "合格证"
        REVOKE = "revoke", "作废记录"

    biz_type = models.CharField("业务类型", max_length=20, choices=BizType.choices)
    biz_id = models.IntegerField("业务ID")
    biz_no = models.CharField("业务编号", max_length=60, blank=True, default="")
    data_hash = models.CharField("数据哈希", max_length=64)
    tx_hash = models.CharField("交易哈希(模拟)", max_length=80, blank=True, default="")
    block_height = models.IntegerField("区块高度(模拟)", default=0)
    chain_status = models.CharField("存证状态", max_length=20, default="success")
    payload = models.TextField("存证原文(规范化字段)", blank=True, default="")

    class Meta:
        db_table = "chain_evidence_record"
        verbose_name = "存证记录"
        verbose_name_plural = "存证记录"
        ordering = ["-id"]

    def __str__(self):
        return f"{self.get_biz_type_display()}#{self.biz_id}"


# ---------------------------------------------------------------
# 6. 公众查询记录
# ---------------------------------------------------------------
class PublicQueryRecord(BaseModel):
    cert_no = models.CharField("查询证书编号", max_length=40)
    result = models.CharField("验真结果", max_length=20, default="")
    ip = models.CharField("来源IP", max_length=50, blank=True, default="")

    class Meta:
        db_table = "pub_query_record"
        verbose_name = "公众查询记录"
        verbose_name_plural = "公众查询记录"
        ordering = ["-id"]


# ---------------------------------------------------------------
# 7. 产销供需信息（差异化亮点：产销衔接）
# ---------------------------------------------------------------
class SupplyDemand(BaseModel):
    class Kind(models.TextChoices):
        SUPPLY = "supply", "供应"
        DEMAND = "demand", "求购"

    class DealStatus(models.TextChoices):
        OPEN = "open", "待对接"
        MATCHED = "matched", "已对接"
        CLOSED = "closed", "已关闭"

    kind = models.CharField("类型", max_length=10, choices=Kind.choices,
                            default=Kind.SUPPLY)
    title = models.CharField("标题", max_length=120)
    product_name = models.CharField("产品名称", max_length=80, default="柑橘")
    variety = models.CharField("品种", max_length=80, blank=True, default="")
    quantity_kg = models.DecimalField("数量(kg)", max_digits=12, decimal_places=2, default=0)
    price = models.DecimalField("期望单价(元/kg)", max_digits=10, decimal_places=2,
                                default=0)
    region = models.CharField("地区", max_length=100, blank=True, default="")
    contact = models.CharField("联系人", max_length=50, blank=True, default="")
    phone = models.CharField("联系电话", max_length=20, blank=True, default="")
    # 关联合格证，实现"先验真再对接"
    certificate = models.ForeignKey(Certificate, on_delete=models.SET_NULL,
                                    null=True, blank=True, verbose_name="关联合格证",
                                    related_name="supplies")
    batch = models.ForeignKey(ProductBatch, on_delete=models.SET_NULL,
                              null=True, blank=True, verbose_name="关联批次",
                              related_name="supplies")
    deal_status = models.CharField("对接状态", max_length=20,
                                   choices=DealStatus.choices, default=DealStatus.OPEN)
    description = models.TextField("详细说明", blank=True, default="")
    publisher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                  null=True, blank=True, verbose_name="发布人",
                                  related_name="supplies")

    class Meta:
        db_table = "biz_supply_demand"
        verbose_name = "产销供需"
        verbose_name_plural = "产销供需"
        ordering = ["-id"]

    def __str__(self):
        return self.title


# ---------------------------------------------------------------
# 8. 产销意向单（对接过程留痕：谁对哪条供需发起了对接）
# ---------------------------------------------------------------
class MatchIntent(BaseModel):
    """
    产销对接意向单。

    原实现里「对接」只是把 SupplyDemand.deal_status 改成 matched，
    既没有留痕也无法追溯是谁发起的。这里把它建模成独立实体：
    一次对接 = 一条意向单，供需双方、发起人、数量、成交价全部落库，
    并可回写供需单的对接状态，形成完整链路。
    """

    class Status(models.TextChoices):
        PENDING = "pending", "待确认"
        ACCEPTED = "accepted", "已接受"
        REJECTED = "rejected", "已拒绝"
        DONE = "done", "已完成"

    supply = models.ForeignKey(SupplyDemand, on_delete=models.CASCADE,
                               verbose_name="关联供需单", related_name="intents")
    initiator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                  null=True, blank=True, verbose_name="发起人",
                                  related_name="match_intents")
    quantity_kg = models.DecimalField("意向数量(kg)", max_digits=12, decimal_places=2,
                                      default=0)
    price = models.DecimalField("意向单价(元/kg)", max_digits=10, decimal_places=2,
                                default=0)
    status = models.CharField("意向状态", max_length=20, choices=Status.choices,
                              default=Status.PENDING)
    # 匹配度打分（0-100），由 services.score_match 计算，便于答辩展示算法
    match_score = models.IntegerField("匹配度评分", default=0)
    score_detail = models.CharField("评分明细", max_length=255, blank=True, default="")
    message = models.CharField("对接留言", max_length=255, blank=True, default="")

    class Meta:
        db_table = "biz_match_intent"
        verbose_name = "产销意向单"
        verbose_name_plural = "产销意向单"
        ordering = ["-id"]

    def __str__(self):
        return f"意向#{self.id} -> {self.supply_id}"


# ---------------------------------------------------------------
# 9. 操作审计日志（谁 / 何时 / 对哪条数据 / 做了什么）
# ---------------------------------------------------------------
class OperationLog(BaseModel):
    """
    操作审计日志。

    为什么需要：本系统主题是「可信存证」，但存证记录本身也需要被审计 ——
    谁在什么时候签发了合格证、谁把它作废了、谁触发了存证。
    这张表与链上存证互补：链上锚定的是「数据摘要」，日志锚定的是「操作行为」。
    """

    class Action(models.TextChoices):
        LOGIN = "login", "登录"
        CREATE = "create", "新增"
        UPDATE = "update", "修改"
        DELETE = "delete", "删除"
        AUDIT = "audit", "审核"
        ISSUE = "issue", "签发"
        REVOKE = "revoke", "作废"
        ANCHOR = "anchor", "存证"
        VERIFY = "verify", "验真"
        MATCH = "match", "产销对接"

    operator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                 null=True, blank=True, verbose_name="操作人",
                                 related_name="operation_logs")
    operator_name = models.CharField("操作人账号", max_length=50, blank=True, default="")
    operator_role = models.CharField("操作人角色", max_length=20, blank=True, default="")
    action = models.CharField("操作类型", max_length=20, choices=Action.choices)
    target_type = models.CharField("对象类型", max_length=40, blank=True, default="")
    target_id = models.IntegerField("对象ID", null=True, blank=True)
    target_no = models.CharField("对象编号", max_length=60, blank=True, default="")
    detail = models.CharField("操作详情", max_length=255, blank=True, default="")
    ip = models.CharField("来源IP", max_length=50, blank=True, default="")
    success = models.BooleanField("是否成功", default=True)

    class Meta:
        db_table = "sys_operation_log"
        verbose_name = "操作日志"
        verbose_name_plural = "操作日志"
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["action"]),
            models.Index(fields=["target_type", "target_id"]),
        ]

    def __str__(self):
        return f"{self.operator_name} {self.get_action_display()} {self.target_no}"