// SPDX-License-Identifier: MIT
pragma solidity >=0.6.10 <0.8.27;

/**
 * @title CertificateEvidence 农产品合格证存证合约
 * @notice 只存证哈希、不上链原始业务数据 —— 兼顾可信存证与数据隐私。
 *
 * 设计说明（毕设要点）：
 *  1. 业务侧（Django）用 SHA-256 计算合格证/批次/检测报告的规范化摘要；
 *  2. 摘要通过本合约写入联盟链，形成不可篡改的存证记录；
 *  3. 验真时重新计算摘要，与链上记录比对，即可判断数据是否被篡改；
 *  4. 链上只保存 32 字节摘要 + 元信息，不保存产地、产量等商业数据。
 *
 * 存储结构：
 *   records[bizType][bizNo] = Evidence  —— 按业务类型+业务编号索引，天然防重复存证
 *
 * v2.0 变更（2026-09-26 引入「作废双向上链」）：
 *  Evidence 新增 revoked / revokedAt / revokeReason 三字段，并新增
 *  revokeEvidence()：对一条已存证的记录打「已撤销」标记（而非删链）。
 *  意义：把「作废」这个动作本身固化为链上事实，公众验真时能查到
 *        「该合格证已被撤销」，避免「本地已作废、链上却仍显合法」的信任悖论。
 *
 * v3.0 变更（2026-10-11 安全加固）：
 *  1. ★ 写入者白名单（onlyWriter）：addEvidence / revokeEvidence 由 public
 *     改为仅限 owner 及其授权地址调用。原实现任何地址都能写入，与本合约
 *     「链上记录不可被任意改写」的承诺相悖。
 *  2. ★ 防覆盖：addEvidence 对**已存在**的记录默认拒绝（require），
 *     避免「重复写入把已撤销状态重置回未撤销」这一信任漏洞。
 *     确需更新摘要（业务上合法的重存证）时走 updateEvidence()，
 *     它保留 revoked 标记 —— 已作废的记录不会被「更新」变回有效。
 *  3. ★ 修正文档与实现不一致：原 README 称「重复调用会 revert」，
 *     但 v2.0 实现其实是静默覆盖。v3.0 起该说法才成立。
 */
contract CertificateEvidence {

    struct Evidence {
        string  dataHash;      // 业务数据的 SHA-256 摘要（hex，无 0x 前缀）
        string  bizType;       // 业务类型：certificate / batch / report / inspection
        uint256 timestamp;     // 上链时间戳
        address operator;      // 操作人地址（存证发起方）
        uint256 blockNum;      // 所在区块高度
        bool    exists;        // 是否存在（区分"未存证"与"存证为空"）
        bool    revoked;       // 是否已被撤销（作废）
        uint256 revokedAt;     // 撤销时间戳（0 表示未撤销）
        string  revokeReason;  // 撤销原因
        uint256 version;       // ★ 追加式版本号（每次更新 +1，可回溯修改历史）
    }

    // bizType => bizNo => Evidence
    mapping(string => mapping(string => Evidence)) private records;

    // ★ 写入者白名单：owner + 授权地址
    address public owner;
    mapping(address => bool) public writers;

    // 累计存证条数（便于看板展示）
    uint256 public totalEvidence;

    // 每次存证触发事件，便于链上按事件检索
    event EvidenceAdded(
        string indexed bizType,
        string indexed bizNo,
        string dataHash,
        address operator,
        uint256 timestamp
    );

    // ★ 摘要更新事件（追加式版本，保留作废状态）
    event EvidenceUpdated(
        string indexed bizType,
        string indexed bizNo,
        string oldHash,
        string newHash,
        uint256 version,
        address operator,
        uint256 timestamp
    );

    // ★ 写入者授权变更事件
    event WriterChanged(address indexed account, bool allowed);

    // 每次撤销触发事件，便于链上按事件检索"谁在何时作废了什么"
    event EvidenceRevoked(
        string indexed bizType,
        string indexed bizNo,
        string reason,
        address operator,
        uint256 timestamp
    );

    /**
     * @notice 部署时把部署者登记为 owner（唯一初始写入者）。
     */
    constructor() public {
        owner = msg.sender;
        writers[msg.sender] = true;
        emit WriterChanged(msg.sender, true);
    }

    /**
     * @notice 仅白名单可写（owner 或已授权地址）。
     */
    modifier onlyWriter() {
        require(writers[msg.sender], "not authorized to write evidence");
        _;
    }

    /**
     * @notice 仅 owner 可授权/取消授权写入地址。
     */
    modifier onlyOwner() {
        require(msg.sender == owner, "only owner");
        _;
    }

    /**
     * @notice 授权/取消授权一个写入地址（业务侧存证账户）。
     */
    function setWriter(address account, bool allowed) public onlyOwner {
        require(account != address(0), "zero address");
        writers[account] = allowed;
        emit WriterChanged(account, allowed);
    }

    /**
     * @notice 转移 owner 权限。
     * ★ 续审修复：转移时**撤销旧 owner 的 writers 权限** ——
     *   原实现只给新 owner 授权，旧 owner 仍在白名单里，转移后依然能
     *   写入/更新/撤销存证，等于「换了管理员但前任的钥匙没收回」。
     *   若确实需要保留旧地址的写入权，请在转移后显式 setWriter(oldOwner, true)。
     */
    function transferOwnership(address newOwner) public onlyOwner {
        require(newOwner != address(0), "zero address");
        address oldOwner = owner;
        // 先收回旧 owner 的写入权（除非就是他自己，防自伤）
        if (oldOwner != newOwner && oldOwner != address(0)) {
            writers[oldOwner] = false;
            emit WriterChanged(oldOwner, false);
        }
        owner = newOwner;
        writers[newOwner] = true;
        emit WriterChanged(newOwner, true);
    }

    /**
     * @notice 新增一条存证记录。
     * ★ 若该 bizType+bizNo **已存在**，直接 revert（不做静默覆盖）。
     *   原因：v2.0 的覆盖语义会把 `revoked` 重置为 false ——
     *   只要有人重复写入同一条记录，原本已作废的证就会「复活」，
     *   等于链上不可篡改的承诺被一个重复调用架空。
     *   需要更新摘要（合法重存证）请用 updateEvidence()。
     */
    function addEvidence(string memory bizType, string memory bizNo, string memory dataHash)
        public
        onlyWriter
    {
        require(bytes(dataHash).length > 0, "dataHash cannot be empty");
        require(!records[bizType][bizNo].exists, "evidence already exists, use updateEvidence");

        totalEvidence += 1;
        records[bizType][bizNo] = Evidence({
            dataHash:  dataHash,
            bizType:   bizType,
            timestamp: block.timestamp,
            operator:  msg.sender,
            blockNum:  block.number,
            exists:    true,
            revoked:   false,
            revokedAt: 0,
            revokeReason: "",
            version:   1
        });

        emit EvidenceAdded(bizType, bizNo, dataHash, msg.sender, block.timestamp);
    }

    /**
     * @notice 更新一条已存在记录的摘要（合法的「重存证」）。
     * ★ 关键约束：**不允许**把已撤销的记录更新为有效 ——
     *   revoked / revokedAt / revokeReason 三个字段原样保留。
     *   这样「作废」仍是链上不可逆的事实，符合业务语义。
     * @return newVersion 更新后的版本号
     */
    function updateEvidence(string memory bizType, string memory bizNo, string memory dataHash)
        public
        onlyWriter
        returns (uint256 newVersion)
    {
        require(bytes(dataHash).length > 0, "dataHash cannot be empty");
        Evidence storage e = records[bizType][bizNo];
        require(e.exists, "evidence not found, use addEvidence");

        string memory oldHash = e.dataHash;
        e.dataHash = dataHash;
        e.timestamp = block.timestamp;
        e.operator = msg.sender;
        e.blockNum = block.number;
        e.version += 1;
        // ★ revoked / revokedAt / revokeReason 保持不变（不复活）

        emit EvidenceUpdated(bizType, bizNo, oldHash, dataHash, e.version,
                             msg.sender, block.timestamp);
        return e.version;
    }

    /**
     * @notice 撤销一条已存证记录（作废双向上链）。
     *         不删除原记录，而是把 revoked 置 true 并记录撤销原因与时间。
     */
    function revokeEvidence(string memory bizType, string memory bizNo, string memory reason)
        public
        onlyWriter
    {
        require(records[bizType][bizNo].exists, "evidence not found");
        require(!records[bizType][bizNo].revoked, "already revoked");

        records[bizType][bizNo].revoked = true;
        records[bizType][bizNo].revokedAt = block.timestamp;
        records[bizType][bizNo].revokeReason = reason;

        emit EvidenceRevoked(bizType, bizNo, reason, msg.sender, block.timestamp);
    }

    /**
     * @notice 读取存证记录
     */
    function getEvidence(string memory bizType, string memory bizNo)
        public
        view
        returns (string memory dataHash, uint256 timestamp, address operator,
                 uint256 blockNum, bool exists,
                 bool revoked, uint256 revokedAt, string memory revokeReason)
    {
        Evidence memory e = records[bizType][bizNo];
        return (e.dataHash, e.timestamp, e.operator, e.blockNum, e.exists,
                e.revoked, e.revokedAt, e.revokeReason);
    }

    /**
     * @notice 读取存证记录（含版本号）。
     */
    function getEvidenceWithVersion(string memory bizType, string memory bizNo)
        public
        view
        returns (string memory dataHash, uint256 version, bool exists,
                 bool revoked, uint256 timestamp)
    {
        Evidence memory e = records[bizType][bizNo];
        return (e.dataHash, e.version, e.exists, e.revoked, e.timestamp);
    }

    /**
     * @notice 校验摘要是否与链上记录一致（验真核心接口）。
     */
    function verifyEvidence(string memory bizType, string memory bizNo, string memory dataHash)
        public
        view
        returns (bool matched, bool exists)
    {
        Evidence memory e = records[bizType][bizNo];
        if (!e.exists) {
            return (false, false);
        }
        if (e.revoked) {
            return (false, true);
        }
        return (keccak256(bytes(e.dataHash)) == keccak256(bytes(dataHash)), true);
    }

    /**
     * @notice 查询一条记录是否已被撤销（供验真页快速判断）
     */
    function isRevoked(string memory bizType, string memory bizNo)
        public
        view
        returns (bool found, bool revoked)
    {
        Evidence memory e = records[bizType][bizNo];
        return (e.exists, e.revoked);
    }

    /// @notice 合约版本，便于运维确认部署版本
    function version() public pure returns (string memory) {
        return "CertificateEvidence v3.0";
    }
}