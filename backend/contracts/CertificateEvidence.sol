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
 *   txIndex[txHash] = ...                —— 便于按交易反查
 */
contract CertificateEvidence {

    struct Evidence {
        string  dataHash;    // 业务数据的 SHA-256 摘要（hex，无 0x 前缀）
        string  bizType;     // 业务类型：certificate / batch / report
        uint256 timestamp;   // 上链时间戳
        address operator;    // 操作人地址（存证发起方）
        uint256 blockNum;    // 所在区块高度
        bool    exists;      // 是否存在（区分"未存证"与"存证为空"）
    }

    // bizType => bizNo => Evidence
    mapping(string => mapping(string => Evidence)) private records;

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

    /**
     * @notice 写入一条存证记录（同一 bizType+bizNo 重复调用会覆盖并更新时间）
     * @param bizType  业务类型
     * @param bizNo    业务编号
     * @param dataHash 数据摘要
     */
    function addEvidence(string memory bizType, string memory bizNo, string memory dataHash) public {
        require(bytes(dataHash).length > 0, "dataHash cannot be empty");

        if (!records[bizType][bizNo].exists) {
            totalEvidence += 1;
        }
        records[bizType][bizNo] = Evidence({
            dataHash:  dataHash,
            bizType:   bizType,
            timestamp: block.timestamp,
            operator:  msg.sender,
            blockNum:  block.number,
            exists:    true
        });

        emit EvidenceAdded(bizType, bizNo, dataHash, msg.sender, block.timestamp);
    }

    /**
     * @notice 读取存证记录
     * @return dataHash 数据摘要 / timestamp 上链时间 / operator 操作人 / blockNum 区块高度 / exists 是否存在
     */
    function getEvidence(string memory bizType, string memory bizNo)
        public
        view
        returns (string memory dataHash, uint256 timestamp, address operator,
                 uint256 blockNum, bool exists)
    {
        Evidence memory e = records[bizType][bizNo];
        return (e.dataHash, e.timestamp, e.operator, e.blockNum, e.exists);
    }

    /**
     * @notice 校验摘要是否与链上记录一致（验真核心接口）
     * @return matched 是否一致；exists 链上是否已有该存证
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
        return (keccak256(bytes(e.dataHash)) == keccak256(bytes(dataHash)), true);
    }

    /// @notice 合约版本，便于运维确认部署版本
    function version() public pure returns (string memory) {
        return "CertificateEvidence v1.0";
    }
}
