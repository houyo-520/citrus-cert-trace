from django.contrib import admin
from .models import (Producer, ProductBatch, TestReport, Certificate,
                     EvidenceRecord, PublicQueryRecord, SupplyDemand)


@admin.register(Producer)
class ProducerAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "contact", "phone", "region", "audit_status")
    list_filter = ("audit_status", "region")
    search_fields = ("name", "contact", "phone")


@admin.register(ProductBatch)
class ProductBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "batch_no", "producer", "product_name", "variety",
                    "harvest_date", "weight_kg")
    search_fields = ("batch_no", "product_name", "variety")


@admin.register(TestReport)
class TestReportAdmin(admin.ModelAdmin):
    list_display = ("id", "report_no", "batch", "test_org", "conclusion", "test_date")
    list_filter = ("conclusion",)


@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    list_display = ("id", "cert_no", "producer_name", "product_name", "status",
                    "is_anchored", "cert_hash")
    list_filter = ("status", "is_anchored")
    search_fields = ("cert_no", "producer_name")


@admin.register(EvidenceRecord)
class EvidenceRecordAdmin(admin.ModelAdmin):
    list_display = ("id", "biz_type", "biz_id", "biz_no", "data_hash",
                    "tx_hash", "block_height")
    list_filter = ("biz_type", "chain_status")


@admin.register(SupplyDemand)
class SupplyDemandAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "title", "product_name", "quantity_kg",
                    "price", "region", "deal_status")
    list_filter = ("kind", "deal_status")


@admin.register(PublicQueryRecord)
class PublicQueryRecordAdmin(admin.ModelAdmin):
    list_display = ("id", "cert_no", "result", "ip", "created_at")