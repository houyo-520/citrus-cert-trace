"""
可信存证核心服务

设计要点：
1. 哈希在后端生成（前端不可信）—— 前端只能提交业务数据，哈希由服务端按统一规则计算。
2. 规范化拼接：把关键字段按固定顺序、固定分隔符拼接，保证同一数据每次计算结果一致。
3. 存证留痕：写入 EvidenceRecord，并尝试上链到 FISCO BCOS（chain.py）。
   链不可用时自动降级为模拟存证（tx_hash 为本地随机值、block_height=0），
   业务不中断，便于无链环境下开发演示。
4. 验真比对：重新读取当前字段重算哈希，与存证记录比对，识别是否被篡改。
"""
import hashlib
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings

from . import chain
from .models import Certificate, EvidenceRecord


def _fmt(value):
    """
    把字段值统一格式化为字符串，保证「同一语义的数据」在任何时刻拼出的原文完全一致：
      None -> 空串
      日期时间 -> 固定格式字符串
      数值(int/float/Decimal) -> 统一两位小数（避免 5000 与 5000.00 拼接结果不同）
      其余 -> 去空格字符串
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if hasattr(value, "strftime"):  # date
        return value.strftime("%Y-%m-%d")
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float, Decimal)):
        try:
            return str(Decimal(str(value)).quantize(Decimal("0.01")))
        except (InvalidOperation, ValueError):
            return str(value)
    return str(value).strip()


def build_certificate_payload(cert: Certificate) -> str:
    """
    构造合格证的规范化原文（参与哈希计算的字段集合）。
    字段顺序固定，用 '|' 分隔，避免顺序歧义。
    """
    parts = [
        cert.cert_no,
        cert.producer_name,
        cert.product_name,
        _fmt(cert.quantity_kg),
        cert.batch.batch_no if cert.batch_id else "",
        cert.report.report_no if cert.report_id else "",
        _fmt(cert.issue_date),
        _fmt(cert.valid_until),
    ]
    return "|".join(parts)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_certificate_hash(cert: Certificate) -> str:
    return sha256_hex(build_certificate_payload(cert))


def _anchor(digest: str, payload: str, biz_type: str, biz_id: int, biz_no: str) -> dict:
    """
    统一上链入口：把摘要写入联盟链，返回 chain.send_evidence 的结果。
    关闭链功能（FISCO_BCOS.ENABLE_CHAIN=False）时直接走模拟。
    """
    if not getattr(settings, "FISCO_BCOS", {}).get("ENABLE_CHAIN", True):
        return {"ok": False, "tx_hash": chain._mock_tx_hash(), "block_height": 0,
                "mode": "mock", "message": "链功能已关闭，使用模拟存证"}
    return chain.send_evidence(data_hash=digest, biz_no=biz_no, biz_type=biz_type)


def _save_record(biz_type: str, biz_id: int, biz_no: str, digest: str,
                 payload: str, result: dict) -> EvidenceRecord:
    """把上链结果写入存证记录（含链上交易哈希与区块高度）。"""
    record, _ = EvidenceRecord.objects.update_or_create(
        biz_type=biz_type,
        biz_id=biz_id,
        defaults={
            "biz_no": biz_no,
            "data_hash": digest,
            "payload": payload,
            "tx_hash": result.get("tx_hash", ""),
            "block_height": result.get("block_height", 0),
            "chain_status": "success" if result.get("ok") else result.get("mode", "mock"),
        },
    )
    return record


def anchor_certificate(cert: Certificate) -> EvidenceRecord:
    """
    对合格证执行存证：
      1. 计算哈希并写回合格证
      2. 上链（真实链优先，失败降级模拟）
      3. 写入存证记录
    """
    payload = build_certificate_payload(cert)
    digest = sha256_hex(payload)
    cert.cert_hash = digest
    cert.is_anchored = True
    cert.anchored_at = datetime.now()
    cert.save(update_fields=["cert_hash", "is_anchored", "anchored_at", "updated_at"])

    result = _anchor(digest, payload, EvidenceRecord.BizType.CERTIFICATE,
                     cert.id, cert.cert_no)
    return _save_record(EvidenceRecord.BizType.CERTIFICATE, cert.id,
                        cert.cert_no, digest, payload, result)


def anchor_batch(batch) -> EvidenceRecord:
    payload = "|".join([
        batch.batch_no, batch.producer.name if batch.producer_id else "",
        batch.product_name, _fmt(batch.harvest_date), _fmt(batch.weight_kg),
    ])
    digest = sha256_hex(payload)
    result = _anchor(digest, payload, EvidenceRecord.BizType.BATCH,
                     batch.id, batch.batch_no)
    return _save_record(EvidenceRecord.BizType.BATCH, batch.id,
                        batch.batch_no, digest, payload, result)


def anchor_report(report) -> EvidenceRecord:
    payload = "|".join([
        report.report_no, report.batch.batch_no if report.batch_id else "",
        report.conclusion, _fmt(report.test_date),
    ])
    digest = sha256_hex(payload)
    report.report_hash = digest
    report.save(update_fields=["report_hash", "updated_at"])
    result = _anchor(digest, payload, EvidenceRecord.BizType.REPORT,
                     report.id, report.report_no)
    return _save_record(EvidenceRecord.BizType.REPORT, report.id,
                        report.report_no, digest, payload, result)


def verify_certificate(cert: Certificate, check_chain: bool = False) -> dict:
    """
    验真：重算当前合格证哈希，与存证记录中的摘要比对；可选做链上二次核验。

    双层校验逻辑（答辩亮点）：
      第一层「本地比对」：当前数据重算哈希 == 存证表里的摘要
      第二层「链上核验」：存证表里的摘要 == 链上交易 data 中记录的摘要
    两层都过，才能证明「数据没被改，且存证摘要没被库内篡改」。

    返回 {trusted, current_hash, stored_hash, message, chain_verified, chain_detail}
    """
    current = compute_certificate_hash(cert)
    record = EvidenceRecord.objects.filter(
        biz_type=EvidenceRecord.BizType.CERTIFICATE, biz_id=cert.id
    ).first()
    stored = record.data_hash if record else ""
    local_trusted = bool(stored) and (current == stored)

    if not record:
        message = "未找到存证记录，无法验真"
    elif local_trusted:
        message = "数据可信：当前数据与存证摘要一致"
    else:
        message = "数据不可信：当前数据与存证摘要不一致，可能已被篡改"

    result = {
        "trusted": local_trusted,
        "current_hash": current,
        "stored_hash": stored,
        "message": message,
        "chain_verified": False,
        "chain_detail": "",
    }

    # ---- 第二层：链上核验（调用合约 verifyEvidence）----
    if check_chain and record and record.tx_hash and record.tx_hash.startswith("0x"):
        chain_info = chain.verify_on_chain(
            EvidenceRecord.BizType.CERTIFICATE, cert.cert_no, stored)
        if chain_info.get("ok"):
            result["chain_verified"] = chain_info["matched"]
            result["chain_detail"] = chain_info["message"]
            if chain_info["exists"] and not chain_info["matched"]:
                result["trusted"] = False
                result["message"] = "数据不可信：存证记录与链上数据不一致"
        else:
            result["chain_detail"] = f"链上查询失败：{chain_info.get('message', '未知原因')}"

    return result