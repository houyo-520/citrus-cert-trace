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
    """
    把上链结果写入存证记录（含链上交易哈希与区块高度）。

    ★ 追加式版本（B3 修复：替代早期的「冻结原摘要」语义）：

    背景 —— 合约是**单值槽**（`records[bizType][bizNo]` 重复上链会覆盖），
    所以链上永远只有「最新一次」的摘要。早期「摘要分歧就冻结本地 hash」的做法
    会与链上分叉：本地存旧、链上存新 → 公众验真页每张重存证过的证都变红，
    且 rehash_all（加盐迁移的唯一用途）整条路被堵死。

    现在改为**追加式版本**：
      - 首次存证：version=1, is_current=True；
      - 摘要相同：在原行上补齐链上信息（如 mock → 真上链）；
      - 摘要不同（重存证 / 加盐迁移）：把原行 is_current=False 归档，
        **追加**一条 version+1, is_current=True 的新行。
    这样：① 历史版本全部留痕（可展示「摘要演进：旧→新」）；
          ② 当前比对以 is_current 为准，与链上单槽天然一致；
          ③ 防篡改与「合法重存证/换盐迁移」不再互斥。
    """
    chain_ok = "success" if result.get("ok") else result.get("mode", "mock")

    current = EvidenceRecord.objects.filter(
        biz_type=biz_type, biz_id=biz_id, is_current=True).first()

    # 1) 首次存证
    if current is None:
        # 兼容历史数据：若存在但都被标为非 current，取最大版本续号
        last_ver = (EvidenceRecord.objects
                    .filter(biz_type=biz_type, biz_id=biz_id)
                    .order_by("-version")
                    .values_list("version", flat=True)
                    .first()) or 0
        return EvidenceRecord.objects.create(
            biz_type=biz_type, biz_id=biz_id, biz_no=biz_no,
            data_hash=digest, payload=payload,
            version=last_ver + 1, is_current=True,
            tx_hash=result.get("tx_hash", ""),
            block_height=result.get("block_height", 0),
            chain_status=chain_ok,
        )

    # 2) 摘要一致：在原行补齐链上信息即可（不新增版本）
    if current.data_hash == digest:
        current.biz_no = biz_no or current.biz_no
        current.payload = payload
        if result.get("ok"):
            current.tx_hash = result.get("tx_hash", current.tx_hash)
            current.block_height = result.get("block_height", current.block_height)
            current.chain_status = chain_ok
        current.save()
        return current

    # 3) 摘要不同：归档旧版本，追加新版本
    current.is_current = False
    current.save(update_fields=["is_current", "updated_at"])
    return EvidenceRecord.objects.create(
        biz_type=biz_type, biz_id=biz_id, biz_no=biz_no,
        data_hash=digest, payload=payload,
        version=current.version + 1, is_current=True,
        tx_hash=result.get("tx_hash", ""),
        block_height=result.get("block_height", 0),
        chain_status=chain_ok,
    )


def anchor_certificate(cert: Certificate) -> EvidenceRecord:
    """
    对合格证执行存证：
      1. 计算哈希并写回合格证
      2. 上链（真实链优先，失败降级模拟）
      3. 写入存证记录

    ★ 写入顺序（原 M4 修复）：先只写 cert_hash（本地重算即可得，廉价），
    待上链 + 落存证记录**之后**，再把 is_anchored/anchored_at 标为已完成。
    这样中间若上链抛异常，不会留下「is_anchored=True 但无存证记录」的半态
    —— 半态会让验真页展示「已存证」却查不到记录，自相矛盾。
    （注意：本函数不做 transaction.atomic —— 40s 的 RPC 会把 DB 事务按住，
      故采用「先落字段、后标完成」的补偿式顺序，而非包大事务。）
    """
    payload = build_certificate_payload(cert)
    digest = sha256_hex(payload)
    # 第一步：只固化哈希（不置 is_anchored）
    cert.cert_hash = digest
    cert.save(update_fields=["cert_hash", "updated_at"])

    # 第二步：上链 + 落存证记录
    result = _anchor(digest, payload, EvidenceRecord.BizType.CERTIFICATE,
                     cert.id, cert.cert_no)
    record = _save_record(EvidenceRecord.BizType.CERTIFICATE, cert.id,
                          cert.cert_no, digest, payload, result)

    # 第三步：确认有存证记录后，再标记「已存证」
    cert.is_anchored = True
    cert.anchored_at = datetime.now()
    cert.save(update_fields=["is_anchored", "anchored_at", "updated_at"])
    return record


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

    # ★ cert_no 兜底（原 M2）：payload 已兜底但 biz_no 没兜底，会写 None 进
    #   NOT NULL 列。这里统一用兜底串，payload 与 biz_no 保持一致。
    cert_no = cert.cert_no or f"UNISSUED-{cert.id}"
    payload = "|".join([cert_no, "MERKLE", root])
    result = _anchor(root, payload, EvidenceRecord.BizType.MERKLE,
                     cert.id, cert_no)
    return _save_record(EvidenceRecord.BizType.MERKLE, cert.id,
                        cert_no, root, payload, result)


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
    # ★ cert_no 兜底（原 M2）：草稿证 cert_no 为 None，直接参与拼接会得到
    #   "None"，写进 biz_no（NOT NULL）虽不炸但语义错误；统一转成稳定占位串。
    cert_no = cert.cert_no or f"UNISSUED-{cert.id}"
    payload = "|".join([
        cert_no, "REVOKED", reason.strip(), _fmt(_dt.now()),
    ])

    # 1. 链上打撤销标记（对原 certificate bizNo；失败降级不中断业务）
    revoke_result = _revoke_on_chain(cert_no, reason)

    # 2. 链上写一条 REVOKE 类型的存证记录作为审计留痕（bizType=revoke 与 certificate 不冲突）
    digest = sha256_hex(payload)
    result = _anchor(digest, payload, EvidenceRecord.BizType.REVOKE,
                     cert.id, cert_no)
    # 若 revokeEvidence 已成功上链，则用其 tx_hash 作为这条审计记录的链上锚点
    if revoke_result.get("ok") and revoke_result.get("tx_hash"):
        result = dict(result, tx_hash=revoke_result["tx_hash"],
                      block_height=revoke_result.get("block_height", 0),
                      mode=revoke_result.get("mode", "mock"))

    return _save_record(EvidenceRecord.BizType.REVOKE, cert.id,
                        cert_no, digest, payload, result)


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
    # ★ 取「当前版本」记录（B3 追加式版本）：历史版本 is_current=False，不参与比对
    record = EvidenceRecord.objects.filter(
        biz_type=EvidenceRecord.BizType.CERTIFICATE, biz_id=cert.id,
        is_current=True
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
    # ★ 原 M5：只看 tx_hash 是否 0x 开头挡不住降级存证 —— mock 的 tx_hash
    #   本身就是 0x+64hex。真正的判据是 record.chain_status：
    #   只有真链上成功（"success"）的记录才值得做链上核验，否则离线/mock
    #   模式下每次扫码都要打 3 次 RPC（各 10s），公开接口可被拖垮。
    chain_evidence_ok = (
        record is not None
        and record.chain_status == "success"
        and record.tx_hash.startswith("0x")
    )
    if check_chain and chain_evidence_ok:
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
    elif check_chain and record is not None:
        # 降级存证（mock）或链关闭：不发起 RPC，明确告知只过第一层
        result["chain_detail"] = "该存证为降级模式（未真实上链），仅完成本地比对"

    return result