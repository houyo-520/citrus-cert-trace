"""
业务序列化器
"""
from rest_framework import serializers

from .models import (Producer, ProductBatch, TestReport, Certificate,
                     EvidenceRecord, PublicQueryRecord, SupplyDemand,
                     MatchIntent, OperationLog, InspectionRecord,
                     Notification)
from apps.accounts.models import LoginRecord

# 检测报告附件允许的扩展名与大小上限（10 MB）
REPORT_FILE_EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx"}
REPORT_FILE_MAX_MB = 10


class ProducerSerializer(serializers.ModelSerializer):
    audit_status_display = serializers.CharField(source="get_audit_status_display",
                                                 read_only=True)
    owner_name = serializers.CharField(source="owner.username", read_only=True)
    # ★ 主体信用分与等级（监管看列表时快速识别高风险主体；纯计算不落库）
    credit_score = serializers.SerializerMethodField()
    credit_grade = serializers.SerializerMethodField()

    class Meta:
        model = Producer
        fields = "__all__"
        # ★ 安全红线：audit_status 是「监管行为」的产物，绝不能让主体自改
        #   —— 否则生产者 PATCH 一条 {"audit_status":"approved"} 就绕过监管审核
        #   （写权限是 ReadOnlyOrProducerOrRegulator，比 audit 动作宽松）。
        #   审核只能走 POST /producer/{id}/audit/（仅 IsRegulator）。
        # owner 由后端按登录用户写入，前端不可指定（防冒领/跨租户）。
        read_only_fields = ["owner", "audit_status"]

    def _credit(self, obj):
        # 每次序列化只算一次，score 与 grade 复用同一结果（避免二次查询）
        cached = getattr(obj, "_credit_cache", None)
        if cached is None:
            from . import credit
            cached = credit.score_producer(obj)
            obj._credit_cache = cached
        return cached

    def get_credit_score(self, obj):
        return self._credit(obj)["score"]

    def get_credit_grade(self, obj):
        return self._credit(obj)["grade"]


class ProductBatchSerializer(serializers.ModelSerializer):
    producer_name = serializers.CharField(source="producer.name", read_only=True)

    class Meta:
        model = ProductBatch
        fields = "__all__"

    def validate_producer(self, value):
        """
        ★ 防跨租户挂靠（M1）：主体只能把批次挂到**自己名下**的主体上。

        原本 fields="__all__" 让 producer 完全可写，主体 A 可把批次挂到主体 B
        的 id 上（跨租户注入）。这里在序列化层校验归属：
          - 监管/管理员：可为任意主体建档（监管代录场景）；
          - 种植主体：只能选 owner 是自己的主体。
        """
        request = self.context.get("request")
        user = getattr(request, "user", None)
        from apps.accounts.models import Role
        if user and getattr(user, "role", None) == Role.PRODUCER:
            if value.owner_id != user.id:
                raise serializers.ValidationError("只能关联属于自己的生产主体")
        return value


class TestReportSerializer(serializers.ModelSerializer):
    batch_no = serializers.CharField(source="batch.batch_no", read_only=True)
    conclusion_display = serializers.CharField(source="get_conclusion_display",
                                               read_only=True)
    # 附件的可点击地址（file 字段本身返回的是 /media/reports/xxx 相对路径，
    # 前端拼上 API 源即可下载；这里额外给一个带文件名的展示字段）
    file_name = serializers.SerializerMethodField()
    file_url = serializers.SerializerMethodField()
    # ★ OCR 识别来源（便于审计：这个字段是人填的还是机器认的）
    ocr_method_display = serializers.SerializerMethodField()

    class Meta:
        model = TestReport
        fields = "__all__"
        read_only_fields = ["ocr_method", "ocr_confidence", "ocr_text"]

    def validate_batch(self, value):
        """★ 防跨租户挂靠（M1）：报告只能挂到自己名下的批次上。"""
        request = self.context.get("request")
        user = getattr(request, "user", None)
        from apps.accounts.models import Role
        if user and getattr(user, "role", None) == Role.PRODUCER:
            if value.producer_id and value.producer.owner_id != user.id:
                raise serializers.ValidationError("只能关联属于自己的生产批次")
        return value

    def get_ocr_method_display(self, obj):
        return {
            "pdf-text": "PDF 文本层提取",
            "ocr-pdf": "扫描件 OCR 识别",
            "ocr-image": "图片 OCR 识别",
            "manual": "手工录入",
        }.get(getattr(obj, "ocr_method", "") or "", "")

    def get_file_name(self, obj):
        """
        附件原始文件名（无附件返回空串）。

        ★ 不能用 obj.file.name —— Django 的存储层会为同名文件自动加随机后缀
        （上传 report.pdf 实际落盘为 report_rBcOEvO.pdf），直接回显磁盘名
        会让用户看到一串乱码后缀，误以为文件被改过。这里剥掉存储层加的后缀。
        """
        if not obj.file:
            return ""
        import os
        import re
        name = obj.file.name.rsplit("/", 1)[-1]
        stem, ext = os.path.splitext(name)
        # 只剥 storage 生成的 `_7位随机串` 尾巴，不碰用户名字里本就有的下划线
        stem = re.sub(r"_[A-Za-z0-9]{7}$", "", stem)
        return f"{stem}{ext}"

    def get_file_url(self, obj):
        """附件访问地址（无附件返回空串）。"""
        if not obj.file:
            return ""
        try:
            return obj.file.url
        except ValueError:
            return ""

    def validate_file(self, value):
        """
        附件校验：限制类型与大小。

        前端 el-upload 已做一层（体验层），后端这层才是可信边界 ——
        绕过前端直接 POST 也必须被拦住。
        """
        if value is None:
            return value
        import os
        ext = os.path.splitext(value.name)[1].lower()
        if ext not in REPORT_FILE_EXTS:
            raise serializers.ValidationError(
                f"仅支持 {'/'.join(sorted(e.lstrip('.') for e in REPORT_FILE_EXTS))} "
                f"格式，当前为 {ext or '未知类型'}")
        limit = REPORT_FILE_MAX_MB * 1024 * 1024
        if value.size > limit:
            raise serializers.ValidationError(
                f"附件不得超过 {REPORT_FILE_MAX_MB} MB，当前 "
                f"{value.size / 1024 / 1024:.1f} MB")
        return value


class CertificateSerializer(serializers.ModelSerializer):
    batch_no = serializers.CharField(source="batch.batch_no", read_only=True)
    report_no = serializers.CharField(source="report.report_no", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    issuer_name = serializers.CharField(source="issuer.username", read_only=True)
    producer_name_display = serializers.SerializerMethodField()
    inspection_count = serializers.SerializerMethodField()
    latest_inspection_result = serializers.SerializerMethodField()
    cert_no_parsed = serializers.SerializerMethodField()

    class Meta:
        model = Certificate
        fields = "__all__"
        # 编号由后端按「区域码+日期+流水」在签发时分配，前端不可填
        # merkle_root 由后端在签发存证时汇聚证据树计算，前端不可填
        # ★ status 必须只读：否则 PATCH {"status":"issued"} 就绕过了 issue 的
        #   监管校验、取号与上链，造出一张「无编号却已签发」的不自洽证（C2）。
        #   状态流转只能走 submit/issue/reject/revoke 等专用动作。
        read_only_fields = ["cert_no", "cert_hash", "is_anchored", "anchored_at",
                            "merkle_root", "issuer", "status", "suspend_reason"]

    def validate_batch(self, value):
        """★ 防跨租户挂靠（M1）：合格证只能挂到自己名下的批次上。"""
        request = self.context.get("request")
        user = getattr(request, "user", None)
        from apps.accounts.models import Role
        if user and getattr(user, "role", None) == Role.PRODUCER:
            if value.producer_id and value.producer.owner_id != user.id:
                raise serializers.ValidationError("只能关联属于自己的生产批次")
        return value

    def validate_report(self, value):
        """★ 防跨租户挂靠（M1）：检测报告须属于自己名下的主体。"""
        if value is None:
            return value
        request = self.context.get("request")
        user = getattr(request, "user", None)
        from apps.accounts.models import Role
        if user and getattr(user, "role", None) == Role.PRODUCER:
            if value.batch_id and value.batch.producer_id and \
                    value.batch.producer.owner_id != user.id:
                raise serializers.ValidationError("只能关联属于自己的检测报告")
        return value

    def get_producer_name_display(self, obj):
        """主体名称：优先取存证时固化的快照，其次实时取批次关联的主体。"""
        if obj.producer_name:
            return obj.producer_name
        if obj.batch_id and obj.batch.producer_id:
            return obj.batch.producer.name
        return ""

    def get_inspection_count(self, obj):
        # ★ 用预取缓存避免 N+1（原 M12）：视图 queryset 已 prefetch_related("inspections")
        #   （见 CertificateViewSet.queryset），此处读缓存不再各打一条 COUNT。
        return len(obj.inspections.all())

    def get_latest_inspection_result(self, obj):
        """最近一次监督检查结论 —— 列表页据此显示「检查不合格」标签。"""
        # ★ 复用预取缓存：按 check_time/id 排序取最新（避免每条再查一次库）
        items = list(obj.inspections.all())
        if not items:
            return ""
        latest = max(items, key=lambda r: (r.check_time or r.created_at, r.id))
        return latest.get_check_result_display()

    def get_cert_no_parsed(self, obj):
        """把 19 位编号拆成「区域码 / 日期 / 流水」，前端可展示构成。"""
        from . import certno
        return certno.parse_cert_no(obj.cert_no)


class InspectionRecordSerializer(serializers.ModelSerializer):
    check_type_display = serializers.CharField(source="get_check_type_display",
                                               read_only=True)
    check_result_display = serializers.CharField(source="get_check_result_display",
                                                 read_only=True)
    rectify_status_display = serializers.CharField(source="get_rectify_status_display",
                                                   read_only=True)
    cert_no = serializers.CharField(source="certificate.cert_no", read_only=True)
    producer_name = serializers.CharField(source="certificate.batch.producer.name",
                                          read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    attachment_list = serializers.SerializerMethodField()

    class Meta:
        model = InspectionRecord
        fields = "__all__"
        read_only_fields = ["inspector", "inspector_name", "record_hash",
                            "is_anchored", "anchored_at"]

    def get_attachment_list(self, obj):
        raw = (obj.attachments or "").strip()
        return [p.strip() for p in raw.split(",") if p.strip()]


class EvidenceRecordSerializer(serializers.ModelSerializer):
    biz_type_display = serializers.CharField(source="get_biz_type_display", read_only=True)

    class Meta:
        model = EvidenceRecord
        fields = "__all__"


class SupplyDemandSerializer(serializers.ModelSerializer):
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    deal_status_display = serializers.CharField(source="get_deal_status_display",
                                                read_only=True)
    cert_no = serializers.CharField(source="certificate.cert_no", read_only=True)
    publisher_name = serializers.CharField(source="publisher.username", read_only=True)
    intent_count = serializers.SerializerMethodField()

    class Meta:
        model = SupplyDemand
        fields = "__all__"
        read_only_fields = ["publisher"]

    def get_intent_count(self, obj):
        return obj.intents.count()


class MatchIntentSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    initiator_name = serializers.CharField(source="initiator.username", read_only=True)
    supply_title = serializers.CharField(source="supply.title", read_only=True)
    supply_kind = serializers.CharField(source="supply.kind", read_only=True)
    # ★ 对手方供需单标题（原 Minor 修复：意向单现在能还原双向关系）
    counterpart_title = serializers.CharField(source="counterpart.title",
                                              read_only=True, default="")

    class Meta:
        model = MatchIntent
        fields = "__all__"
        read_only_fields = ["initiator", "match_score", "score_detail"]


class OperationLogSerializer(serializers.ModelSerializer):
    action_display = serializers.CharField(source="get_action_display", read_only=True)

    class Meta:
        model = OperationLog
        fields = "__all__"


class LoginRecordSerializer(serializers.ModelSerializer):
    """登录历史序列化器（登录监控用，含地理位置）。"""
    role_display = serializers.SerializerMethodField()

    class Meta:
        model = LoginRecord
        fields = "__all__"

    def get_role_display(self, obj):
        role_map = {
            "admin": "系统管理员", "regulator": "监管人员",
            "producer": "种植主体", "buyer": "收购商",
        }
        return role_map.get(obj.role, obj.role or "")


class NotificationSerializer(serializers.ModelSerializer):
    category_display = serializers.CharField(source="get_category_display", read_only=True)
    level_display = serializers.CharField(source="get_level_display", read_only=True)
    is_read = serializers.BooleanField(read_only=True)
    actor_name = serializers.CharField(source="actor.username", read_only=True)

    class Meta:
        model = Notification
        fields = "__all__"
        # 全部字段由后端生成，前端只能改「已读」状态（走专门 action）
        read_only_fields = ["recipient", "category", "level", "title", "content",
                            "link_route", "link_id", "link_label", "biz_type",
                            "biz_no", "read_at", "actor"]
