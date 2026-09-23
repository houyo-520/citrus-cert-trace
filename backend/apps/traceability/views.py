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
from django.db.models import Q, Sum
from django.http import HttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Role

from . import audit, certno, chain, matching, services
from .models import (Certificate, EvidenceRecord, InspectionRecord,
                     MatchIntent, OperationLog, Producer, ProductBatch,
                     PublicQueryRecord, SupplyDemand, TestReport)
from .permissions import (CanTrade, IsRegulator,
                          ReadOnlyOrProducerOrRegulator, ReadOnlyOrRegulator,
                          RoleScopedQuerySetMixin)
from .serializers import (CertificateSerializer, EvidenceRecordSerializer,
                          InspectionRecordSerializer, MatchIntentSerializer,
                          OperationLogSerializer, ProducerSerializer,
                          ProductBatchSerializer, SupplyDemandSerializer,
                          TestReportSerializer)


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

    def perform_update(self, serializer):
        """
        更新报告（含补传/替换附件）。

        ★ 替换附件时要先删旧文件 —— 否则每换一次就在磁盘上多留一份孤儿，
        演示库跑几轮下来 media/reports/ 会全是没人引用的历史版本。
        """
        old_file = self.get_object().file if self.get_object().file else None
        report = serializer.save()
        # 附件被换掉（或清空）时清理旧文件
        if old_file and old_file.name != (report.file.name if report.file else ""):
            try:
                old_file.delete(save=False)
            except OSError:
                pass

    def perform_destroy(self, instance):
        audit.log_operation(self.request, OperationLog.Action.DELETE,
                            target_type="检测报告", target_id=instance.id,
                            target_no=instance.report_no,
                            detail=f"删除检测报告 {instance.report_no}")
        # ★ 先删附件文件再删记录。
        #   Django 的 FileField 只负责存路径，删除模型实例**不会**自动清理磁盘文件，
        #   不显式删除就会在 media/reports/ 里留下永远没人引用的孤儿文件（实测已积累）。
        #   顺序不能反：删了记录就取不到 file.name 了。
        if instance.file:
            try:
                instance.file.delete(save=False)
            except OSError:
                pass  # 文件已不存在不应阻断删除操作
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
    """
    合格证视图集。

    ★ 编号分配策略（防占号）：
      create  -> 状态 draft，cert_no 为空（不占号）
      submit  -> 状态 pending，仍不占号（等监管签发）
      issue   -> 分配 19 位编号 + 置 issued + 存证上链
      这样草稿不会吃掉流水号，废号也不会产生空号段。
    """
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
        # 签发/作废/驳回是监管专属动作
        if self.action in ("issue", "revoke", "reject"):
            return [IsRegulator()]
        if self.action in ("create", "update", "partial_update", "destroy", "submit"):
            return [ReadOnlyOrProducerOrRegulator()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        """建档：状态为草稿，此时不分配编号（防占号）。"""
        cert = serializer.save(issuer=self.request.user,
                               status=Certificate.Status.DRAFT)
        audit.log_operation(self.request, OperationLog.Action.CREATE,
                            target_type="合格证", target_id=cert.id,
                            target_no=cert.cert_no or f"草稿#{cert.id}",
                            detail=f"新建合格证草稿（{cert.producer_name or '未填主体'}），"
                                   f"待提交审核后分配编号")

    def perform_destroy(self, instance):
        audit.log_operation(self.request, OperationLog.Action.DELETE,
                            target_type="合格证", target_id=instance.id,
                            target_no=instance.cert_no or f"草稿#{instance.id}",
                            detail=f"删除合格证 {instance.cert_no or '草稿#' + str(instance.id)}")
        instance.delete()

    @action(detail=False, methods=["get"], url_path="peek-cert-no")
    def peek_cert_no(self, request):
        """
        预看下一个可用编号（不占用）。

        前端在「签发」对话框里先展示「即将分配 5101062026092300001」，
        让监管人员对编号规则有直观认识；真正分配发生在 issue 时。
        """
        producer_id = request.query_params.get("producer")
        producer = None
        if producer_id:
            producer = Producer.objects.filter(id=producer_id).first()
        if producer is None:
            producer = Producer.objects.order_by("id").first()

        region = getattr(producer, "region_code", "") if producer else ""
        try:
            preview = certno.peek_next_cert_no(producer) if producer else ""
        except ValueError as exc:
            return Response({"code": 400, "message": str(exc)},
                            status=status.HTTP_400_BAD_REQUEST)

        return Response({
            "code": 200, "message": "ok",
            "data": {
                "preview_cert_no": preview,
                "region_code": certno.normalize_region_code(region),
                "producer_id": getattr(producer, "id", None),
                "producer_name": getattr(producer, "name", ""),
                "rule": "区域码(6) + 日期(8) + 流水(5) = 19 位",
            },
        })

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        """提交审核：草稿 -> 待审核（仍不占号）。"""
        obj = self.get_object()
        if obj.status not in (Certificate.Status.DRAFT,
                              Certificate.Status.SUSPENDED):
            return Response({"code": 400, "message": "仅草稿或已暂停的合格证可提交审核"},
                            status=status.HTTP_400_BAD_REQUEST)
        if not obj.report_id:
            return Response({"code": 400, "message": "请先关联合格的检测报告再提交"},
                            status=status.HTTP_400_BAD_REQUEST)
        obj.status = Certificate.Status.PENDING
        obj.save(update_fields=["status", "updated_at"])
        audit.log_operation(request, OperationLog.Action.UPDATE,
                            target_type="合格证", target_id=obj.id,
                            target_no=obj.cert_no or f"草稿#{obj.id}",
                            detail="提交合格证审核（待监管签发）")
        return Response({"code": 200, "message": "已提交审核",
                         "data": CertificateSerializer(obj).data})

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """驳回：待审核 -> 草稿（附驳回意见）。"""
        obj = self.get_object()
        if obj.status != Certificate.Status.PENDING:
            return Response({"code": 400, "message": "仅待审核状态可驳回"},
                            status=status.HTTP_400_BAD_REQUEST)
        reason = (request.data.get("reason") or "").strip()
        obj.status = Certificate.Status.DRAFT
        obj.remark = reason[:255] or obj.remark
        obj.save(update_fields=["status", "remark", "updated_at"])
        audit.log_operation(request, OperationLog.Action.AUDIT,
                            target_type="合格证", target_id=obj.id,
                            target_no=obj.cert_no or f"草稿#{obj.id}",
                            detail="驳回合格证申请" + (f"：{reason}" if reason else ""))
        return Response({"code": 200, "message": "已驳回",
                         "data": CertificateSerializer(obj).data})

    @action(detail=True, methods=["post"])
    def issue(self, request, pk=None):
        """
        ★ 签发合格证：分配 19 位编号 -> 置已签发 -> 存证上链。

        编号只在此时分配（防占号），且用「唯一约束 + 冲突重试」保证并发安全
        （见 certno.save_with_cert_no）。已签发过的证重复调用不会重新取号。
        """
        obj = self.get_object()
        if obj.status == Certificate.Status.REVOKED:
            return Response({"code": 400, "message": "已作废的合格证不可签发"},
                            status=status.HTTP_400_BAD_REQUEST)

        already = bool(obj.cert_no)
        producer = obj.batch.producer if obj.batch_id and obj.batch.producer_id else None

        # 首次签发：分配编号并保存；已分配过则只改状态，避免换号
        if not already:
            if producer is None:
                return Response({"code": 400, "message": "合格证未关联生产主体，无法分配编号"},
                                status=status.HTTP_400_BAD_REQUEST)
            try:
                certno.save_with_cert_no(obj, producer)
            except ValueError as exc:
                return Response({"code": 400, "message": str(exc)},
                                status=status.HTTP_400_BAD_REQUEST)
            except RuntimeError as exc:
                return Response({"code": 500, "message": str(exc)},
                                status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # 补齐业务字段（签发日期/主体名称快照）
        update_fields = ["status", "cert_no", "updated_at"]
        obj.status = Certificate.Status.ISSUED
        obj.suspend_reason = ""
        if not obj.issue_date:
            from datetime import date as _date
            obj.issue_date = _date.today()
            update_fields.append("issue_date")
        if not obj.producer_name and producer is not None:
            obj.producer_name = producer.name
            update_fields.append("producer_name")
        obj.issuer = request.user
        update_fields.append("issuer")
        obj.save(update_fields=update_fields)

        services.anchor_certificate(obj)
        audit.log_operation(request, OperationLog.Action.ISSUE,
                            target_type="合格证", target_id=obj.id,
                            target_no=obj.cert_no,
                            detail=f"签发合格证 {obj.cert_no}"
                                   + ("（沿用原编号）" if already else "（新分配编号）"))
        return Response({"code": 200, "message": "签发成功并完成可信存证",
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
                            target_no=obj.cert_no or f"草稿#{obj.id}",
                            detail=f"作废合格证 {obj.cert_no or '草稿'}"
                                   + (f"，原因：{reason}" if reason else ""))
        return Response({"code": 200, "message": "已作废",
                         "data": CertificateSerializer(obj).data,
                         "evidence": EvidenceRecordSerializer(record).data})

    @action(detail=True, methods=["get"])
    def qrcode(self, request, pk=None):
        """生成追溯二维码（指向公众验真页）"""
        obj = self.get_object()
        if not obj.cert_no:
            return Response({"code": 400, "message": "合格证尚未分配编号，无法生成二维码"},
                            status=status.HTTP_400_BAD_REQUEST)
        verify_url = f"{settings.FRONTEND_VERIFY_BASE}/{obj.cert_no}"
        img = qrcode.make(verify_url)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return HttpResponse(buf.getvalue(), content_type="image/png")

    @action(detail=True, methods=["get"], url_path="inspection-summary")
    def inspection_summary(self, request, pk=None):
        """该合格证的监督检查汇总（前端详情抽屉用）。"""
        obj = self.get_object()
        rows = obj.inspections.order_by("-check_time", "-id")
        latest = rows.first()
        return Response({
            "code": 200, "message": "ok",
            "data": {
                "total": rows.count(),
                "unqualified_count": rows.filter(
                    check_result=InspectionRecord.CheckResult.UNQUALIFIED).count(),
                "overdue_count": rows.filter(
                    rectify_status=InspectionRecord.RectifyStatus.OVERDUE).count(),
                "latest": InspectionRecordSerializer(latest).data if latest else None,
                "cert_no_parsed": certno.parse_cert_no(obj.cert_no),
            },
        })


# ===============================================================
# 监管现场监督检查（执法闭环）
# ===============================================================
class InspectionRecordViewSet(RoleScopedQuerySetMixin, BaseViewSet):
    """
    监督检查记录。

    权限设计：
      - 读：所有登录用户（收购商/公众要能看到「这张证被查过」）
      - 写：仅监管人员/管理员（ReadOnlyOrRegulator）
    数据级隔离：
      - 种植主体只看自己批次关联的合格证上的检查记录
      - 收购商只看已签发合格证上的检查记录

    闭环动作：
      - create          现场检查录入 -> 结论存证上链
      - rectify         整改复查：提交整改说明 / 复查结论
      - 检查不合格时自动把关联合格证置为「暂停使用」（见 _apply_certificate_status）
    """
    queryset = InspectionRecord.objects.select_related(
        "certificate", "certificate__batch",
        "certificate__batch__producer", "inspector").all()
    serializer_class = InspectionRecordSerializer

    def scope_queryset(self, qs, user):
        if user.role == Role.PRODUCER:
            return qs.filter(certificate__batch__producer__owner=user)
        if user.role == Role.BUYER:
            return qs.filter(certificate__status=Certificate.Status.ISSUED)
        return qs.none()

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy",
                           "rectify", "anchor"):
            return [ReadOnlyOrRegulator()]
        return [IsAuthenticated()]

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get("check_result"):
            qs = qs.filter(check_result=p["check_result"])
        if p.get("rectify_status"):
            qs = qs.filter(rectify_status=p["rectify_status"])
        if p.get("check_type"):
            qs = qs.filter(check_type=p["check_type"])
        if p.get("certificate"):
            qs = qs.filter(certificate_id=p["certificate"])
        if p.get("overdue") in ("1", "true", "yes"):
            qs = qs.filter(rectify_status=InspectionRecord.RectifyStatus.OVERDUE)
        if p.get("keyword"):
            from django.db.models import Q
            kw = p["keyword"]
            qs = qs.filter(Q(problem_description__icontains=kw)
                           | Q(certificate__cert_no__icontains=kw)
                           | Q(inspector_name__icontains=kw)
                           | Q(check_location__icontains=kw))
        return qs

    def list(self, request, *args, **kwargs):
        """列表查询时顺带刷新逾期状态 —— 逾期随时间自然发生，不靠人工维护。"""
        qs = self.filter_queryset(self.get_queryset())
        for rec in qs:
            rec.refresh_overdue()
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        obj.refresh_overdue()
        return super().retrieve(request, *args, **kwargs)

    def perform_create(self, serializer):
        user = self.request.user
        record = serializer.save(
            inspector=user,
            inspector_name=serializer.validated_data.get("inspector_name")
            or user.first_name or user.username,
        )
        # 整改状态兜底：需要整改但没填状态时，默认「待整改」
        if (record.rectify_requirement or record.rectify_deadline) and \
                record.rectify_status == InspectionRecord.RectifyStatus.NOT_REQUIRED:
            record.rectify_status = InspectionRecord.RectifyStatus.PENDING
            record.save(update_fields=["rectify_status", "updated_at"])

        services.anchor_inspection(record)
        self._apply_certificate_status(record)

        audit.log_operation(self.request, OperationLog.Action.INSPECT,
                            target_type="监督检查", target_id=record.id,
                            target_no=record.certificate.cert_no or f"INS-{record.id}",
                            detail=f"现场检查结论「{record.get_check_result_display()}」"
                                   f"，评分 {record.check_score}"
                                   + (f"，罚款 {record.penalty_amount} 元"
                                      if record.penalty_amount else ""))

    def perform_update(self, serializer):
        record = serializer.save()
        audit.log_operation(self.request, OperationLog.Action.UPDATE,
                            target_type="监督检查", target_id=record.id,
                            target_no=record.certificate.cert_no or f"INS-{record.id}",
                            detail="修改监督检查记录")

    def perform_destroy(self, instance):
        audit.log_operation(self.request, OperationLog.Action.DELETE,
                            target_type="监督检查", target_id=instance.id,
                            target_no=instance.certificate.cert_no or f"INS-{instance.id}",
                            detail="删除监督检查记录")
        instance.delete()

    @action(detail=True, methods=["post"])
    def rectify(self, request, pk=None):
        """
        整改复查：被检方/复查人提交整改说明，监管确认复查结论。

        入参：
          rectify_note    整改说明（必填）
          result          done 已整改 / ongoing 整改中 / unqualified 复查仍不合格
          follow_up_person 复查负责人
          remark          备注
        """
        obj = self.get_object()
        note = (request.data.get("rectify_note") or "").strip()
        if not note:
            return Response({"code": 400, "message": "请填写整改说明"},
                            status=status.HTTP_400_BAD_REQUEST)

        result = request.data.get("result", InspectionRecord.RectifyStatus.DONE)
        if result not in dict(InspectionRecord.RectifyStatus.choices):
            return Response({"code": 400, "message": "整改状态非法"},
                            status=status.HTTP_400_BAD_REQUEST)
        if result == InspectionRecord.RectifyStatus.NOT_REQUIRED:
            return Response({"code": 400, "message": "复查不可置为「无需整改」"},
                            status=status.HTTP_400_BAD_REQUEST)

        obj.rectify_note = note
        obj.rectify_status = result
        if request.data.get("follow_up_person"):
            obj.follow_up_person = request.data["follow_up_person"][:50]
        if request.data.get("remark"):
            obj.remark = request.data["remark"][:255]

        update_fields = ["rectify_note", "rectify_status",
                         "follow_up_person", "remark", "updated_at"]
        if result == InspectionRecord.RectifyStatus.DONE:
            from datetime import datetime as _dt
            obj.rectify_time = _dt.now()
            update_fields.append("rectify_time")
        obj.save(update_fields=update_fields)

        # 复查合格 -> 恢复合格证为「已签发」；复查仍不合格 -> 维持暂停
        if result == InspectionRecord.RectifyStatus.DONE:
            self._restore_certificate(obj)
        elif result == InspectionRecord.RectifyStatus.RECHECK_FAILED:
            self._suspend_certificate(obj, reason=f"整改复查仍不合格（检查#{obj.id}）")

        audit.log_operation(request, OperationLog.Action.RECTIFY,
                            target_type="监督检查", target_id=obj.id,
                            target_no=obj.certificate.cert_no or f"INS-{obj.id}",
                            detail=f"整改复查：{obj.get_rectify_status_display()}")
        return Response({"code": 200, "message": "整改复查已记录",
                         "data": InspectionRecordSerializer(obj).data})

    @action(detail=True, methods=["post"])
    def anchor(self, request, pk=None):
        """手动重做检查结论存证。"""
        obj = self.get_object()
        record = services.anchor_inspection(obj)
        audit.log_operation(request, OperationLog.Action.ANCHOR,
                            target_type="监督检查", target_id=obj.id,
                            target_no=obj.certificate.cert_no or f"INS-{obj.id}",
                            detail=f"检查结论重新存证 tx={record.tx_hash[:18]}…")
        return Response({"code": 200, "message": "检查结论已存证",
                         "data": EvidenceRecordSerializer(record).data})

    # ---- 内部：检查结论与合格证状态联动 ----
    @staticmethod
    def _suspend_certificate(record, reason: str):
        """检查不合格 -> 合格证暂停使用（不是作废，复查合格可恢复）。"""
        cert = record.certificate
        if cert.status == Certificate.Status.REVOKED:
            return
        cert.status = Certificate.Status.SUSPENDED
        cert.suspend_reason = reason[:255]
        cert.save(update_fields=["status", "suspend_reason", "updated_at"])

    @staticmethod
    def _restore_certificate(record):
        """复查合格 -> 恢复为已签发（仅当当前是被暂停状态）。"""
        cert = record.certificate
        if cert.status != Certificate.Status.SUSPENDED:
            return
        # 还有其他未闭环的不合格检查时不恢复
        pending = cert.inspections.filter(
            check_result=InspectionRecord.CheckResult.UNQUALIFIED
        ).exclude(
            rectify_status__in=[InspectionRecord.RectifyStatus.DONE,
                                InspectionRecord.RectifyStatus.NOT_REQUIRED]
        ).exists()
        if pending:
            return
        cert.status = Certificate.Status.ISSUED
        cert.suspend_reason = ""
        cert.save(update_fields=["status", "suspend_reason", "updated_at"])

    def _apply_certificate_status(self, record):
        """根据检查结论联动合格证状态。"""
        if record.check_result == InspectionRecord.CheckResult.UNQUALIFIED:
            self._suspend_certificate(
                record, reason=f"现场监督检查不合格（检查#{record.id}）："
                               f"{(record.problem_description or '')[:80]}")
        elif record.check_result == InspectionRecord.CheckResult.QUALIFIED:
            self._restore_certificate(record)


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
            cert_ids = list(cert_ids)
            return qs.filter(
                Q(biz_type=EvidenceRecord.BizType.CERTIFICATE, biz_id__in=cert_ids)
                | Q(biz_type=EvidenceRecord.BizType.INSPECTION, biz_id__in=list(
                    InspectionRecord.objects.filter(
                        certificate_id__in=cert_ids).values_list("id", flat=True)))
                | Q(biz_type=EvidenceRecord.BizType.BATCH, biz_id__in=list(
                    ProductBatch.objects.filter(
                        producer__owner=user).values_list("id", flat=True)))
                | Q(biz_type=EvidenceRecord.BizType.REPORT, biz_id__in=list(
                    TestReport.objects.filter(
                        batch__producer__owner=user).values_list("id", flat=True)))
            )
        if user.role == Role.BUYER:
            cert_ids = list(Certificate.objects.filter(
                status=Certificate.Status.ISSUED).values_list("id", flat=True))
            return qs.filter(
                Q(biz_type=EvidenceRecord.BizType.CERTIFICATE, biz_id__in=cert_ids)
                | Q(biz_type=EvidenceRecord.BizType.INSPECTION, biz_id__in=list(
                    InspectionRecord.objects.filter(
                        certificate_id__in=cert_ids).values_list("id", flat=True)))
            )
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

        # ★ 监督检查公示：让消费者扫码就能看到「这张证是否被查出过问题」
        inspections = cert.inspections.order_by("-check_time", "-id")
        latest_ins = inspections.first()
        inspection_data = None
        if latest_ins:
            inspection_data = {
                "check_time": (latest_ins.check_time.strftime("%Y-%m-%d")
                               if latest_ins.check_time else ""),
                "check_type": latest_ins.get_check_type_display(),
                "check_result": latest_ins.get_check_result_display(),
                "check_score": str(latest_ins.check_score),
                "department": latest_ins.department,
                "problem": latest_ins.problem_description,
                "rectify_status": latest_ins.get_rectify_status_display(),
                "is_anchored": latest_ins.is_anchored,
                "record_hash": latest_ins.record_hash,
            }

        data = {
            "cert_no": cert.cert_no,
            "status": cert.status,
            "status_display": cert.get_status_display(),
            "suspend_reason": cert.suspend_reason,
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
            # ★ 编号解析（让公众看到「区域/日期/序号」的构成）
            "cert_no_parsed": certno.parse_cert_no(cert.cert_no),
            # ★ 监督检查公示
            "inspection_count": inspections.count(),
            "inspection": inspection_data,
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
            "draft_cert_count": Certificate.objects.filter(
                status=Certificate.Status.DRAFT).count(),
            "pending_cert_count": Certificate.objects.filter(
                status=Certificate.Status.PENDING).count(),
            "suspended_count": Certificate.objects.filter(
                status=Certificate.Status.SUSPENDED).count(),
            "evidence_count": EvidenceRecord.objects.count(),
            "supply_count": SupplyDemand.objects.filter(
                kind=SupplyDemand.Kind.SUPPLY).count(),
            "demand_count": SupplyDemand.objects.filter(
                kind=SupplyDemand.Kind.DEMAND).count(),
            "total_weight": str(ProductBatch.objects.aggregate(
                s=Sum("weight_kg"))["s"] or 0),
            "intent_count": MatchIntent.objects.count(),
            "log_count": OperationLog.objects.count(),
            # ★ 监管检查维度
            "inspection_count": InspectionRecord.objects.count(),
            "inspection_unqualified_count": InspectionRecord.objects.filter(
                check_result=InspectionRecord.CheckResult.UNQUALIFIED).count(),
            "rectify_pending_count": InspectionRecord.objects.filter(
                rectify_status__in=[InspectionRecord.RectifyStatus.PENDING,
                                    InspectionRecord.RectifyStatus.ONGOING]).count(),
            "rectify_overdue_count": InspectionRecord.objects.filter(
                rectify_status=InspectionRecord.RectifyStatus.OVERDUE).count(),
            "penalty_total": str(InspectionRecord.objects.aggregate(
                s=Sum("penalty_amount"))["s"] or 0),
        }
        if user.role in (Role.ADMIN, Role.REGULATOR):
            base["scope_label"] = "全量数据（监管视角）"
            base["pending_audit_count"] = Producer.objects.filter(
                audit_status=Producer.AuditStatus.PENDING).count()
            return base

        if user.role == Role.PRODUCER:
            my_batches = ProductBatch.objects.filter(producer__owner=user)
            my_certs = Certificate.objects.filter(batch__producer__owner=user)
            my_ins = InspectionRecord.objects.filter(
                certificate__batch__producer__owner=user)
            base.update({
                "scope_label": "我的生产数据（种植主体视角）",
                "producer_count": Producer.objects.filter(owner=user).count(),
                "approved_producer_count": Producer.objects.filter(
                    owner=user, audit_status=Producer.AuditStatus.APPROVED).count(),
                "batch_count": my_batches.count(),
                "certificate_count": my_certs.count(),
                "issued_count": my_certs.filter(
                    status=Certificate.Status.ISSUED).count(),
                "draft_cert_count": my_certs.filter(
                    status=Certificate.Status.DRAFT).count(),
                "pending_cert_count": my_certs.filter(
                    status=Certificate.Status.PENDING).count(),
                "suspended_count": my_certs.filter(
                    status=Certificate.Status.SUSPENDED).count(),
                "total_weight": str(my_batches.aggregate(
                    s=Sum("weight_kg"))["s"] or 0),
                "my_supply_count": SupplyDemand.objects.filter(
                    publisher=user, kind=SupplyDemand.Kind.SUPPLY).count(),
                "intent_count": MatchIntent.objects.filter(initiator=user).count(),
                # 自己的被检查情况（被检方视角）
                "inspection_count": my_ins.count(),
                "inspection_unqualified_count": my_ins.filter(
                    check_result=InspectionRecord.CheckResult.UNQUALIFIED).count(),
                "rectify_pending_count": my_ins.filter(
                    rectify_status__in=[InspectionRecord.RectifyStatus.PENDING,
                                        InspectionRecord.RectifyStatus.ONGOING]).count(),
                "rectify_overdue_count": my_ins.filter(
                    rectify_status=InspectionRecord.RectifyStatus.OVERDUE).count(),
                "penalty_total": str(my_ins.aggregate(
                    s=Sum("penalty_amount"))["s"] or 0),
            })
            return base

        if user.role == Role.BUYER:
            issued = Certificate.objects.filter(status=Certificate.Status.ISSUED)
            base.update({
                "scope_label": "可交易数据（收购商视角）",
                "producer_count": Producer.objects.filter(
                    audit_status=Producer.AuditStatus.APPROVED).count(),
                "certificate_count": issued.count(),
                "my_demand_count": SupplyDemand.objects.filter(
                    publisher=user, kind=SupplyDemand.Kind.DEMAND).count(),
                "intent_count": MatchIntent.objects.filter(initiator=user).count(),
                # 收购商视角：已签发证里被检查出问题的数量（进货风险提示）
                "inspection_count": InspectionRecord.objects.filter(
                    certificate__in=issued).count(),
                "inspection_unqualified_count": InspectionRecord.objects.filter(
                    certificate__in=issued,
                    check_result=InspectionRecord.CheckResult.UNQUALIFIED).count(),
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
        {"resource": "监督检查", "admin": "全部、可写", "regulator": "全部、可写",
         "producer": "仅自己批次关联的", "buyer": "仅已签发证上的"},
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
        {"action": "监督检查录入", "admin": True, "regulator": True,
         "producer": False, "buyer": False},
        {"action": "整改复查", "admin": True, "regulator": True,
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
                "cert_no_rule": {
                    "length": certno.TOTAL_LEN,
                    "segments": [
                        {"name": "区域码", "length": certno.REGION_LEN,
                         "example": "510106", "desc": "生产主体所属行政区划代码"},
                        {"name": "日期", "length": certno.DATE_LEN,
                         "example": "20260923", "desc": "签发当日 YYYYMMDD"},
                        {"name": "流水", "length": certno.SEQ_LEN,
                         "example": "00001", "desc": "当日第 N 张，5 位左补零"},
                    ],
                    "example": "510106" + "20260923" + "00001",
                    "note": "编号仅在「签发」环节分配；草稿/待审核不占号，"
                            "避免废号造成号码跳跃",
                },
                "cert_status_flow": [
                    {"key": "draft", "label": "草稿", "desc": "已建档，未占号"},
                    {"key": "pending", "label": "待审核", "desc": "已提交，仍不占号"},
                    {"key": "issued", "label": "已签发", "desc": "已分配编号 + 已上链"},
                    {"key": "suspended", "label": "暂停使用",
                     "desc": "监督检查不合格，暂缓流通，复查合格可恢复"},
                    {"key": "revoked", "label": "已作废", "desc": "永久失效 + 作废存证"},
                ],
            },
        })
