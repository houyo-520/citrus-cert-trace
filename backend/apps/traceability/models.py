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
    # 行政区划代码（6 位，如 510106 = 四川省成都市金牛区）
    # 用于合格证编号的前 6 位：区域码 + 日期 + 流水
    region_code = models.CharField("行政区划代码", max_length=6, blank=True, default="",
                                   help_text="6 位数字，如 510106；留空则编号区域码为 000000")
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
    # ---- OCR 识别溯源 ----
    # 记录「这条数据是机器认出来的还是人敲的」。检测结论这类字段直接决定
    # 农产品能否流通，出问题时必须能回答「当时是谁/什么填的」。
    ocr_method = models.CharField("识别方式", max_length=20, blank=True, default="",
                                  help_text="pdf-text / ocr-pdf / ocr-image / manual")
    ocr_confidence = models.CharField("识别置信度", max_length=10, blank=True, default="")
    ocr_text = models.TextField("识别原文", blank=True, default="",
                                help_text="OCR/文本层原文片段，便于事后核对")
    remark = models.CharField("备注", max_length=255, blank=True, default="")

    class Meta:
        db_table = "biz_test_report"
        verbose_name = "检测报告"
        verbose_name_plural = "检测报告"
        ordering = ["-id"]
        # ★ 索引（原 M10）：按批次反查报告是热点（存证、时间轴、看板）。
        indexes = [
            models.Index(fields=["batch"]),
            models.Index(fields=["conclusion"]),
        ]

    def __str__(self):
        return self.report_no


# ---------------------------------------------------------------
# 4. 合格证（核心！含可信存证哈希）
# ---------------------------------------------------------------
class Certificate(BaseModel):
    """
    合格证。

    编号分配策略（★防占号）：
      编号 = 区域码(6) + 日期(8) + 流水(5)，共 19 位，对标农业农村部实际格式。
      草稿/待审核阶段 cert_no 为空，**只有正式签发时才分配编号**。
      否则大量草稿会吃掉流水号，导致正式号码跳跃、废号无法回收。
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "草稿"            # 已建档，未提交，尚未占号
        PENDING = "pending", "待审核"       # 已提交，等待监管签发
        ISSUED = "issued", "已签发"         # 已分配编号 + 已存证上链
        SUSPENDED = "suspended", "暂停使用"  # 监督检查不合格，暂停对外流通
        REVOKED = "revoked", "已作废"

    cert_no = models.CharField("合格证编号", max_length=40, unique=True,
                               null=True, blank=True,
                               help_text="19 位：区域码6+日期8+流水5；草稿阶段为空，签发时自动分配")
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
                              default=Status.DRAFT)

    # ---- 可信存证核心字段 ----
    cert_hash = models.CharField("合格证哈希(SHA-256)", max_length=64, blank=True, default="")
    is_anchored = models.BooleanField("是否已存证", default=False)
    anchored_at = models.DateTimeField("存证时间", null=True, blank=True)
    # ★ Merkle 树根哈希：把「批次/报告/证/检查」等多份证据哈希汇聚成单一根，
    #   一张证只需在链上存这一个根，即可代表整棵证据树（空间效率 + 防篡改定位）。
    merkle_root = models.CharField("Merkle 根哈希", max_length=64, blank=True, default="")

    # ---- 监督检查联动 ----
    # 现场检查判定不合格时置为「暂停使用」，与签发状态区分开：
    # 暂停不是作废（证还在，只是暂缓流通）。整改复查通过后，
    # 原证从 suspended 直接转 revoked 终态（不复原），需重新申领新批次。
    suspend_reason = models.CharField("暂停原因", max_length=255, blank=True, default="")

    issuer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                               null=True, blank=True, verbose_name="签发人",
                               related_name="issued_certs")
    remark = models.CharField("备注", max_length=255, blank=True, default="")

    class Meta:
        db_table = "biz_certificate"
        verbose_name = "合格证"
        verbose_name_plural = "合格证"
        ordering = ["-id"]
        # ★ 索引（原 M10）：状态过滤（看板/验真/预警）与批次反查都走索引。
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["batch"]),
            models.Index(fields=["issue_date"]),
        ]

    def __str__(self):
        return self.cert_no or f"草稿#{self.pk}"

    @property
    def is_active(self) -> bool:
        """是否为「当前有效、可流通」的合格证。"""
        return self.status == self.Status.ISSUED


# ---------------------------------------------------------------
# 5. 存证记录（哈希上链/入库留痕）
# ---------------------------------------------------------------
class EvidenceRecord(BaseModel):
    class BizType(models.TextChoices):
        BATCH = "batch", "生产批次"
        REPORT = "report", "检测报告"
        CERTIFICATE = "certificate", "合格证"
        REVOKE = "revoke", "作废记录"
        INSPECTION = "inspection", "监督检查"
        MERKLE = "merkle", "Merkle 根"

    biz_type = models.CharField("业务类型", max_length=20, choices=BizType.choices)
    biz_id = models.IntegerField("业务ID")
    biz_no = models.CharField("业务编号", max_length=60, blank=True, default="")
    data_hash = models.CharField("数据哈希", max_length=64)
    tx_hash = models.CharField("交易哈希(模拟)", max_length=80, blank=True, default="")
    block_height = models.IntegerField("区块高度(模拟)", default=0)
    chain_status = models.CharField("存证状态", max_length=20, default="success")
    payload = models.TextField("存证原文(规范化字段)", blank=True, default="")
    # ★ 追加式版本（B3）：同一业务对象的存证可有多条历史版本，
    #   version 递增、is_current 标记哪条是「当前生效版本」。
    #   重存证/换盐迁移时追加新版本、归档旧版本，而非覆盖 ——
    #   既保留篡改可检出（当前版本与链上/重算比对），又不堵死合法迁移。
    version = models.IntegerField("版本号", default=1)
    is_current = models.BooleanField("是否当前版本", default=True)

    class Meta:
        db_table = "chain_evidence_record"
        verbose_name = "存证记录"
        verbose_name_plural = "存证记录"
        ordering = ["-id"]
        # ★ 条件唯一：同一 (biz_type, biz_id) 只能有**一条** is_current=True 的记录；
        #   历史版本（is_current=False）可以有任意多条。这是追加式版本的核心约束。
        constraints = [
            models.UniqueConstraint(
                fields=["biz_type", "biz_id"],
                condition=models.Q(is_current=True),
                name="uniq_evidence_current"),
        ]
        # ★ 索引（原 M10）：热点查询 filter(biz_type=…, biz_id=…) 走索引不扫全表。
        indexes = [
            models.Index(fields=["biz_type", "biz_id"]),
            models.Index(fields=["biz_no"]),
            models.Index(fields=["biz_type", "biz_id", "is_current"]),
        ]

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
        # ★ 索引（原 M10）：限流按 (ip, created_at) 查询，而该表每次扫码都在长，
        #   无索引就是全表扫 → 越刷越慢。
        indexes = [
            models.Index(fields=["ip", "created_at"]),
        ]


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
    # ★ 对手方供需单（原 Minor 修复）：原实现只在打匹配度时用到 counterpart_id，
    #   未落库 —— 「一次对接留痕」实际丢掉了「跟谁对接」。这里显式建模，
    #   让意向单能还原「我方单 ←→ 对方单」的双向关系。
    counterpart = models.ForeignKey(SupplyDemand, on_delete=models.SET_NULL,
                                    null=True, blank=True, verbose_name="对手方供需单",
                                    related_name="counterpart_intents")
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
# 9. 监管现场监督检查记录（执法闭环）
# ---------------------------------------------------------------
class InspectionRecord(BaseModel):
    """
    监管现场监督检查记录。

    为什么需要这张表：
      本系统原有的「检测报告」是**第三方检测机构**出具的技术报告；
      而《农产品质量安全承诺达标合格证管理办法》中，农业农村部门的
      **现场监督检查**是另一条独立主线，也是「监管人员」角色的核心业务。
      没有它，regulator 只能审核主体、签发合格证，业务单薄。

    执法闭环（★这是本表的设计重点）：
      检查 -> 评分 + 问题描述
           -> 整改要求 + 整改期限
           -> 整改状态：待整改 -> 整改中 -> 已整改 / 逾期
           -> 复查负责人 + 现场照片
           -> 罚款金额 + 罚款理由 + 法律依据
      检查结论为「不合格」时，自动把关联合格证置为「暂停使用」——
      让监督检查与可信存证链路串起来。
    """

    class CheckType(models.TextChoices):
        ROUTINE = "routine", "日常检查"
        SPECIAL = "special", "专项检查"
        COMPLAINT = "complaint", "投诉核查"
        SAMPLING = "sampling", "监督抽样"

    class CheckResult(models.TextChoices):
        QUALIFIED = "qualified", "合格"
        UNQUALIFIED = "unqualified", "不合格"
        RECHECK = "recheck", "待复检"

    class RectifyStatus(models.TextChoices):
        NOT_REQUIRED = "not_required", "无需整改"
        PENDING = "pending", "待整改"
        ONGOING = "ongoing", "整改中"
        DONE = "done", "已整改"
        OVERDUE = "overdue", "已逾期"
        RECHECK_FAILED = "recheck_failed", "复查仍不合格"

    # ---- 关联对象 ----
    certificate = models.ForeignKey(Certificate, on_delete=models.CASCADE,
                                    verbose_name="受检合格证", related_name="inspections")
    # ---- 执行人 ----
    inspector = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                  null=True, blank=True, verbose_name="检查人",
                                  related_name="inspections")
    inspector_name = models.CharField("检查人姓名", max_length=50, blank=True, default="")
    inspector_no = models.CharField("执法证号/工号", max_length=50, blank=True, default="")
    department = models.CharField("检查部门", max_length=100, blank=True, default="")
    check_time = models.DateTimeField("检查时间", null=True, blank=True)
    check_location = models.CharField("检查地点", max_length=200, blank=True, default="")
    # ---- 检查内容 ----
    check_type = models.CharField("检查类型", max_length=20, choices=CheckType.choices,
                                  default=CheckType.ROUTINE)
    check_result = models.CharField("检查结果", max_length=20,
                                    choices=CheckResult.choices,
                                    default=CheckResult.RECHECK)
    check_score = models.DecimalField("检查评分", max_digits=5, decimal_places=2,
                                      default=0,
                                      help_text="百分制评分")
    problem_description = models.TextField("发现问题", blank=True, default="")
    # ---- 整改闭环 ----
    rectify_requirement = models.TextField("整改要求", blank=True, default="")
    rectify_deadline = models.DateField("整改期限", null=True, blank=True)
    rectify_status = models.CharField("整改状态", max_length=20,
                                      choices=RectifyStatus.choices,
                                      default=RectifyStatus.NOT_REQUIRED)
    rectify_time = models.DateTimeField("整改完成时间", null=True, blank=True)
    rectify_note = models.TextField("整改说明", blank=True, default="")
    follow_up_person = models.CharField("复查负责人", max_length=50, blank=True, default="")
    # ---- 处罚 ----
    penalty_amount = models.DecimalField("罚款金额(元)", max_digits=12, decimal_places=2,
                                         default=0)
    penalty_reason = models.CharField("处罚事由", max_length=255, blank=True, default="")
    legal_basis = models.CharField("法律依据", max_length=255, blank=True, default="")
    # ---- 证据材料（存文件路径，逗号分隔）----
    attachments = models.TextField("现场证据材料", blank=True, default="",
                                   help_text="多个文件路径用英文逗号分隔")
    # ---- 结论存证 ----
    # 检查结论上链后，事后任何一方都无法否认「当时判了不合格」：
    # 对监管方是履职留痕，对被检方是程序正义保障。
    record_hash = models.CharField("检查结论哈希(SHA-256)", max_length=64,
                                   blank=True, default="")
    is_anchored = models.BooleanField("是否已存证", default=False)
    anchored_at = models.DateTimeField("存证时间", null=True, blank=True)
    remark = models.CharField("备注", max_length=255, blank=True, default="")

    class Meta:
        db_table = "biz_inspection_record"
        verbose_name = "监督检查记录"
        verbose_name_plural = "监督检查记录"
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["check_result"]),
            models.Index(fields=["rectify_status"]),
            models.Index(fields=["certificate"]),
        ]

    def __str__(self):
        return f"检查#{self.id}({self.get_check_result_display()})"

    @property
    def is_overdue(self) -> bool:
        """是否已逾期：有期限、未整改完、且当前日期已超过期限。"""
        from datetime import date as _date
        if not self.rectify_deadline:
            return False
        if self.rectify_status in (self.RectifyStatus.DONE,
                                   self.RectifyStatus.NOT_REQUIRED,
                                   self.RectifyStatus.RECHECK_FAILED):
            return False
        return _date.today() > self.rectify_deadline

    def refresh_overdue(self, save: bool = True) -> bool:
        """
        刷新逾期状态（幂等）。

        逾期是「随时间自然发生」的状态，不可能靠人工点击维护，
        因此在列表查询/详情查看时按当前日期动态判定并落库。
        """
        if self.is_overdue and self.rectify_status != self.RectifyStatus.OVERDUE:
            self.rectify_status = self.RectifyStatus.OVERDUE
            if save:
                self.save(update_fields=["rectify_status", "updated_at"])
            return True
        return False


# ---------------------------------------------------------------
# 10. 站内消息通知（解决"审核完没人知道"）
# ---------------------------------------------------------------
class Notification(BaseModel):
    """
    站内消息。

    为什么需要这张表：
      系统原来的流程是**单向哑流程** —— 种植主体提交合格证送审后，
      监管签发或不签发，种植主体不会收到任何反馈；监管检查判定不合格，
      被检方也不知道；产销意向单发起后对方不回来看就永远不知道。
      所有"需要别人知道"的节点都靠人主动刷新页面去发现。

      消息表把「业务动作」翻译成「给谁的提醒」，是流程闭环的最后一环。

    设计要点：
      - **收件人显式建模**（recipient 外键），不靠角色广播：
        同一个角色可能有多个用户（producer / producer2），
        广播会导致「别人家的事也推给我」。
      - **category 分类**：前端可按类型筛选、给不同图标与颜色。
      - **link_route + link_id**：点消息直接跳到对应业务对象，
        而不是只告诉用户"有事发生了"却不告诉他去哪儿看。
      - **read_at 用时间戳而非布尔**：既能判断未读，也保留了「何时读的」这一审计信息。
    """

    class Category(models.TextChoices):
        CERT_SUBMIT = "cert_submit", "合格证送审"
        CERT_ISSUE = "cert_issue", "合格证签发"
        CERT_REJECT = "cert_reject", "合格证驳回"
        CERT_REVOKE = "cert_revoke", "合格证作废"
        CERT_SUSPEND = "cert_suspend", "合格证暂停"
        INSPECTION = "inspection", "监督检查"
        RECTIFY = "rectify", "整改复查"
        MATCH = "match", "产销对接"
        AUDIT = "audit", "主体审核"
        SYSTEM = "system", "系统通知"

    class Level(models.TextChoices):
        INFO = "info", "提示"
        SUCCESS = "success", "成功"
        WARNING = "warning", "提醒"
        DANGER = "danger", "重要"

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                  verbose_name="收件人", related_name="notifications")
    category = models.CharField("消息类型", max_length=20,
                                choices=Category.choices, default=Category.SYSTEM)
    level = models.CharField("重要级别", max_length=20,
                             choices=Level.choices, default=Level.INFO)
    title = models.CharField("标题", max_length=120)
    content = models.TextField("正文", blank=True, default="")
    # 点击跳转：link_route 是前端路由名（如 certificate），link_id 是对象 id
    link_route = models.CharField("跳转路由", max_length=40, blank=True, default="")
    link_id = models.IntegerField("跳转对象ID", null=True, blank=True)
    link_label = models.CharField("跳转按钮文案", max_length=40, blank=True, default="")
    # 业务来源（便于追溯这条消息是哪次操作产生的）
    biz_type = models.CharField("关联业务类型", max_length=40, blank=True, default="")
    biz_no = models.CharField("关联业务编号", max_length=60, blank=True, default="")
    read_at = models.DateTimeField("读取时间", null=True, blank=True)
    # 发起动作的人（展示「谁触发了这条消息」）
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                              null=True, blank=True, verbose_name="触发人",
                              related_name="sent_notifications")

    class Meta:
        db_table = "sys_notification"
        verbose_name = "站内消息"
        verbose_name_plural = "站内消息"
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["recipient", "read_at"]),
            models.Index(fields=["category"]),
        ]

    def __str__(self):
        return f"→{self.recipient_id}: {self.title}"

    @property
    def is_read(self) -> bool:
        return self.read_at is not None


# ---------------------------------------------------------------
# 11. 操作审计日志（谁 / 何时 / 对哪条数据 / 做了什么）
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
        INSPECT = "inspect", "监督检查"
        RECTIFY = "rectify", "整改复查"

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