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
from django.db.models import Avg, Count, Q, Sum
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Role

from . import audit, certno, chain, matching, notify, ocr, services, timeline
from .models import (Certificate, EvidenceRecord, InspectionRecord,
                     MatchIntent, Notification, OperationLog, Producer,
                     ProductBatch, PublicQueryRecord, SupplyDemand, TestReport)
from .permissions import (CanTrade, IsRegulator,
                          ReadOnlyOrProducerOrRegulator, ReadOnlyOrRegulator,
                          RoleScopedQuerySetMixin)
from .serializers import (CertificateSerializer, EvidenceRecordSerializer,
                          InspectionRecordSerializer, MatchIntentSerializer,
                          NotificationSerializer, OperationLogSerializer,
                          ProducerSerializer, ProductBatchSerializer,
                          SupplyDemandSerializer, TestReportSerializer)


class BaseViewSet(viewsets.ModelViewSet):
    """所有业务 ViewSet 的基类：默认要求登录。"""
    permission_classes = [IsAuthenticated]


def now_str(fmt: str = "%Y-%m-%d %H:%M") -> str:
    """
    当前时间字符串（安全版）。

    ★ 不要直接写 timezone.localtime() —— 当 settings.USE_TZ=False 时
    timezone.now() 返回的是 **naive** datetime，localtime() 会抛
    "localtime() cannot be applied to a naive datetime"。
    本项目开发用 SQLite + USE_TZ=False，生产同理，所以必须做分支。
    """
    now = timezone.now()
    if timezone.is_aware(now):
        now = timezone.localtime(now)
    return now.strftime(fmt)


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
        # ★ 站内消息：把审核结果推给主体 owner（解决"审核完没人知道"）
        notify.producer_audited(obj, request.user, new_status)
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
        """
        新增检测报告并自动存证。

        ★ OCR 溯源字段要显式接收。
          序列化器把 ocr_method/ocr_confidence/ocr_text 设为 read_only，是因为
          它们**只能由识别流程产生、不该由前端随意声明**；但「不该随便写」不等于
          「永远不写」—— 前端确认识别结果后保存时会把来源带上来，这里显式取出。
          若不做这一步，字段在库里恒为空，「这条数据是机器认的还是人敲的」就永远
          答不出来，溯源字段等于白加（实测该 bug 静默存在：不报错、字段就是空）。
        """
        extra = {}
        for k in ("ocr_method", "ocr_confidence", "ocr_text"):
            v = self.request.data.get(k)
            if v:
                extra[k] = str(v)[:2000 if k == "ocr_text" else 20]
        report = serializer.save(**extra)
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

    @action(detail=False, methods=["post"], url_path="ocr",
            parser_classes=[MultiPartParser, FormParser])
    def ocr_extract(self, request):
        """
        ★ 检测报告 OCR 识别：上传 PDF/图片 → 自动提取关键字段。

        ★ 重要：本接口**只识别、不落库**。
          识别是概率性的，检测结论（合格/不合格）一旦认错会直接影响
          农产品能否流通，必须由人眼确认后另行保存。
          因此这里返回识别结果与置信度，前端回填表单，用户确认后走正常 create。

        分层识别（见 ocr.py）：
          电子版 PDF → 直接读文本层（准、快、零 OCR 依赖）
          扫描件/图片 → 降级到 tesseract OCR
        """
        f = request.FILES.get("file")
        if f is None:
            return Response({"code": 400, "message": "请选择要识别的检测报告文件"},
                            status=status.HTTP_400_BAD_REQUEST)
        # 复用序列化器里的类型/大小校验规则，保持前后端一致
        from .serializers import REPORT_FILE_EXTS, REPORT_FILE_MAX_MB
        import os as _os
        ext = _os.path.splitext(f.name)[1].lower()
        if ext not in REPORT_FILE_EXTS:
            return Response(
                {"code": 400,
                 "message": f"仅支持 {'/'.join(sorted(e.lstrip('.') for e in REPORT_FILE_EXTS))} "
                            f"格式，当前为 {ext or '未知类型'}"},
                status=status.HTTP_400_BAD_REQUEST)
        if f.size > REPORT_FILE_MAX_MB * 1024 * 1024:
            return Response({"code": 400,
                             "message": f"文件不得超过 {REPORT_FILE_MAX_MB} MB，"
                                        f"当前 {f.size / 1024 / 1024:.1f} MB"},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            data = f.read()
        except Exception as exc:  # noqa: BLE001
            return Response({"code": 500, "message": f"读取文件失败：{exc}"},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        result = ocr.extract_from_file(f.name, data)
        audit.log_operation(request, OperationLog.Action.VERIFY,
                            target_type="检测报告", target_id=None,
                            target_no=f.name[:60],
                            detail=f"OCR 识别报告（{result['method']}，"
                                   f"置信度 {result['confidence']}，"
                                   f"识别出 {len(result['filled'])} 个字段）")
        return Response({
            "code": 200,
            "message": "识别完成" if result["ok"] else "未能识别，请手工录入",
            "data": result,
        })

    @action(detail=False, methods=["get"], url_path="ocr-status")
    def ocr_status(self, request):
        """OCR 引擎可用性自检（部署排查用）。"""
        return Response({"code": 200, "message": "ok", "data": ocr.engine_status()})


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
        # 签发/作废/驳回/批量签发是监管专属动作
        if self.action in ("issue", "revoke", "reject", "batch_issue"):
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

    @action(detail=False, methods=["post"], url_path="batch-issue")
    def batch_issue(self, request):
        """
        ★ 批量签发合格证。

        设计要点（为什么不是简单 for 循环调 issue）：
          1. **逐张独立成败**：一张证缺检测报告/缺主体，不能连累其余合规的证。
             整体事务回滚会让监管「一次操作全废」，体验极差。因此逐张
             try/except，各自独立提交，逐张返回结果。
          2. **编号仍然逐张原子分配**：复用 certno.save_with_cert_no 的
             「唯一索引 + IntegrityError 重试」机制，所以批量签发出来的
             编号依然连续递增、不会互相覆盖。
          3. **上限保护**：单次最多 50 张，避免有人一次提交上千条把
             链上存证队列打满（链上写入是同步阻塞的）。
        """
        ids = request.data.get("ids") or []
        if not isinstance(ids, list) or not ids:
            return Response({"code": 400, "message": "请提供要签发的合格证 ID 列表"},
                            status=status.HTTP_400_BAD_REQUEST)
        if len(ids) > 50:
            return Response({"code": 400, "message": "单次批量签发不超过 50 张"},
                            status=status.HTTP_400_BAD_REQUEST)

        # 保持与视图一致的数据级隔离：只允许操作本角色可见的证
        qs = self.filter_queryset(self.get_queryset()).filter(id__in=ids)
        found = {c.id: c for c in qs.select_related("batch", "batch__producer")}

        results, ok_count, fail_count = [], 0, 0
        from datetime import date as _date
        for cid in ids:
            obj = found.get(cid)
            label = f"合格证#{cid}"
            if obj is None:
                results.append({"id": cid, "label": label, "ok": False,
                                "cert_no": "", "message": "不存在或无权操作"})
                fail_count += 1
                continue

            label = obj.cert_no or f"草稿#{obj.id}"
            if obj.status == Certificate.Status.REVOKED:
                results.append({"id": cid, "label": label, "ok": False, "cert_no": "",
                                "message": "已作废，不可签发"})
                fail_count += 1
                continue
            # 与单张签发保持同一条业务规则：无检测报告不可签发
            if not obj.report_id:
                results.append({"id": cid, "label": label, "ok": False,
                                "cert_no": obj.cert_no or "",
                                "message": "缺少关联的检测报告，不可签发"})
                fail_count += 1
                continue

            producer = obj.batch.producer if obj.batch_id and obj.batch.producer_id else None
            try:
                if not obj.cert_no:
                    if producer is None:
                        raise ValueError("未关联生产主体，无法分配编号")
                    certno.save_with_cert_no(obj, producer)  # 逐张原子取号
                update_fields = ["status", "cert_no", "updated_at"]
                obj.status = Certificate.Status.ISSUED
                obj.suspend_reason = ""
                if not obj.issue_date:
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
                                    detail=f"批量签发合格证 {obj.cert_no}")
                # ★ 站内消息：批量签发时逐张通知（各主体只会收到自己那几张）
                notify.cert_issued(obj, request.user)
                results.append({"id": obj.id, "label": label, "ok": True,
                                "cert_no": obj.cert_no, "message": "签发并存证成功"})
                ok_count += 1
            except (ValueError, RuntimeError) as exc:
                results.append({"id": cid, "label": label, "ok": False,
                                "cert_no": obj.cert_no or "", "message": str(exc)})
                fail_count += 1
            except Exception as exc:  # noqa: BLE001 单张异常不应中断整批
                results.append({"id": cid, "label": label, "ok": False,
                                "cert_no": obj.cert_no or "",
                                "message": f"处理异常：{exc}"})
                fail_count += 1

        return Response({
            "code": 200,
            "message": f"批量签发完成：成功 {ok_count} 张，失败 {fail_count} 张",
            "data": {"success_count": ok_count, "fail_count": fail_count,
                     "total": len(ids), "results": results},
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
        # ★ 站内消息：提醒监管「有人送审了，来签发」
        notify.cert_submitted(obj, request.user)
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
        # ★ 站内消息：驳回意见必须让申请人看到，否则他不知道该改什么
        notify.cert_rejected(obj, request.user, reason)
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
        # ★ 签发前必须有关联的合格检测报告 —— 这是「承诺达标」的事实依据。
        # 正常流程里 submit 已拦过一次，这里再拦一次是防御性校验：
        # 若有人绕过 submit 直接改库造出 pending 状态，签发口仍能兜住。
        if not obj.report_id:
            return Response({"code": 400, "message": "缺少关联的检测报告，不可签发"},
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
        # ★ 站内消息：签发结果推给主体
        notify.cert_issued(obj, request.user)
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
        # ★ 站内消息：作废是重大变更，必须主动通知持证人
        notify.cert_revoked(obj, request.user, reason)
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

    @action(detail=True, methods=["get"], url_path="certificate-data")
    def certificate_data(self, request, pk=None):
        """
        ★ 单张合格证的「证面打印数据」。

        为什么单独开一个接口而不复用列表序列化结果：
          合格证是要**打印出来贴在箱子上**的实物凭证，证面字段与列表页字段
          不是同一套口径 —— 证面需要主体详细地址、批次产地、报告检测机构、
          存证交易哈希、验真链接等列表页不展示的内容。
          统一在这里聚合，前端拿到即可直接渲染打印模板，无需再串多个接口。

        同时把 status 校验放在这里：草稿/待审核的证**没有编号也无法验真**，
        证面不完整，因此不允许打印（前端按钮也会同步禁用）。
        """
        obj = self.get_object()
        if not obj.cert_no:
            return Response({"code": 400, "message": "合格证尚未签发（无编号），不可打印"},
                            status=status.HTTP_400_BAD_REQUEST)

        batch = obj.batch
        producer = batch.producer if batch and batch.producer_id else None
        report = obj.report

        # 存证记录（取该合格证最新一条证书类存证）
        record = EvidenceRecord.objects.filter(
            biz_type=EvidenceRecord.BizType.CERTIFICATE, biz_id=obj.id
        ).order_by("-id").first()

        # ★ FRONTEND_VERIFY_BASE 本身已含路由（默认 "http://host/#/verify"），
        # 这里只能追加证号。曾经写成 BASE + "/#/verify/" + cert_no，
        # 结果生成 "…/#/verify/#/verify/CERT-xxx" 这种双路由链接 ——
        # 二维码照样能生成、图也有尺寸，只有扫出来才发现打不开，极难肉眼发现。
        verify_url = f"{settings.FRONTEND_VERIFY_BASE.rstrip('/')}/{obj.cert_no}"

        data = {
            "cert_no": obj.cert_no,
            "status": obj.status,
            "status_display": obj.get_status_display(),
            "suspend_reason": obj.suspend_reason,
            # 主体
            "producer_name": obj.producer_name or (producer.name if producer else ""),
            "producer_address": producer.address if producer else "",
            "producer_contact": producer.contact if producer else "",
            "producer_phone": producer.phone if producer else "",
            "producer_credit_code": producer.credit_code if producer else "",
            # 产品
            "product_name": obj.product_name,
            "variety": batch.variety if batch else "",
            "origin": batch.origin if batch else "",
            "batch_no": batch.batch_no if batch else "",
            "harvest_date": batch.harvest_date if batch else None,
            "farming_record": batch.farming_record if batch else "",
            "quantity_kg": str(obj.quantity_kg),
            # 检测
            "report_no": report.report_no if report else "",
            "test_org": report.test_org if report else "",
            "test_date": report.test_date if report else None,
            "conclusion": report.get_conclusion_display() if report else "",
            "pesticide_residue": report.pesticide_residue if report else "",
            "heavy_metal": report.heavy_metal if report else "",
            # 期间
            "issue_date": obj.issue_date,
            "valid_until": obj.valid_until,
            "issuer_name": obj.issuer.get_full_name() or obj.issuer.username
            if obj.issuer_id else "",
            # 可信存证
            "cert_hash": obj.cert_hash,
            "is_anchored": obj.is_anchored,
            "anchored_at": obj.anchored_at,
            "tx_hash": record.tx_hash if record else "",
            "block_height": record.block_height if record else 0,
            "chain_status": record.chain_status if record else "",
            # 追溯
            "verify_url": verify_url,
            "cert_no_parsed": certno.parse_cert_no(obj.cert_no),
            # 公告依据（证面下方注明样式来源，答辩时也是政策依据）
            "notice": "本证样式依据农业农村部《农产品质量安全承诺达标合格证"
                      "内容及样式》公告（第989号）设计",
            "printed_at": now_str(),
        }
        audit.log_operation(request, OperationLog.Action.VERIFY,
                            target_type="合格证", target_id=obj.id,
                            target_no=obj.cert_no, detail="导出/打印合格证证面")
        return Response({"code": 200, "message": "ok", "data": data})

    @action(detail=True, methods=["get"], url_path="timeline")
    def timeline_view(self, request, pk=None):
        """
        ★ 追溯链时间轴：把散落在 5 张表里的时间点串成「一件柑橘的旅程」。

        与 certificate-data 的分工：
          certificate-data 是给「打印」用的静态证面字段；
          timeline 是给「展示」用的过程叙事，节点按事实动态生成、带状态色与锚点。
        """
        obj = self.get_object()
        data = timeline.build_timeline(obj)
        data["cert_no"] = obj.cert_no
        return Response({"code": 200, "message": "ok", "data": data})

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
        # ★ 站内消息：检查结论必须让被检主体知道（尤其不合格时要限期整改）
        notify.inspection_created(record, user)
        # 若因此暂停了合格证，额外发一条「证被暂停」的提醒 —— 两件事对主体
        # 是不同后果（要整改 vs 货不能卖了），合并成一条会弱化后者
        if record.check_result == InspectionRecord.CheckResult.UNQUALIFIED:
            cert = record.certificate
            cert.refresh_from_db(fields=["status", "suspend_reason"])
            if cert.status == Certificate.Status.SUSPENDED:
                notify.cert_suspended(cert, user, cert.suspend_reason)

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
        # ★ 站内消息：整改提交后提醒监管来复核闭环
        notify.rectify_done(obj, request.user)
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
        # ★ 站内消息：提醒供需单发布方「有人找你对接」
        notify.intent_created(intent, request.user)
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
        # ★ 站内消息：回应结果推给意向发起人
        notify.intent_responded(obj, request.user)
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
# ★ 站内消息（流程闭环的最后一环）
# ===============================================================
class NotificationViewSet(viewsets.ModelViewSet):
    """
    站内消息。

    ★ 数据级隔离在这里是**硬隔离**：查询集永远锁死在 recipient=当前用户。
      消息天生是「私有的」，不像合格证那样存在「监管能看全量」的例外，
      所以不走 RoleScopedQuerySetMixin，直接无条件过滤，避免任何角色
      通过改 URL 里的 id 读到别人的消息。
    """
    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer

    def get_queryset(self):
        qs = (Notification.objects
              .filter(recipient=self.request.user)
              .select_related("actor"))
        p = self.request.query_params
        if p.get("category"):
            qs = qs.filter(category=p["category"])
        if p.get("unread") in ("1", "true", "yes"):
            qs = qs.filter(read_at__isnull=True)
        if p.get("level"):
            qs = qs.filter(level=p["level"])
        if p.get("keyword"):
            kw = p["keyword"]
            qs = qs.filter(Q(title__icontains=kw) | Q(content__icontains=kw))
        return qs

    @action(detail=False, methods=["get"])
    def unread(self, request):
        """未读汇总（顶栏铃铛用，轻量接口，前端轮询打这个）。"""
        return Response({"code": 200, "message": "ok",
                         "data": notify.unread_summary(request.user)})

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        """标记单条已读。"""
        obj = self.get_object()
        if obj.read_at is None:
            obj.read_at = timezone.now()
            obj.save(update_fields=["read_at", "updated_at"])
        return Response({"code": 200, "message": "已读",
                         "data": NotificationSerializer(obj).data})

    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        """一键全部已读（只影响自己的消息）。"""
        n = (Notification.objects
             .filter(recipient=request.user, read_at__isnull=True)
             .update(read_at=timezone.now()))
        return Response({"code": 200, "message": f"已将 {n} 条消息标记为已读",
                         "data": {"updated": n}})

    @action(detail=False, methods=["post"], url_path="clear-read")
    def clear_read(self, request):
        """清理已读消息（保持收件箱干净）。"""
        n, _ = (Notification.objects
                .filter(recipient=request.user, read_at__isnull=False).delete())
        return Response({"code": 200, "message": f"已清理 {n} 条已读消息",
                         "data": {"deleted": n}})

    @action(detail=False, methods=["get"], url_path="categories")
    def categories(self, request):
        """消息类型字典（前端筛选下拉用）。"""
        return Response({
            "code": 200, "message": "ok",
            "data": {
                "categories": [{"key": k, "label": v}
                               for k, v in Notification.Category.choices],
                "levels": [{"key": k, "label": v}
                           for k, v in Notification.Level.choices],
            },
        })


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
        # ★ 追溯链时间轴：把「育苗→采收→检测→签发→流通」串成一条可看的时间线。
        #   公众验真页从「表格」升级为「时间轴」的核心数据源。
        tl = timeline.build_timeline(cert)
        data["timeline"] = tl["nodes"]
        data["timeline_summary"] = tl["summary"]
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
            "revoked_count": Certificate.objects.filter(
                status=Certificate.Status.REVOKED).count(),
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
            "rectify_done_count": InspectionRecord.objects.filter(
                rectify_status=InspectionRecord.RectifyStatus.DONE).count(),
            "penalty_total": str(InspectionRecord.objects.aggregate(
                s=Sum("penalty_amount"))["s"] or 0),
            # ★ 检测报告 / 存证质量维度（看板增强）
            "report_count": TestReport.objects.count(),
            "report_unqualified_count": TestReport.objects.filter(
                conclusion=TestReport.Conclusion.UNQUALIFIED).count(),
            # 批次存证数：ProductBatch 模型上没有 is_anchored 字段
            # （批次存证落在 EvidenceRecord 里），必须从存证表反查，
            # 不能写成 ProductBatch.objects.filter(is_anchored=True) —— 那会 FieldError。
            "batch_anchored_count": EvidenceRecord.objects.filter(
                biz_type=EvidenceRecord.BizType.BATCH
            ).values("biz_id").distinct().count(),
            "public_query_count": PublicQueryRecord.objects.count(),
            "matched_supply_count": SupplyDemand.objects.filter(
                deal_status=SupplyDemand.DealStatus.MATCHED).count(),
            # ★ 平均匹配度：产销对接质量的量化指标（智能匹配算法的效果度量）
            "avg_match_score": self._avg_match_score(),
        }
        # ★ 近 6 个月签发趋势（折线图数据源）
        base["issue_trend"] = self._issue_trend()
        # ★ 检查类型分布（监管看板饼图）
        base["inspection_type_dist"] = self._inspection_type_dist()
        # ★ 各主体签发量 TOP5（对比柱图）
        base["top_producers"] = self._top_producers()

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

    # ---- 看板增强：三个统计辅助 ----

    @staticmethod
    def _issue_trend(months: int = 6):
        """
        近 N 个月合格证签发趋势。

        用 TruncMonth 在数据库侧按自然月分组 —— 不要在 Python 里
        for 循环 6 次各查一遍（那就是 6 条 SQL），一次聚合出全部月份。

        注意补零：没有任何签发的月份数据库不会返回行，
        折线图会出现「断点」而不是「0」，所以必须自己把月份轴补全。
        """
        from datetime import date as _date
        from django.db.models.functions import TruncMonth

        today = _date.today()
        # 构造连续 N 个月的 (year, month) 轴，从 N-1 个月前到本月
        axis = []
        y, m = today.year, today.month
        for _ in range(months):
            axis.append((y, m))
            m -= 1
            if m == 0:
                m = 12
                y -= 1
        axis.reverse()

        start = axis[0]
        start_date = _date(start[0], start[1], 1)
        rows = (Certificate.objects
                .filter(status=Certificate.Status.ISSUED,
                        issue_date__gte=start_date)
                .annotate(mth=TruncMonth("issue_date"))
                .values("mth")
                .annotate(c=Count("id")))
        bucket = {(r["mth"].year, r["mth"].month): r["c"]
                  for r in rows if r["mth"]}
        return [{"month": f"{yy}-{mm:02d}", "count": bucket.get((yy, mm), 0)}
                for yy, mm in axis]

    @staticmethod
    def _inspection_type_dist():
        """检查类型分布（监管看板饼图）。"""
        rows = (InspectionRecord.objects.values("check_type")
                .annotate(c=Count("id")))
        label = dict(InspectionRecord.CheckType.choices)
        return [{"name": label.get(r["check_type"], r["check_type"]),
                 "value": r["c"]} for r in rows]

    @staticmethod
    def _top_producers(limit: int = 5):
        """签发量 TOP N 生产主体（横向对比柱图）。"""
        rows = (Producer.objects
                .annotate(c=Count("batches__certificates",
                                  filter=Q(batches__certificates__status=
                                           Certificate.Status.ISSUED)))
                .filter(c__gt=0)
                .order_by("-c")
                .values("name", "c")[:limit])
        return [{"name": r["name"], "count": r["c"]} for r in rows]

    @staticmethod
    def _avg_match_score():
        """
        平均产销匹配度（智能匹配算法的效果度量）。

        只统计有评分的意向单（match_score > 0）——
        没走智能匹配直接发起对接的那些是 0，混进去会把均值拉低失真。
        """
        from django.db.models import Avg
        v = MatchIntent.objects.filter(match_score__gt=0).aggregate(
            a=Avg("match_score"))["a"]
        return round(float(v), 1) if v is not None else 0


# ===============================================================
# ★ 风险预警汇总（首页红点 + 预警清单）
# ===============================================================
class AlertView(APIView):
    """
    风险预警聚合。

    为什么需要：系统里原本有 6 类「需要人去处理」的信号，但分散在 6 个页面，
    监管不逐个点开就发现不了 —— 逾期整改、抽检不合格、暂停证未复查、
    草稿堆积、待审核主体、检查不合格持有的有效证。
    这里把它们统一聚合成一份「待办清单」，首页给红点数字，点开看明细。

    设计原则：
      - **按角色裁剪**：只返回该角色有权处理/有权知晓的预警。
        种植主体只看到「自己被检查出的问题」，不该看到别人的。
      - **严重度分级**：danger 需立即处置（逾期/不合格），warning 需关注，
        info 仅提示。前端据此着色排序，避免满屏红字导致告警疲劳。
      - **每条预警带 action 路由**：点一下直接跳到对应页面，
        而不是只告诉用户「有问题」却不告诉他去哪儿解决。
    """

    permission_classes = [IsAuthenticated]

    # 预警条目统一结构：
    #   {key, level, title, desc, count, action_route, action_label, items[]}
    def get(self, request):
        user = request.user
        alerts = []
        if user.role in (Role.ADMIN, Role.REGULATOR):
            alerts = self._regulator_alerts()
        elif user.role == Role.PRODUCER:
            alerts = self._producer_alerts(user)
        elif user.role == Role.BUYER:
            alerts = self._buyer_alerts()

        total = sum(a["count"] for a in alerts)
        danger = sum(a["count"] for a in alerts if a["level"] == "danger")
        return Response({
            "code": 200, "message": "ok",
            "data": {
                "total": total,
                "danger": danger,
                "alerts": alerts,
                "role": user.role,
                "generated_at": now_str(),
            },
        })

    # ---- 各角色的预警构造 ----

    def _regulator_alerts(self):
        from datetime import date as _date
        today = _date.today()
        alerts = []

        # ① 逾期未整改（最严重：执法时限已过）
        overdue = InspectionRecord.objects.filter(
            rectify_status=InspectionRecord.RectifyStatus.OVERDUE)
        alerts.append(self._pack(
            "rectify_overdue", "danger", "整改逾期未完成",
            "已超过整改期限仍未闭环，需启动后续处置",
            overdue, "inspection", "去处理",
            self._inspection_items(overdue)))

        # ② 抽查不合格但尚在期限内（预警，别等到逾期）
        pending_bad = InspectionRecord.objects.filter(
            check_result=InspectionRecord.CheckResult.UNQUALIFIED,
            rectify_deadline__gte=today,
        ).exclude(rectify_status__in=[
            InspectionRecord.RectifyStatus.DONE,
            InspectionRecord.RectifyStatus.NOT_REQUIRED])
        alerts.append(self._pack(
            "rectify_pending", "warning", "不合格整改中",
            "检查判定不合格，仍在整改期限内，需跟踪复查",
            pending_bad, "inspection", "去跟踪",
            self._inspection_items(pending_bad)))

        # ③ 合格证被暂停使用（流通受阻，要么复查恢复要么作废）
        suspended = Certificate.objects.filter(status=Certificate.Status.SUSPENDED)
        alerts.append(self._pack(
            "cert_suspended", "warning", "合格证暂停使用",
            "因检查不合格被暂停流通，复查合格可恢复",
            suspended, "certificate", "去查看",
            [{"label": c.cert_no or f"#{c.id}", "extra": c.producer_name or "",
              "desc": c.suspend_reason} for c in suspended[:20]]))

        # ④ 待审核主体（卡在入门环节，主体无法开展业务）
        pending_producer = Producer.objects.filter(
            audit_status=Producer.AuditStatus.PENDING)
        alerts.append(self._pack(
            "producer_pending", "warning", "主体待审核",
            "尚未审核的生产主体无法正常开展业务",
            pending_producer, "producer", "去审核",
            [{"label": p.name, "extra": p.region or "", "desc": p.address}
             for p in pending_producer[:20]]))

        # ⑤ 待签发合格证（已提交但未签发，主体在等）
        pending_cert = Certificate.objects.filter(status=Certificate.Status.PENDING)
        alerts.append(self._pack(
            "cert_pending", "info", "合格证待签发",
            "已提交审核，等待监管签发分配编号",
            pending_cert, "certificate", "去签发",
            [{"label": c.producer_name or f"#{c.id}", "extra": c.product_name,
              "desc": f"{c.quantity_kg} kg"} for c in pending_cert[:20]]))

        # ⑥ 草稿堆积（建档后长期不提交，容易变成垃圾数据）
        draft_cert = Certificate.objects.filter(status=Certificate.Status.DRAFT)
        alerts.append(self._pack(
            "cert_draft", "info", "合格证草稿未提交",
            "已建档但未提交审核（草稿不占号，可安全删除）",
            draft_cert, "certificate", "去整理",
            [{"label": c.producer_name or f"#{c.id}", "extra": c.product_name,
              "desc": "草稿"} for c in draft_cert[:20]]))

        return [a for a in alerts if a["count"] > 0]

    def _producer_alerts(self, user):
        """种植主体：只看自己被查出的问题 + 自己待办的证。"""
        from datetime import date as _date
        today = _date.today()
        alerts = []

        my_ins = InspectionRecord.objects.filter(
            certificate__batch__producer__owner=user)
        overdue = my_ins.filter(
            rectify_status=InspectionRecord.RectifyStatus.OVERDUE)
        alerts.append(self._pack(
            "rectify_overdue", "danger", "整改已逾期",
            "监督检查发现的整改事项已超过期限，请尽快整改并申请复查",
            overdue, "inspection", "去查看",
            self._inspection_items(overdue)))

        need_fix = my_ins.filter(
            check_result=InspectionRecord.CheckResult.UNQUALIFIED,
            rectify_deadline__gte=today,
        ).exclude(rectify_status__in=[
            InspectionRecord.RectifyStatus.DONE,
            InspectionRecord.RectifyStatus.NOT_REQUIRED])
        alerts.append(self._pack(
            "rectify_pending", "warning", "待整改事项",
            "监督检查判定不合格，请在期限内完成整改",
            need_fix, "inspection", "去查看",
            self._inspection_items(need_fix)))

        my_certs = Certificate.objects.filter(batch__producer__owner=user)
        suspended = my_certs.filter(status=Certificate.Status.SUSPENDED)
        alerts.append(self._pack(
            "cert_suspended", "warning", "我的合格证被暂停",
            "该证当前不可用于流通交易，完成整改并通过复查后可恢复",
            suspended, "certificate", "去查看",
            [{"label": c.cert_no or f"#{c.id}", "extra": c.product_name,
              "desc": c.suspend_reason} for c in suspended[:20]]))

        draft_cert = my_certs.filter(status=Certificate.Status.DRAFT)
        alerts.append(self._pack(
            "cert_draft", "info", "合格证草稿未提交",
            "完善检测报告关联后即可提交审核",
            draft_cert, "certificate", "去提交",
            [{"label": c.producer_name or f"#{c.id}", "extra": c.product_name,
              "desc": "草稿"} for c in draft_cert[:20]]))

        # 主体自身未通过审核（影响后续所有业务）
        my_producer = Producer.objects.filter(
            owner=user).exclude(audit_status=Producer.AuditStatus.APPROVED)
        alerts.append(self._pack(
            "producer_unapproved", "info", "主体尚未通过审核",
            "审核通过后才能正常开展批次建档与合格证申请",
            my_producer, "producer", "去查看",
            [{"label": p.name, "extra": p.get_audit_status_display(),
              "desc": p.remark} for p in my_producer[:20]]))

        return [a for a in alerts if a["count"] > 0]

    def _buyer_alerts(self):
        """收购商：进货风险提示 + 自己的对接待办。"""
        from django.db.models import Q
        issued = Certificate.objects.filter(status=Certificate.Status.ISSUED)
        alerts = []

        # ① 已签发证上出现过不合格检查（进货前必须知道）
        risky = InspectionRecord.objects.filter(
            certificate__in=issued,
            check_result=InspectionRecord.CheckResult.UNQUALIFIED)
        alerts.append(self._pack(
            "incoming_risk", "danger", "货源存在检查不合格记录",
            "这些已签发合格证曾被判定不合格，采购前建议核实整改情况",
            risky, "inspection", "去核实",
            self._inspection_items(risky)))

        # ② 收到但未回应的对接意向
        waiting = MatchIntent.objects.filter(
            supply__publisher=self.request.user,
            status=MatchIntent.Status.PENDING)
        alerts.append(self._pack(
            "intent_waiting", "warning", "待回应的对接意向",
            "采购方已发起对接，请及时接受或拒绝",
            waiting, "intent", "去回应",
            [{"label": i.supply.title if i.supply_id else f"#{i.id}",
              "extra": f"{i.quantity_kg} kg",
              "desc": f"匹配度 {i.match_score}"} for i in waiting[:20]]))

        return [a for a in alerts if a["count"] > 0]

    # ---- 工具方法 ----

    @staticmethod
    def _pack(key, level, title, desc, qs, route, action_label, items):
        return {
            "key": key, "level": level, "title": title, "desc": desc,
            "count": qs.count(),
            "action_route": route, "action_label": action_label,
            "items": items or [],
        }

    @staticmethod
    def _inspection_items(qs):
        return [{
            "label": f"检查#{r.id}",
            "extra": (r.certificate.cert_no if r.certificate_id else ""),
            "desc": (r.problem_description or r.get_check_result_display())[:60],
        } for r in qs.select_related("certificate")[:20]]


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
