"""
业务序列化器
"""
from rest_framework import serializers

from .models import (Producer, ProductBatch, TestReport, Certificate,
                     EvidenceRecord, PublicQueryRecord, SupplyDemand,
                     MatchIntent, OperationLog, InspectionRecord)

# 检测报告附件允许的扩展名与大小上限（10 MB）
REPORT_FILE_EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx"}
REPORT_FILE_MAX_MB = 10


class ProducerSerializer(serializers.ModelSerializer):
    audit_status_display = serializers.CharField(source="get_audit_status_display",
                                                 read_only=True)
    owner_name = serializers.CharField(source="owner.username", read_only=True)

    class Meta:
        model = Producer
        fields = "__all__"
        read_only_fields = ["owner"]


class ProductBatchSerializer(serializers.ModelSerializer):
    producer_name = serializers.CharField(source="producer.name", read_only=True)

    class Meta:
        model = ProductBatch
        fields = "__all__"


class TestReportSerializer(serializers.ModelSerializer):
    batch_no = serializers.CharField(source="batch.batch_no", read_only=True)
    conclusion_display = serializers.CharField(source="get_conclusion_display",
                                               read_only=True)
    # 附件的可点击地址（file 字段本身返回的是 /media/reports/xxx 相对路径，
    # 前端拼上 API 源即可下载；这里额外给一个带文件名的展示字段）
    file_name = serializers.SerializerMethodField()
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = TestReport
        fields = "__all__"

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
        read_only_fields = ["cert_no", "cert_hash", "is_anchored", "anchored_at", "issuer"]

    def get_producer_name_display(self, obj):
        """主体名称：优先取存证时固化的快照，其次实时取批次关联的主体。"""
        if obj.producer_name:
            return obj.producer_name
        if obj.batch_id and obj.batch.producer_id:
            return obj.batch.producer.name
        return ""

    def get_inspection_count(self, obj):
        return obj.inspections.count()

    def get_latest_inspection_result(self, obj):
        """最近一次监督检查结论 —— 列表页据此显示「检查不合格」标签。"""
        latest = obj.inspections.order_by("-check_time", "-id").first()
        if not latest:
            return ""
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

    class Meta:
        model = MatchIntent
        fields = "__all__"
        read_only_fields = ["initiator", "match_score", "score_detail"]


class OperationLogSerializer(serializers.ModelSerializer):
    action_display = serializers.CharField(source="get_action_display", read_only=True)

    class Meta:
        model = OperationLog
        fields = "__all__"
