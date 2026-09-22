"""
业务视图
- ViewSet 提供 CRUD
- 合格证签发时自动存证
- 公众验真免登录
- 二维码生成
"""
import io
import qrcode
from django.conf import settings
from django.http import HttpResponse
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (Producer, ProductBatch, TestReport, Certificate,
                     EvidenceRecord, PublicQueryRecord, SupplyDemand)
from .serializers import (ProducerSerializer, ProductBatchSerializer,
                          TestReportSerializer, CertificateSerializer,
                          EvidenceRecordSerializer, SupplyDemandSerializer)
from . import chain, services


class BaseViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]


class ProducerViewSet(BaseViewSet):
    queryset = Producer.objects.all()
    serializer_class = ProducerSerializer

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @action(detail=True, methods=["post"])
    def audit(self, request, pk=None):
        """监管审核主体"""
        obj = self.get_object()
        new_status = request.data.get("audit_status")
        if new_status not in dict(Producer.AuditStatus.choices):
            return Response({"code": 400, "message": "审核状态非法"},
                            status=status.HTTP_400_BAD_REQUEST)
        obj.audit_status = new_status
        obj.save(update_fields=["audit_status", "updated_at"])
        return Response({"code": 200, "message": "审核完成",
                         "data": ProducerSerializer(obj).data})


class ProductBatchViewSet(BaseViewSet):
    queryset = ProductBatch.objects.select_related("producer").all()
    serializer_class = ProductBatchSerializer

    @action(detail=True, methods=["post"])
    def anchor(self, request, pk=None):
        """对批次执行存证"""
        obj = self.get_object()
        record = services.anchor_batch(obj)
        return Response({"code": 200, "message": "批次存证成功",
                         "data": EvidenceRecordSerializer(record).data})


class TestReportViewSet(BaseViewSet):
    queryset = TestReport.objects.select_related("batch").all()
    serializer_class = TestReportSerializer

    def perform_create(self, serializer):
        report = serializer.save()
        services.anchor_report(report)

    @action(detail=True, methods=["post"])
    def anchor(self, request, pk=None):
        obj = self.get_object()
        record = services.anchor_report(obj)
        return Response({"code": 200, "message": "报告存证成功",
                         "data": EvidenceRecordSerializer(record).data})


class CertificateViewSet(BaseViewSet):
    queryset = Certificate.objects.select_related("batch", "report").all()
    serializer_class = CertificateSerializer

    def perform_create(self, serializer):
        cert = serializer.save(issuer=self.request.user)
        services.anchor_certificate(cert)

    @action(detail=True, methods=["post"])
    def issue(self, request, pk=None):
        """签发合格证：置为已签发并重新存证"""
        obj = self.get_object()
        obj.status = Certificate.Status.ISSUED
        obj.save(update_fields=["status", "updated_at"])
        services.anchor_certificate(obj)
        return Response({"code": 200, "message": "签发成功",
                         "data": CertificateSerializer(obj).data})

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        """作废合格证"""
        obj = self.get_object()
        obj.status = Certificate.Status.REVOKED
        obj.save(update_fields=["status", "updated_at"])
        EvidenceRecord.objects.create(
            biz_type=EvidenceRecord.BizType.REVOKE, biz_id=obj.id,
            biz_no=obj.cert_no,
            data_hash=services.sha256_hex(f"revoke|{obj.cert_no}"),
            tx_hash=services._mock_tx_hash(),
            block_height=100000 + obj.id, chain_status="success",
            payload=f"作废合格证 {obj.cert_no}")
        return Response({"code": 200, "message": "已作废",
                         "data": CertificateSerializer(obj).data})

    @action(detail=True, methods=["get"])
    def qrcode(self, request, pk=None):
        """生成追溯二维码（指向公众验真页）"""
        obj = self.get_object()
        verify_url = f"{settings.FRONTEND_VERIFY_BASE}/{obj.cert_no}"
        img = qrcode.make(verify_url)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return HttpResponse(buf.getvalue(), content_type="image/png")


class EvidenceViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    queryset = EvidenceRecord.objects.all()
    serializer_class = EvidenceRecordSerializer

    @action(detail=False, methods=["get"], url_path="verify/(?P<cert_no>[^/.]+)")
    def verify(self, request, cert_no=None):
        """按合格证编号做哈希核验（?chain=1 时额外做链上核验）"""
        cert = Certificate.objects.filter(cert_no=cert_no).first()
        if not cert:
            return Response({"code": 404, "message": "合格证不存在"},
                            status=status.HTTP_404_NOT_FOUND)
        check_chain = request.query_params.get("chain") in ("1", "true", "yes")
        return Response({"code": 200, "message": "ok",
                         "data": services.verify_certificate(cert, check_chain=check_chain)})

    @action(detail=False, methods=["get"], url_path="chain-status")
    def chain_status(self, request):
        """查询联盟链连接状态（前端存证页展示用）"""
        return Response({"code": 200, "message": "ok", "data": chain.chain_status()})


class SupplyDemandViewSet(BaseViewSet):
    queryset = SupplyDemand.objects.select_related("certificate", "publisher").all()
    serializer_class = SupplyDemandSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        kind = self.request.query_params.get("kind")
        deal_status = self.request.query_params.get("deal_status")
        if kind:
            qs = qs.filter(kind=kind)
        if deal_status:
            qs = qs.filter(deal_status=deal_status)
        return qs

    def perform_create(self, serializer):
        serializer.save(publisher=self.request.user)

    @action(detail=True, methods=["post"])
    def match(self, request, pk=None):
        """标记为已对接（产销衔接动作）"""
        obj = self.get_object()
        obj.deal_status = SupplyDemand.DealStatus.MATCHED
        obj.save(update_fields=["deal_status", "updated_at"])
        return Response({"code": 200, "message": "已标记为对接成功",
                         "data": SupplyDemandSerializer(obj).data})


# ---------------------------------------------------------------
# 公众验真（免登录）
# ---------------------------------------------------------------
class PublicVerifyView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, cert_no):
        cert = Certificate.objects.select_related(
            "batch", "batch__producer", "report").filter(cert_no=cert_no).first()
        if not cert:
            PublicQueryRecord.objects.create(cert_no=cert_no, result="not_found",
                                             ip=self._ip(request))
            return Response({"code": 404, "message": "未查询到该合格证",
                             "data": None}, status=status.HTTP_404_NOT_FOUND)

        verify = services.verify_certificate(cert, check_chain=True)
        PublicQueryRecord.objects.create(
            cert_no=cert_no, result="trusted" if verify["trusted"] else "untrusted",
            ip=self._ip(request))

        data = {
            "cert_no": cert.cert_no,
            "status": cert.status,
            "status_display": cert.get_status_display(),
            "producer_name": cert.producer_name,
            "product_name": cert.product_name,
            "quantity_kg": str(cert.quantity_kg),
            "issue_date": cert.issue_date,
            "valid_until": cert.valid_until,
            "batch_no": cert.batch.batch_no if cert.batch_id else "",
            "origin": cert.batch.origin if cert.batch_id else "",
            "variety": cert.batch.variety if cert.batch_id else "",
            "report_no": cert.report.report_no if cert.report_id else "",
            "conclusion": cert.report.get_conclusion_display() if cert.report_id else "",
            "cert_hash": cert.cert_hash,
            "is_anchored": cert.is_anchored,
            "trusted": verify["trusted"],
            "verify_message": verify["message"],
            "stored_hash": verify["stored_hash"],
            "current_hash": verify["current_hash"],
            "chain_verified": verify.get("chain_verified", False),
            "chain_detail": verify.get("chain_detail", ""),
        }
        # 附带存证记录
        record = EvidenceRecord.objects.filter(
            biz_type=EvidenceRecord.BizType.CERTIFICATE, biz_id=cert.id).first()
        if record:
            data["tx_hash"] = record.tx_hash
            data["block_height"] = record.block_height
        return Response({"code": 200, "message": "ok", "data": data})

    @staticmethod
    def _ip(request):
        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        return xff.split(",")[0] if xff else request.META.get("REMOTE_ADDR", "")


# ---------------------------------------------------------------
# 数据看板统计
# ---------------------------------------------------------------
class DashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.db.models import Sum
        data = {
            "producer_count": Producer.objects.count(),
            "approved_producer_count": Producer.objects.filter(
                audit_status=Producer.AuditStatus.APPROVED).count(),
            "batch_count": ProductBatch.objects.count(),
            "certificate_count": Certificate.objects.count(),
            "issued_count": Certificate.objects.filter(
                status=Certificate.Status.ISSUED).count(),
            "evidence_count": EvidenceRecord.objects.count(),
            "supply_count": SupplyDemand.objects.filter(
                kind=SupplyDemand.Kind.SUPPLY).count(),
            "demand_count": SupplyDemand.objects.filter(
                kind=SupplyDemand.Kind.DEMAND).count(),
            "total_weight": str(ProductBatch.objects.aggregate(
                s=Sum("weight_kg"))["s"] or 0),
        }
        return Response({"code": 200, "message": "ok", "data": data})