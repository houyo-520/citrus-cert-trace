"""
业务视图

权限设计（详见 permissions.py）：
  - 接口级：按 action 细分，审核/签发/作废 仅监管；生产数据写入需种植主体
  - 数据级：RoleScopedQuerySetMixin 统一做可见范围隔离
审计设计（详见 audit.py）：
  - 关键动作（审核/签发/作废/存证/对接/登录）全部落 OperationLog
"""
import io

import qrcode
from django.conf import settings
from django.db.models import Sum
from django.http import HttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Role

from . import audit, chain, matching, services
from .models import (Certificate, EvidenceRecord, MatchIntent, OperationLog,
                     Producer, ProductBatch, PublicQueryRecord, SupplyDemand,
                     TestReport)
from .permissions import (CanTrade, IsRegulator,
                          ReadOnlyOrProducerOrRegulator, ReadOnlyOrRegulator,
                          RoleScopedQuerySetMixin)
from .serializers import (CertificateSerializer, EvidenceRecordSerializer,
                          MatchIntentSerializer, OperationLogSerializer,
                          ProducerSerializer, ProductBatchSerializer,
                          SupplyDemandSerializer, TestReportSerializer)


class BaseViewSet(viewsets.ModelViewSet):
    """所有业务 ViewSet 的基类：默认要求登录。"""
    permission_classes = [IsAuthenticated]


# ===============================================================
# 生产主体
# ===============================================================
class ProducerViewSet(RoleScopedQuerySetMixin, BaseViewSet):
    queryset = Producer.objects.select_related("owner").all()
    serializer_class = ProducerSerializer

    def scope_queryset(self, qs, user):
        """种植主体只看自己创建的；收购商只看已审核通过的。"""
        if user.role == Role.PRODUCER:
            return qs.filter(owner=user)
        if user.role == Role.BUYER:
            return qs.filter(audit_status=Producer.AuditStatus.APPROVED)
        return qs.none()

    def get_permissions(self):
        # 审核仅监管；其余写操作需种植主体或监管
        if self.action == "audit":
            return [IsRegulator()]
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [ReadOnlyOrProducerOrRegulator()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        obj = serializer.save(owner=self.request.user)
        audit.log_operation(self.request, OperationLog.Action.CREATE,
                            target_type="生产主体", target_id=obj.id,
                            target_no=obj.name, detail=f"新增主体 {obj.name}")

    def perform_update(self, serializer):
        obj = serializer.save()
        audit.log_operation(self.request, OperationLog.Action.UPDATE,
                            target_type="生产主体", target_id=obj.id,
                            target_no=obj.name, detail=f"修改主体 {obj.name}")

    def perform_destroy(self, instance):
        audit.log_operation(self.request, OperationLog.Action.DELETE,
                            target_type="生产主体", target_id=instance.id,
                            target_no=instance.name, detail=f"删除主体 {instance.name}")
        instance.delete()

    @action(detail=True, methods=["post"])
    def audit(self, request, pk=None):
        """监管审核主体"""
        obj = self.get_object()
        new_status = request.data.get("audit_status")
        if new_status not in dict(Producer.AuditStatus.choices):
            return Response({"code": 400, "message": "审核状态非法"},
                            status=status.HTTP_400_BAD_REQUEST)
        old = obj.get_audit_status_display()
        obj.audit_status = new_status
        obj.save(update_fields=["audit_status", "updated_at"])
        audit.log_operation(request, OperationLog.Action.AUDIT,
                            target_type="生产主体", target_id=obj.id,
                            target_no=obj.name,
                            detail=f"{obj.name} 审核 {old} -> {obj.get_audit_status_display()}")
        return Response({"code": 200, "message": "审核完成",
                         "data": ProducerSerializer(obj).data})


# ===============================================================
# 生产批次
# ===============================================================
class ProductBatchViewSet(RoleScopedQuerySetMixin, BaseViewSet):
    queryset = ProductBatch.objects.select_related("producer").all()
    serializer_class = ProductBatchSerializer

    def scope_queryset(self, qs, user):
        if user.role == Role.PRODUCER:
            return qs.filter(producer__owner=user)
        if user.role == Role.BUYER:
            return qs.filter(producer__audit_status=Producer.AuditStatus.APPROVED)
        return qs.none()

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy", "anchor"):
            return [ReadOnlyOrProducerOrRegulator()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        obj = serializer.save()
        audit.log_operation(self.request, OperationLog.Action.CREATE,
                            target_type="生产批次", target_id=obj.id,
                            target_no=obj.batch_no, detail=f"新增批次 {obj.batch_no}")

    def perform_destroy(self, instance):
        audit.log_operation(self.request, OperationLog.Action.DELETE,
                            target_type="生产批次", target_id=instance.id,
                            target_no=instance.batch_no, detail=f"删除批次 {instance.batch_no}")
        instance.delete()

    @action(detail=True, methods=["post"])
    def anchor(self, request, pk=None):
        """对批次执行存证"""
        obj = self.get_object()
        record = services.anchor_batch(obj)
        audit.log_operation(request, OperationLog.Action.ANCHOR,
                            target_type="生产批次", target_id=obj.id,
                            target_no=obj.batch_no,
                            detail=f"批次存证 tx={record.tx_hash[:18]}…")
        return Response({"code": 200, "message": "批次存证成功",
                         "data": EvidenceRecordSerializer(record).data})


# ===============================================================
# 检测报告
# ===============================================================
class TestReportViewSet(RoleScopedQuerySetMixin, BaseViewSet):
    queryset = TestReport.objects.select_related("batch", "batch__producer").all()
    serializer_class = TestReportSerializer

    def scope_queryset(self, qs, user):
        if user.role == Role.PRODUCER:
            return qs.filter(batch__producer__owner=user)
        if user.role == Role.BUYER:
            # 收购商只看结论为合格的报告
            return qs.filter(conclusion=TestReport.Conclusion.QUALIFIED)
        return qs.none()

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy", "anchor"):
            return [ReadOnlyOrProducerOrRegulator()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        report = serializer.save()
        services.anchor_report(report)
        audit.log_operation(self.request, OperationLog.Action.CREATE,
                            target_type="检测报告", target_id=report.id,
                            target_no=report.report_no,
                            detail=f"新增检测报告 {report.report_no} 并自动存证")

    def perform_destroy(self, instance):
        audit.log_operation(self.request, OperationLog.Action.DELETE,
                            target_type="检测报告", target_id=instance.id,
                            target_no=instance.report_no,
                            detail=f"删除检测报告 {instance.report_no}")
        instance.delete()

    @action(detail=True, methods=["post"])
    def anchor(self, request, pk=None):
        obj = self.get_object()
        record = services.anchor_report(obj)
        audit.log_operation(request, OperationLog.Action.ANCHOR,
                            target_type="检测报告", target_id=obj.id,
                            target_no=obj.report_no, detail=f"报告存证 {obj.report_no}")
        return Response({"code": 200, "message": "报告存证成功",
                         "data": EvidenceRecordSerializer(record).data})


# ===============================================================
# 合格证（核心）
# ===============================================================
class CertificateViewSet(RoleScopedQuerySetMixin, BaseViewSet):
    queryset = Certificate.objects.select_related("batch", "report", "issuer").all()
    serializer_class = CertificateSerializer

    def scope_queryset(self, qs, user):
        if user.role == Role.PRODUCER:
            return qs.filter(batch__producer__owner=user)
        if user.role == Role.BUYER:
            # 收购商只能看到已签发的合格证（未签发/已作废不对收购商开放）
            return qs.filter(status=Certificate.Status.ISSUED)
        return qs.none()

    def get_permissions(self):
        # 签发/作废是监管专属动作
        if self.action in ("issue", "revoke"):
            return [IsRegulator()]
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [ReadOnlyOrProducerOrRegulator()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        cert = serializer.save(issuer=self.request.user)
        services.anchor_certificate(cert)
        audit.log_operation(self.request, OperationLog.Action.CREATE,
                            target_type="合格证", target_id=cert.id,
                            target_no=cert.cert_no,
                            detail=f"新建合格证 {cert.cert_no} 并自动存证")

    def perform_destroy(self, instance):
        audit.log_operation(self.request, OperationLog.Action.DELETE,
                            target_type="合格证", target_id=instance.id,
                            target_no=instance.cert_no, detail=f"删除合格证 {instance.cert_no}")
        instance.delete()

    @action(detail=True, methods=["post"])
    def issue(self, request, pk=None):
        """签发合格证：置为已签发并重新存证"""
        obj = self.get_object()
        obj.status = Certificate.Status.ISSUED
        obj.save(update_fields=["status", "updated_at"])
        services.anchor_certificate(obj)
        audit.log_operation(request, OperationLog.Action.ISSUE,
                            target_type="合格证", target_id=obj.id,
                            target_no=obj.cert_no, detail=f"签发合格证 {obj.cert_no}")
        return Response({"code": 200, "message": "签发成功",
                         "data": CertificateSerializer(obj).data})

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        """作废合格证（同时留一条作废存证）"""
        obj = self.get_object()
        obj.status = Certificate.Status.REVOKED
        obj.save(update_fields=["status", "updated_at"])
        reason = request.data.get("reason", "")
        record = services.anchor_revoke(obj, reason=reason)
        audit.log_operation(request, OperationLog.Action.REVOKE,
                            target_type="合格证", target_id=obj.id,
                            target_no=obj.cert_no,
                            detail=f"作废合格证 {obj.cert_no}"
                                   + (f"，原因：{reason}" if reason else ""))
        return Response({"code": 200, "message": "已作废",
                         "data": CertificateSerializer(obj).data,
                         "evidence": EvidenceRecordSerializer(record).data})

    @action(detail=True, methods=["get"])
    def qrcode(self, request, pk=None):
        """生成追溯二维码（指向公众验真页）"""
        obj = self.get_object()
        verify_url = f"{settings.FRONTEND_VERIFY_BASE}/{obj.cert_no}"
        img = qrcode.make(verify_url)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return HttpResponse(buf.getvalue(), content_type="image/png")


# ===============================================================
# 存证记录
# ===============================================================
class EvidenceViewSet(RoleScopedQuerySetMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    queryset = EvidenceRecord.objects.all()
    serializer_class = EvidenceRecordSerializer

    def scope_queryset(self, qs, user):
        """种植主体/收购商只能看与自己相关业务的存证。"""
        if user.role == Role.PRODUCER:
            cert_ids = Certificate.objects.filter(
                batch__producer__owner=user).values_list("id", flat=True)
            return qs.filter(biz_type=EvidenceRecord.BizType.CERTIFICATE,
                             biz_id__in=list(cert_ids))
        if user.role == Role.BUYER:
            cert_ids = Certificate.objects.filter(
                status=Certificate.Status.ISSUED).values_list("id", flat=True)
            return qs.filter(biz_type=EvidenceRecord.BizType.CERTIFICATE,
                             biz_id__in=list(cert_ids))
        return qs.none()

    @action(detail=False, methods=["get"], url_path="verify/(?P<cert_no>[^/.]+)")
    def verify(self, request, cert_no=None):
        """按合格证编号做哈希核验（?chain=1 时额外做链上核验）"""
        cert = Certificate.objects.filter(cert_no=cert_no).first()
        if not cert:
            return Response({"code": 404, "message": "合格证不存在"},
                            status=status.HTTP_404_NOT_FOUND)
        check_chain = request.query_params.get("chain") in ("1", "true", "yes")
        result = services.verify_certificate(cert, check_chain=check_chain)
        audit.log_operation(request, OperationLog.Action.VERIFY,
                            target_type="合格证", target_id=cert.id,
                            target_no=cert.cert_no,
                            detail=f"哈希核验 {'可信' if result['trusted'] else '不可信'}"
                                   f"（链上{'已核验' if check_chain else '未核验'}）")
        return Response({"code": 200, "message": "ok", "data": result})

    @action(detail=False, methods=["get"], url_path="chain-status")
    def chain_status(self, request):
        """查询联盟链连接状态（前端存证页展示用）"""
        return Response({"code": 200, "message": "ok", "data": chain.chain_status()})


# ===============================================================
# 产销供需（含智能匹配）
# ===============================================================
class SupplyDemandViewSet(RoleScopedQuerySetMixin, BaseViewSet):
    queryset = SupplyDemand.objects.select_related(
        "certificate", "publisher", "batch").all()
    serializer_class = SupplyDemandSerializer

    def scope_queryset(self, qs, user):
        """
        产销信息本身就是"对外发布"的，所以：
          - 种植主体：自己发布的 + 全部求购（要能看到买家需求）
          - 收购商：自己发布的 + 全部供应（要能看到卖家货源）
        """
        from django.db.models import Q
        if user.role == Role.PRODUCER:
            return qs.filter(Q(publisher=user) | Q(kind=SupplyDemand.Kind.DEMAND))
        if user.role == Role.BUYER:
            return qs.filter(Q(publisher=user) | Q(kind=SupplyDemand.Kind.SUPPLY))
        return qs.none()

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy",
                           "match", "recommend", "create_intent"):
            return [CanTrade()]
        return [IsAuthenticated()]

    def get_queryset(self):
        qs = super().get_queryset()
        kind = self.request.query_params.get("kind")
        deal_status = self.request.query_params.get("deal_status")
        keyword = self.request.query_params.get("keyword")
        if kind:
            qs = qs.filter(kind=kind)
        if deal_status:
            qs = qs.filter(deal_status=deal_status)
        if keyword:
            from django.db.models import Q
            qs = qs.filter(Q(title__icontains=keyword)
                           | Q(product_name__icontains=keyword)
                           | Q(variety__icontains=keyword)
                           | Q(region__icontains=keyword))
        return qs

    def perform_create(self, serializer):
        obj = serializer.save(publisher=self.request.user)
        audit.log_operation(self.request, OperationLog.Action.CREATE,
                            target_type="产销供需", target_id=obj.id,
                            target_no=obj.title,
                            detail=f"发布{obj.get_kind_display()}信息 {obj.title}")

    def perform_destroy(self, instance):
        audit.log_operation(self.request, OperationLog.Action.DELETE,
                            target_type="产销供需", target_id=instance.id,
                            target_no=instance.title, detail=f"删除供需信息 {instance.title}")
        instance.delete()

    @action(detail=True, methods=["post"])
    def match(self, request, pk=None):
        """标记为已对接（保留原语义，同时落审计日志）"""
        obj = self.get_object()
        obj.deal_status = SupplyDemand.DealStatus.MATCHED
        obj.save(update_fields=["deal_status", "updated_at"])
        audit.log_operation(request, OperationLog.Action.MATCH,
                            target_type="产销供需", target_id=obj.id,
                            target_no=obj.title, detail=f"标记已对接：{obj.title}")
        return Response({"code": 200, "message": "已标记为对接成功",
                         "data": SupplyDemandSerializer(obj).data})

    @action(detail=True, methods=["get"])
    def recommend(self, request, pk=None):
        """
        ★ 智能匹配推荐：为该供需单推荐匹配度最高的对手方。
        返回按匹配度降序的候选列表，含各因子得分明细。
        """
        obj = self.get_object()
        limit = int(request.query_params.get("limit", 5))
        return Response({"code": 200, "message": "ok",
                         "data": matching.recommend_for(obj, limit=limit)})

    @action(detail=True, methods=["post"], url_path="intent")
    def create_intent(self, request, pk=None):
        """
        ★ 发起产销意向单：一次对接 = 一条留痕记录，同时写入匹配度评分。
        """
        obj = self.get_object()
        counterpart_id = request.data.get("counterpart_id")
        quantity = request.data.get("quantity_kg", 0)
        price = request.data.get("price", 0)

        score, detail = 0, ""
        if counterpart_id:
            cp = SupplyDemand.objects.filter(id=counterpart_id).first()
            if cp is not None:
                try:
                    if obj.kind == SupplyDemand.Kind.SUPPLY:
                        r = matching.score_match(obj, cp)
                    else:
                        r = matching.score_match(cp, obj)
                    score, detail = r["score"], r["detail"]
                except ValueError:
                    pass

        intent = MatchIntent.objects.create(
            supply=obj,
            initiator=request.user,
            quantity_kg=quantity or obj.quantity_kg,
            price=price or obj.price,
            match_score=score,
            score_detail=detail,
            message=request.data.get("message", ""),
        )
        audit.log_operation(request, OperationLog.Action.MATCH,
                            target_type="产销意向单", target_id=intent.id,
                            target_no=obj.title,
                            detail=f"发起对接意向（匹配度 {score}）：{obj.title}")
        return Response({"code": 200, "message": "意向已提交",
                         "data": MatchIntentSerializer(intent).data})


# ===============================================================
# 产销意向单
# ===============================================================
class MatchIntentViewSet(RoleScopedQuerySetMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    queryset = MatchIntent.objects.select_related(
        "supply", "initiator").all()
    serializer_class = MatchIntentSerializer

    def scope_queryset(self, qs, user):
        """自己发起的意向 + 自己发布的供需单上收到的意向。"""
        from django.db.models import Q
        if user.role in (Role.PRODUCER, Role.BUYER):
            return qs.filter(Q(initiator=user) | Q(supply__publisher=user))
        return qs.none()

    def get_queryset(self):
        qs = super().get_queryset()
        status_ = self.request.query_params.get("status")
        if status_:
            qs = qs.filter(status=status_)
        return qs

    @action(detail=True, methods=["post"])
    def respond(self, request, pk=None):
        """供需单发布方回应意向：接受 / 拒绝 / 完成"""
        obj = self.get_object()
        new_status = request.data.get("status")
        if new_status not in dict(MatchIntent.Status.choices):
            return Response({"code": 400, "message": "状态非法"},
                            status=status.HTTP_400_BAD_REQUEST)
        obj.status = new_status
        obj.save(update_fields=["status", "updated_at"])

        # 接受时同步把供需单置为已对接
        if new_status == MatchIntent.Status.ACCEPTED:
            sd = obj.supply
            sd.deal_status = SupplyDemand.DealStatus.MATCHED
            sd.save(update_fields=["deal_status", "updated_at"])

        audit.log_operation(request, OperationLog.Action.MATCH,
                            target_type="产销意向单", target_id=obj.id,
                            target_no=obj.supply.title,
                            detail=f"意向回应：{obj.get_status_display()}")
        return Response({"code": 200, "message": "已回应",
                         "data": MatchIntentSerializer(obj).data})


# ===============================================================
# 操作日志
# ===============================================================
class OperationLogViewSet(RoleScopedQuerySetMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    queryset = OperationLog.objects.select_related("operator").all()
    serializer_class = OperationLogSerializer

    def scope_queryset(self, qs, user):
        """种植主体/收购商只能看自己的操作记录。"""
        return qs.filter(operator=user)

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get("action"):
            qs = qs.filter(action=p["action"])
        if p.get("operator"):
            qs = qs.filter(operator_name__icontains=p["operator"])
        if p.get("target_type"):
            qs = qs.filter(target_type=p["target_type"])
        if p.get("keyword"):
            qs = qs.filter(detail__icontains=p["keyword"])
        return qs


# ===============================================================
# 公众验真（免登录）
# ===============================================================
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
        return audit.get_client_ip(request)


# ===============================================================
# 数据看板统计（按角色差异化）
# ===============================================================
class DashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        data = self._stats_for(user)
        # 附带角色信息，便于前端按角色渲染不同卡片
        data["role"] = user.role
        data["role_display"] = user.get_role_display()
        return Response({"code": 200, "message": "ok", "data": data})

    def _stats_for(self, user):
        """不同角色看到不同维度的统计 —— 这是角色差异化的直接体现。"""
        base = {
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
            "intent_count": MatchIntent.objects.count(),
            "log_count": OperationLog.objects.count(),
        }
        if user.role in (Role.ADMIN, Role.REGULATOR):
            base["scope_label"] = "全量数据（监管视角）"
            base["pending_audit_count"] = Producer.objects.filter(
                audit_status=Producer.AuditStatus.PENDING).count()
            return base

        if user.role == Role.PRODUCER:
            my_batches = ProductBatch.objects.filter(producer__owner=user)
            my_certs = Certificate.objects.filter(batch__producer__owner=user)
            base.update({
                "scope_label": "我的生产数据（种植主体视角）",
                "producer_count": Producer.objects.filter(owner=user).count(),
                "approved_producer_count": Producer.objects.filter(
                    owner=user, audit_status=Producer.AuditStatus.APPROVED).count(),
                "batch_count": my_batches.count(),
                "certificate_count": my_certs.count(),
                "issued_count": my_certs.filter(
                    status=Certificate.Status.ISSUED).count(),
                "total_weight": str(my_batches.aggregate(
                    s=Sum("weight_kg"))["s"] or 0),
                "my_supply_count": SupplyDemand.objects.filter(
                    publisher=user, kind=SupplyDemand.Kind.SUPPLY).count(),
                "intent_count": MatchIntent.objects.filter(initiator=user).count(),
            })
            return base

        if user.role == Role.BUYER:
            base.update({
                "scope_label": "可交易数据（收购商视角）",
                "producer_count": Producer.objects.filter(
                    audit_status=Producer.AuditStatus.APPROVED).count(),
                "certificate_count": Certificate.objects.filter(
                    status=Certificate.Status.ISSUED).count(),
                "my_demand_count": SupplyDemand.objects.filter(
                    publisher=user, kind=SupplyDemand.Kind.DEMAND).count(),
                "intent_count": MatchIntent.objects.filter(initiator=user).count(),
            })
            return base
        return base


# ===============================================================
# 角色权限矩阵（供前端展示 + 答辩演示）
# ===============================================================
class RoleMatrixView(APIView):
    """返回系统的角色-权限矩阵，前端「权限说明」页直接渲染成表格。"""
    permission_classes = [IsAuthenticated]

    MATRIX = [
        {"resource": "生产主体", "admin": "全部", "regulator": "全部",
         "producer": "仅自己创建的", "buyer": "仅已审核通过的"},
        {"resource": "生产批次", "admin": "全部", "regulator": "全部",
         "producer": "仅自己的", "buyer": "仅已审核主体的"},
        {"resource": "检测报告", "admin": "全部", "regulator": "全部",
         "producer": "仅自己的", "buyer": "只读、仅合格"},
        {"resource": "合格证", "admin": "全部", "regulator": "全部",
         "producer": "自己的可增改", "buyer": "只读、仅已签发"},
        {"resource": "存证记录", "admin": "全部", "regulator": "全部",
         "producer": "仅自己的", "buyer": "只读、仅已签发证"},
        {"resource": "产销供需", "admin": "全部", "regulator": "全部",
         "producer": "自己的+全部求购", "buyer": "自己的+全部供应"},
        {"resource": "产销意向单", "admin": "全部", "regulator": "全部",
         "producer": "自己发起/收到的", "buyer": "自己发起/收到的"},
        {"resource": "操作日志", "admin": "全部", "regulator": "全部",
         "producer": "仅自己的", "buyer": "仅自己的"},
    ]

    ACTION_MATRIX = [
        {"action": "主体审核", "admin": True, "regulator": True,
         "producer": False, "buyer": False},
        {"action": "合格证签发", "admin": True, "regulator": True,
         "producer": False, "buyer": False},
        {"action": "合格证作废", "admin": True, "regulator": True,
         "producer": False, "buyer": False},
        {"action": "批次/报告存证", "admin": True, "regulator": True,
         "producer": True, "buyer": False},
        {"action": "发布供需信息", "admin": True, "regulator": True,
         "producer": True, "buyer": True},
        {"action": "发起对接意向", "admin": True, "regulator": True,
         "producer": True, "buyer": True},
    ]

    def get(self, request):
        return Response({
            "code": 200, "message": "ok",
            "data": {
                "resource_matrix": self.MATRIX,
                "action_matrix": self.ACTION_MATRIX,
                "roles": [
                    {"key": "admin", "label": "系统管理员"},
                    {"key": "regulator", "label": "监管人员"},
                    {"key": "producer", "label": "种植主体"},
                    {"key": "buyer", "label": "收购商"},
                ],
            },
        })
