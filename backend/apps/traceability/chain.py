"""
FISCO BCOS 联盟链客户端（web3.py + 存证合约）

设计要点：
1. 通过 web3_rpc（HTTP，默认 8545）与联盟链交互 —— 3.x 的 Channel 端口强制 SSL，
   走 web3 RPC 可免证书，Django 侧接入最省事。
2. 只上链「哈希」，不上链原始业务数据 —— 既满足可信存证，又不泄露商业数据。
3. 调用 CertificateEvidence 合约的 addEvidence / verifyEvidence / getEvidence，
   链上按 (bizType, bizNo) 索引，天然防重复存证，且交易 status 正常为 1。
4. 链不可用时优雅降级：不抛异常中断业务，返回 ok=False，调用方回退到模拟存证
   （便于无链环境下开发演示）。
5. 配置全部走 settings.FISCO_BCOS，私钥从环境变量读取，不进 Git。

依赖：pip install web3

⚠️ 交易格式坑：FISCO BCOS 3.x 只接受 legacy 交易。web3 8.x 中不要给 tx 传 "type" 字段
   （传 type=0 会报 "Unknown Transaction type: 0"），不传即按 legacy 处理。
"""
import json
import logging
import os
import uuid

from django.conf import settings

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 配置读取
# ------------------------------------------------------------------
_CHAIN_CFG = getattr(settings, "FISCO_BCOS", {})

WEB3_RPC = _CHAIN_CFG.get("WEB3_RPC", "http://127.0.0.1:8545")
CHAIN_ID = int(_CHAIN_CFG.get("CHAIN_ID", 20200))
GROUP_ID = _CHAIN_CFG.get("GROUP_ID", "group0")
CONTRACT_ADDRESS = _CHAIN_CFG.get("CONTRACT_ADDRESS", "") or ""
PRIVATE_KEY = _CHAIN_CFG.get("PRIVATE_KEY") or os.environ.get("FISCO_PRIVATE_KEY", "")
GAS_LIMIT = int(_CHAIN_CFG.get("GAS_LIMIT", 500000))
ABI_PATH = _CHAIN_CFG.get("ABI_PATH", "") or ""

# 合约 ABI：与 contracts/CertificateEvidence.sol 对应
# （内联避免依赖外部文件；如需替换合约，改 CONTRACT_ADDRESS + 覆盖此 ABI 即可）
CONTRACT_ABI = [
    {
        "inputs": [
            {"internalType": "string", "name": "bizType", "type": "string"},
            {"internalType": "string", "name": "bizNo", "type": "string"},
            {"internalType": "string", "name": "dataHash", "type": "string"},
        ],
        "name": "addEvidence",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "string", "name": "bizType", "type": "string"},
            {"internalType": "string", "name": "bizNo", "type": "string"},
            {"internalType": "string", "name": "reason", "type": "string"},
        ],
        "name": "revokeEvidence",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "string", "name": "bizType", "type": "string"},
            {"internalType": "string", "name": "bizNo", "type": "string"},
        ],
        "name": "getEvidence",
        "outputs": [
            {"internalType": "string", "name": "dataHash", "type": "string"},
            {"internalType": "uint256", "name": "timestamp", "type": "uint256"},
            {"internalType": "address", "name": "operator", "type": "address"},
            {"internalType": "uint256", "name": "blockNum", "type": "uint256"},
            {"internalType": "bool", "name": "exists", "type": "bool"},
            {"internalType": "bool", "name": "revoked", "type": "bool"},
            {"internalType": "uint256", "name": "revokedAt", "type": "uint256"},
            {"internalType": "string", "name": "revokeReason", "type": "string"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "string", "name": "bizType", "type": "string"},
            {"internalType": "string", "name": "bizNo", "type": "string"},
            {"internalType": "string", "name": "dataHash", "type": "string"},
        ],
        "name": "verifyEvidence",
        "outputs": [
            {"internalType": "bool", "name": "matched", "type": "bool"},
            {"internalType": "bool", "name": "exists", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "string", "name": "bizType", "type": "string"},
            {"internalType": "string", "name": "bizNo", "type": "string"},
        ],
        "name": "isRevoked",
        "outputs": [
            {"internalType": "bool", "name": "found", "type": "bool"},
            {"internalType": "bool", "name": "revoked", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "totalEvidence",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "version",
        "outputs": [{"internalType": "string", "name": "", "type": "string"}],
        "stateMutability": "pure",
        "type": "function",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "string", "name": "bizType", "type": "string"},
            {"indexed": True, "internalType": "string", "name": "bizNo", "type": "string"},
            {"indexed": False, "internalType": "string", "name": "dataHash", "type": "string"},
            {"indexed": False, "internalType": "address", "name": "operator", "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "timestamp", "type": "uint256"},
        ],
        "name": "EvidenceAdded",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "string", "name": "bizType", "type": "string"},
            {"indexed": True, "internalType": "string", "name": "bizNo", "type": "string"},
            {"indexed": False, "internalType": "string", "name": "reason", "type": "string"},
            {"indexed": False, "internalType": "address", "name": "operator", "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "timestamp", "type": "uint256"},
        ],
        "name": "EvidenceRevoked",
        "type": "event",
    },
]


def _load_abi():
    """优先从 ABI_PATH 读取外部 ABI 文件，否则用内联 ABI。"""
    if ABI_PATH and os.path.exists(ABI_PATH):
        try:
            with open(ABI_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取 ABI 文件失败(%s)，改用内联 ABI: %s", ABI_PATH, exc)
    return CONTRACT_ABI


def _get_w3():
    """惰性导入 web3 并建立连接；任何失败返回 None。"""
    try:
        from web3 import Web3
    except ImportError:
        logger.warning("未安装 web3，无法上链。请执行: pip install web3")
        return None
    try:
        w3 = Web3(Web3.HTTPProvider(WEB3_RPC, request_kwargs={"timeout": 10}))
        if not w3.is_connected():
            logger.warning("无法连接联盟链节点: %s", WEB3_RPC)
            return None
        return w3
    except Exception as exc:  # noqa: BLE001
        logger.warning("连接联盟链异常: %s", exc)
        return None


def _get_contract(w3):
    """返回已绑定的合约对象；未配置地址时返回 None。"""
    if not CONTRACT_ADDRESS:
        return None
    return w3.eth.contract(address=w3.to_checksum_address(CONTRACT_ADDRESS),
                           abi=_load_abi())


def _send(w3, fn_call, private_key: str) -> dict:
    """构造、签名、发送一笔合约交易，返回 {tx_hash, block_height, status}。"""
    account = w3.eth.account.from_key(private_key)
    tx = fn_call.build_transaction({
        "from": account.address,
        "value": 0,
        "gas": GAS_LIMIT,
        "gasPrice": 0,
        "nonce": w3.eth.get_transaction_count(account.address),
        "chainId": CHAIN_ID,
        # 不传 type —— FISCO BCOS 3.x 只收 legacy 交易，web3 8.x 传 type=0 会报错
    })
    signed = w3.eth.account.sign_transaction(tx, private_key)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    h = w3.eth.send_raw_transaction(raw)
    tx_hex = h.hex() if hasattr(h, "hex") else str(h)
    if not tx_hex.startswith("0x"):
        tx_hex = "0x" + tx_hex
    receipt = w3.eth.wait_for_transaction_receipt(tx_hex, timeout=40)
    return {
        "tx_hash": tx_hex,
        "block_height": int(receipt.get("blockNumber") or 0),
        "status": int(receipt.get("status") or 0),
    }


def chain_available() -> bool:
    """链是否可用（供健康检查/前端展示用）。"""
    return _get_w3() is not None


def chain_status() -> dict:
    """返回链状态快照，用于看板/健康检查。"""
    w3 = _get_w3()
    if w3 is None:
        return {"connected": False, "rpc": WEB3_RPC,
                "message": "链节点不可达或 web3 未安装"}
    try:
        info = {
            "connected": True,
            "rpc": WEB3_RPC,
            "chain_id": w3.eth.chain_id,
            "group_id": GROUP_ID,
            "block_number": w3.eth.block_number,
            "node_count": 2,
            "contract": CONTRACT_ADDRESS or "(未配置)",
        }
        contract = _get_contract(w3)
        if contract is not None:
            try:
                info["contract_version"] = contract.functions.version().call()
                info["total_evidence"] = contract.functions.totalEvidence().call()
            except Exception as exc:  # noqa: BLE001
                info["contract_message"] = f"合约读取失败: {exc}"
        return info
    except Exception as exc:  # noqa: BLE001
        return {"connected": False, "rpc": WEB3_RPC, "message": str(exc)}


# ------------------------------------------------------------------
# 上链
# ------------------------------------------------------------------
def _mock_tx_hash() -> str:
    """无链环境下的模拟交易哈希（保证 0x + 64 hex）。"""
    return "0x" + (uuid.uuid4().hex + uuid.uuid4().hex)[:64]


def send_evidence(data_hash: str, biz_no: str = "", biz_type: str = "") -> dict:
    """
    把存证哈希写入联盟链。

    ★ v3.0 合约语义（安全加固后）：
      - `addEvidence` **已存在则 revert**（防止重复写入把 revoked 重置为 false）
      - 合法「重存证」改用 `updateEvidence`（保留 revoked / revokedAt）

    因此这里**先查链上是否已有该 bizType+bizNo**：
      - 不存在 → addEvidence
      - 已存在 → updateEvidence（追加式版本，不复活已作废记录）

    参数
      data_hash : 业务数据的 SHA-256 十六进制摘要（后端计算，如 cert_hash）
      biz_no    : 业务编号（链上按此索引）
      biz_type  : 业务类型（certificate / batch / report / inspection）

    返回
      {"ok": bool, "tx_hash": str, "block_height": int, "mode": "chain"|"mock", "message": str}
    """
    w3 = _get_w3()

    # ---- 降级：链不可用 ----
    if w3 is None or not PRIVATE_KEY or not CONTRACT_ADDRESS:
        if w3 is None:
            reason = "链节点不可达"
        elif not PRIVATE_KEY:
            reason = "未配置存证私钥 FISCO_PRIVATE_KEY"
        else:
            reason = "未配置存证合约地址 FISCO_CONTRACT_ADDRESS"
        logger.info("存证降级为模拟模式（%s）: %s", reason, biz_no or data_hash[:16])
        return {
            "ok": False, "tx_hash": _mock_tx_hash(), "block_height": 0,
            "mode": "mock", "message": f"{reason}，已使用模拟存证",
        }

    try:
        contract = _get_contract(w3)
        btype = biz_type or "certificate"
        bno = biz_no or "-"

        # ★ 先判断链上是否已存在该记录，决定走 add 还是 update
        exists = False
        try:
            _hash, _ts, _op, _bn, exists, _rv, _ra, _rr = \
                contract.functions.getEvidence(btype, bno).call()
        except Exception:  # noqa: BLE001
            # 查询失败时保守按「已存在」处理：updateEvidence 对不存在的记录会 revert，
            # 会走下面的异常分支降级；而若误用 addEvidence 覆盖已撤销记录，
            # 后果更严重（信任漏洞）。两害相权取轻。
            exists = True

        method = "updateEvidence" if exists else "addEvidence"
        fn = getattr(contract.functions, method)(btype, bno, data_hash)
        result = _send(w3, fn, PRIVATE_KEY)
        ok = result["status"] == 1
        logger.info("存证上链%s（%s）: %s block=%s",
                    "成功" if ok else "失败", method,
                    result["tx_hash"], result["block_height"])
        return {
            "ok": ok,
            "tx_hash": result["tx_hash"],
            "block_height": result["block_height"],
            "mode": "chain",
            "message": ("已上链存证" if ok else "交易执行失败（status != 1）"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("上链异常: %s", exc)
        return {
            "ok": False, "tx_hash": _mock_tx_hash(), "block_height": 0,
            "mode": "mock", "message": f"上链异常：{exc}",
        }


# ------------------------------------------------------------------
# 链上查询（验真辅助）
# ------------------------------------------------------------------
def revoke_evidence(biz_type: str, biz_no: str, reason: str = "") -> dict:
    """
    在链上撤销一条存证（作废双向上链）。

    对应合约 revokeEvidence：不删原记录，而是把 revoked 置 true 并记录撤销原因。
    这样验真时能查到「该证已被撤销」，与「从未存在」和「被篡改」区分开。

    返回与 send_evidence 同构：{"ok", "tx_hash", "block_height", "mode", "message"}
    """
    w3 = _get_w3()
    if w3 is None or not PRIVATE_KEY or not CONTRACT_ADDRESS:
        if w3 is None:
            reason_msg = "链节点不可达"
        elif not PRIVATE_KEY:
            reason_msg = "未配置存证私钥 FISCO_PRIVATE_KEY"
        else:
            reason_msg = "未配置存证合约地址 FISCO_CONTRACT_ADDRESS"
        logger.info("作废降级为模拟模式（%s）: %s", reason_msg, biz_no)
        return {
            "ok": False, "tx_hash": _mock_tx_hash(), "block_height": 0,
            "mode": "mock", "message": f"{reason_msg}，作废未上链",
        }

    try:
        contract = _get_contract(w3)
        fn = contract.functions.revokeEvidence(biz_type or "certificate",
                                               biz_no or "-", reason or "")
        result = _send(w3, fn, PRIVATE_KEY)
        ok = result["status"] == 1
        logger.info("作废上链%s: %s block=%s",
                    "成功" if ok else "失败", result["tx_hash"], result["block_height"])
        return {
            "ok": ok,
            "tx_hash": result["tx_hash"],
            "block_height": result["block_height"],
            "mode": "chain",
            "message": "已上链作废" if ok else "交易执行失败（status != 1）",
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("作废上链异常: %s", exc)
        return {
            "ok": False, "tx_hash": _mock_tx_hash(), "block_height": 0,
            "mode": "mock", "message": f"作废上链异常：{exc}",
        }


def query_evidence(biz_type: str, biz_no: str) -> dict:
    """
    从链上读取存证记录（调用 getEvidence）。
    返回 {ok, exists, data_hash, timestamp, operator, block_number, revoked, revoked_at, revoke_reason}
    """
    w3 = _get_w3()
    if w3 is None:
        return {"ok": False, "message": "链节点不可达"}
    if not CONTRACT_ADDRESS:
        return {"ok": False, "message": "未配置存证合约地址"}
    try:
        contract = _get_contract(w3)
        data_hash, ts, operator, block_num, exists, revoked, revoked_at, revoke_reason = \
            contract.functions.getEvidence(biz_type, biz_no).call()
        return {
            "ok": True,
            "exists": bool(exists),
            "data_hash": data_hash,
            "timestamp": int(ts),
            "operator": operator,
            "block_number": int(block_num),
            "revoked": bool(revoked),
            "revoked_at": int(revoked_at),
            "revoke_reason": revoke_reason,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc)}


def verify_on_chain(biz_type: str, biz_no: str, data_hash: str) -> dict:
    """
    链上验真（调用 verifyEvidence）：判断给定摘要是否与链上记录一致。
    返回 {ok, matched, exists, revoked, message}
    说明：若该记录在链上已被撤销，matched 恒为 False 且 revoked=True（作废≠篡改）。
    """
    w3 = _get_w3()
    if w3 is None:
        return {"ok": False, "matched": False, "exists": False, "revoked": False,
                "message": "链节点不可达"}
    if not CONTRACT_ADDRESS:
        return {"ok": False, "matched": False, "exists": False, "revoked": False,
                "message": "未配置存证合约地址"}
    try:
        contract = _get_contract(w3)
        # 先查是否已被撤销，再校验摘要
        found, revoked = contract.functions.isRevoked(biz_type, biz_no).call()
        if not found:
            return {"ok": True, "matched": False, "exists": False, "revoked": False,
                    "message": "链上无该存证记录"}
        if revoked:
            return {"ok": True, "matched": False, "exists": True, "revoked": True,
                    "message": "该存证已在链上被撤销（作废）"}
        matched, exists = contract.functions.verifyEvidence(
            biz_type, biz_no, data_hash).call()
        if not exists:
            msg = "链上无该存证记录"
        elif matched:
            msg = "链上摘要一致，存证可信"
        else:
            msg = "链上摘要不一致，数据可能被篡改"
        return {"ok": True, "matched": bool(matched), "exists": bool(exists),
                "revoked": False, "message": msg}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "matched": False, "exists": False, "revoked": False,
                "message": str(exc)}
