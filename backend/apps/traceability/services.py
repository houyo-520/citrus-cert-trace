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


def _salt() -> str:
    """取存证盐值（加盐哈希用）。缺失时回退空串，保证本地无配置也能跑。"""
    return getattr(settings, "EVIDENCE_SALT", "") or ""


def sha256_hex(text: str) -> str:
    """加盐 SHA-256 摘要。

    哈希 = sha256(salt + text)。盐由后端私有参数（EVIDENCE_SALT）提供，
    抵御彩虹表/字典逆向 —— 即使拿到规范化原文与摘要，也无法离线枚举敏感字段。
    注意：salt 只参与哈希，不写入 payload（payload 仍存明码原文供追溯展示）。
    """
    return hashlib.sha256((_salt() + text).encode("utf-8")).hexdigest()


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


def anchor_merkle_root(cert: Certificate) -> EvidenceRecord:
    """
    ★ 把一张合格证的「证据树」压缩成单个 Merkle 根上链（新增亮点，不侵入现有验真）。

    时机：在 anchor_certificate 之后调用，因此批/报告/证三者的存证记录都已落库，
    build_certificate_merkle_root 才能取到它们的 data_hash 参与汇聚。

    做了三件事：
      1. 计算 Merkle 根（批次->报告->证->检查，顺序固定）；
      2. 写回 Certificate.merkle_root（证面/详情可直接展示）；
      3. 以独立 bizType=merkle 上链（bizNo 用 cert_no，与 certificate 记录分属
         不同 bizType，不互相覆盖），链上能核验「这张证只能压缩成这一个根」。

    ★ 为什么不破坏现有验真：verify_certificate 只按 CERTIFICATE 类型做双层核验，
    这条 merkle 记录是额外的、平行的证据压缩层，任何一层缺失都不影响另一层结论。
    """
    from . import merkle

    root = merkle.build_certificate_merkle_root(cert)
    if not root:   # 突发情况（如证据哈希全空）不该上链一条空根
        return None

    cert.merkle_root = root
    cert.save(update_fields=["merkle_root", "updated_at"])

    payload = "|".join([cert.cert_no or f"证#{cert.id}", "MERKLE", root])
    result = _anchor(root, payload, EvidenceRecord.BizType.MERKLE,
                     cert.id, cert.cert_no)
    return _save_record(EvidenceRecord.BizType.MERKLE, cert.id,
                        cert.cert_no, root, payload, result)


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


def anchor_revoke(cert: Certificate, reason: str = "") -> EvidenceRecord:
    """
    合格证作废存证（作废双向上链）。

    作废必须留痕，且必须是「链上可核验的撤销」，而非仅本地加一条记录：
      1. 链上：调 revokeEvidence，对原 certificate 记录的 bizNo 打 revoked 标记
         —— 公众验真时链上能查到「该证已被撤销」，与「被篡改」「不存在」区分；
      2. 本地：存一条 REVOKE 类型 EvidenceRecord，作为可追溯的审计留痕
         （记录原因 + 时间，供看板/时间轴展示）。

    注意：biz_id 用负数偏移，避免与 CERTIFICATE 类型的同 id 记录冲突。
    """
    from datetime import datetime as _dt
    payload = "|".join([
        cert.cert_no, "REVOKED", reason.strip(), _fmt(_dt.now()),
    ])

    # 1. 链上打撤销标记（对原 certificate bizNo；失败降级不中断业务）
    revoke_result = _revoke_on_chain(cert.cert_no, reason)

    # 2. 链上写一条 REVOKE 类型的存证记录作为审计留痕（bizType=revoke 与 certificate 不冲突）
    digest = sha256_hex(payload)
    result = _anchor(digest, payload, EvidenceRecord.BizType.REVOKE,
                     cert.id, cert.cert_no)
    # 若 revokeEvidence 已成功上链，则用其 tx_hash 作为这条审计记录的链上锚点
    if revoke_result.get("ok") and revoke_result.get("tx_hash"):
        result = dict(result, tx_hash=revoke_result["tx_hash"],
                      block_height=revoke_result.get("block_height", 0),
                      mode=revoke_result.get("mode", "mock"))

    return _save_record(EvidenceRecord.BizType.REVOKE, cert.id,
                        cert.cert_no, digest, payload, result)


def _revoke_on_chain(biz_no: str, reason: str = "") -> dict:
    """
    调链上 revokeEvidence 对原存证记录打撤销标记。
    链不可用/未启用时降级（返回 mode=mock），绝不抛异常。
    """
    if not getattr(settings, "FISCO_BCOS", {}).get("ENABLE_CHAIN", True):
        return {"ok": False, "tx_hash": chain._mock_tx_hash(), "block_height": 0,
                "mode": "mock", "message": "链功能已关闭，作废未上链"}
    return chain.revoke_evidence(biz_type=EvidenceRecord.BizType.CERTIFICATE,
                                 biz_no=biz_no, reason=reason)


def build_inspection_payload(record) -> str:
    """
    构造监督检查记录的规范化原文。

    参与哈希的字段选择原则：只放**结论性、不可抵赖**的事实
    （检查结果、评分、罚款、法律依据、整改期限），
    不放「整改说明」这类后续会补充的描述性文字 —— 否则整改一填写哈希就变，
    存证就失去了「固化当时判定」的意义。
    """
    return "|".join([
        str(record.id),
        record.certificate.cert_no if record.certificate_id else "",
        record.get_check_type_display(),
        record.get_check_result_display(),
        _fmt(record.check_score),
        record.inspector_name or "",
        _fmt(record.check_time),
        _fmt(record.rectify_deadline),
        _fmt(record.penalty_amount),
        (record.legal_basis or "").strip(),
    ])


def inspection_biz_no(record) -> str:
    """
    监督检查的链上业务编号。

    ★ 必须带检查记录自身 id —— 合约是 `records[bizType][bizNo]` 单值映射，
    同一 bizNo 后写会覆盖先写。若直接用 cert_no，同一张合格证上的多次检查
    只有最后一次能在链上核验，先前记录会被永久覆盖（已实测踩坑）。
    """
    return f"INS-{record.id}"


def anchor_inspection(record) -> EvidenceRecord:
    """
    监督检查结论存证上链。

    ★ 这是本系统「监管 + 可信存证」的结合点：
    检查结论一旦上链，事后任何一方都无法单方面否认「当时判了不合格」。
    对监管方是履职留痕，对被检方是程序正义保障。
    """
    biz_no = inspection_biz_no(record)
    payload = build_inspection_payload(record)
    digest = sha256_hex(payload)
    result = _anchor(digest, payload, EvidenceRecord.BizType.INSPECTION,
                     record.id, biz_no)
    record.record_hash = digest
    record.is_anchored = True
    record.anchored_at = datetime.now()
    record.save(update_fields=["record_hash", "is_anchored", "anchored_at",
                               "updated_at"])
    return _save_record(EvidenceRecord.BizType.INSPECTION, record.id,
                        biz_no, digest, payload, result)


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
        "revoked": False,
    }

    # ---- 第二层：链上核验（调用合约 verifyEvidence / isRevoked）----
    if check_chain and record and record.tx_hash and record.tx_hash.startswith("0x"):
        chain_info = chain.verify_on_chain(
            EvidenceRecord.BizType.CERTIFICATE, cert.cert_no, stored)
        if chain_info.get("ok"):
            # ★ 链上已撤销（作废双向上链）：无论摘要是否一致，都判定为「已作废」
            if chain_info.get("revoked"):
                result["chain_verified"] = False
                result["revoked"] = True
                result["trusted"] = False
                result["chain_detail"] = chain_info["message"]
                result["message"] = "该合格证已在链上作废（撤销），不可再用于流通"
            else:
                result["chain_verified"] = chain_info["matched"]
                result["chain_detail"] = chain_info["message"]
                if chain_info["exists"] and not chain_info["matched"]:
                    result["trusted"] = False
                    result["message"] = "数据不可信：存证记录与链上数据不一致"
        else:
            result["chain_detail"] = f"链上查询失败：{chain_info.get('message', '未知原因')}"

    return result