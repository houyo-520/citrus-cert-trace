"""
业务序列化器
"""
from rest_framework import serializers

from .models import (Producer, ProductBatch, TestReport, Certificate,
                     EvidenceRecord, PublicQueryRecord, SupplyDemand)


class ProducerSerializer(serializers.ModelSerializer):
    audit_status_display = serializers.CharField(source="get_audit_status_display",
                                                 read_only=True)

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

    class Meta:
        model = TestReport
        fields = "__all__"


class CertificateSerializer(serializers.ModelSerializer):
    batch_no = serializers.CharField(source="batch.batch_no", read_only=True)
    report_no = serializers.CharField(source="report.report_no", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Certificate
        fields = "__all__"
        read_only_fields = ["cert_hash", "is_anchored", "anchored_at", "issuer"]


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

    class Meta:
        model = SupplyDemand
        fields = "__all__"
        read_only_fields = ["publisher"]