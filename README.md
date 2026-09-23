# 基于 Django 的柑橘合格证追溯系统设计与实现

> 前后端分离架构 · 区块链可信存证 · 二维码追溯 · 产销衔接

## 一、项目简介

本系统面向柑橘等特色农产品的质量安全监管场景，围绕 **"生产主体备案 → 生产批次建档 → 检测报告 → 承诺达标合格证签发 → 哈希上链存证 → 二维码扫码验真 → 产销供需对接"** 形成完整业务闭环。

选题依据：农业农村部 2025 年第 4 号令《农产品质量安全承诺达标合格证管理办法》，2026 年 2 月起全面启用"身份证"式农产品合格证。

## 二、技术栈

| 层 | 技术 |
| --- | --- |
| 前端 | Vue 3 + Vite + Element Plus + Pinia + Vue Router + Axios + ECharts |
| 后端 | Django + DRF（REST framework）+ SimpleJWT |
| 数据库 | SQLite（默认）/ MySQL 8 |
| 可信存证 | Python hashlib（SHA-256）+ **FISCO BCOS 3.17.1 联盟链** |
| 链上交互 | web3.py（web3 RPC / HTTP，免证书） |
| 智能合约 | Solidity 0.8.26 · `CertificateEvidence.sol` |
| 追溯 | 合格证二维码 → 公众验真页 |
| 部署 | Nginx（前端静态 + /api 反代）+ Gunicorn |

## 三、核心亮点

### 1. 双层可信存证（防篡改）★ 核心创新点

存证采用 **"本地哈希 + 联盟链上链"双层校验**：

**第一层 · 本地哈希比对**
- 合格证签发时，后端对关键字段（证书编号、主体、产品、数量、批次、报告、日期等）
  按固定规则拼接后计算 **SHA-256 摘要**，写入存证记录表。
- 验真时重新读取当前数据、按同样规则重算哈希，与存证摘要比对。
- **哈希由后端生成**，前端无法伪造。

**第二层 · 联盟链上链核验**
- 摘要通过 `CertificateEvidence` 合约写入 **FISCO BCOS 联盟链**（单机 2 节点，PBFT 共识）。
- 验真时调用合约 `verifyEvidence()` 与链上摘要比对。
- **链上数据不可篡改**，因此能识别"连数据库摘要一起改"的高级攻击 —— 这是纯数据库方案无法防御的。

> 实测结论：攻击者若同时篡改业务数据 + 数据库存证摘要，**仅本地比对会误判为"可信"**，
> 而加上链上核验后能被准确识别为"不可信"。这正是引入区块链的价值所在。

链上只保存 32 字节摘要，**不保存产地、产量等商业数据**，兼顾可信与隐私。

### 2. 二维码追溯

- 每张合格证可生成二维码，指向公众验真页。
- 消费者扫码即可查看产品、主体、检测结论、有效期及存证信息（含链上交易哈希、区块高度）。

### 3. 产销衔接（差异化）

- 种植主体发布供应信息，收购商发布求购信息。
- 供应信息可**关联合格证**，实现"先扫码验真，再对接下单"。
- **智能匹配推荐**：可解释的多因子加权打分模型，输出 0–100 匹配度与逐因子明细。

  | 因子 | 权重 | 计分规则 |
  | --- | --- | --- |
  | 品种匹配 | 30 | 品种一致 30；同产品不同品种 15；产品不同 0 |
  | 数量满足度 | 25 | `min(供,需)/max(供,需) × 25`（比值而非差值，体现相对差异） |
  | 价格契合度 | 20 | 报价差在期望价 ±15% 内满分，15%~60% 线性衰减，≥60% 得 0 |
  | 地域邻近度 | 15 | 同地区 15；同省市前缀 8；跨区域 0 |
  | 信用合规 | 10 | 供方主体已审核 + 关联合格证已签发（各 5 分） |

- **产销意向单（MatchIntent）**：把"一次对接"建模为独立实体，记录发起人、数量、成交价、
  匹配度评分与评分明细，状态流转 `待确认 → 已接受/已拒绝/已完成`，实现对接全程留痕。
- 种植主体与收购商**双向均可发起**对接，符合"产销衔接"的双向撮合本质。

### 4. 四角色权限差异化 ★

系统区分 **系统管理员 / 监管人员 / 种植主体 / 收购商** 四类角色，权限分**接口级**与**数据级**两层：

**数据级可见范围**

| 资源 | 管理员 | 监管人员 | 种植主体 | 收购商 |
| --- | --- | --- | --- | --- |
| 生产主体 | 全部 | 全部 | 仅自己创建的 | 仅已审核通过的 |
| 生产批次 | 全部 | 全部 | 仅自己的 | 仅已审核主体的 |
| 检测报告 | 全部 | 全部 | 仅自己的 | 只读、仅合格 |
| 合格证 | 全部 | 全部 | 自己的可增改 | 只读、仅已签发 |
| 监督检查 | 全部、可写 | 全部、可写 | 仅自己批次关联的 | 仅已签发证上的 |
| 存证记录 | 全部 | 全部 | 仅自己的 | 只读、仅已签发证 |
| 产销供需 | 全部 | 全部 | 自己的 + 全部求购 | 自己的 + 全部供应 |
| 产销意向单 | 全部 | 全部 | 自己发起/收到的 | 自己发起/收到的 |
| 操作日志 | 全部 | 全部 | 仅自己的 | 仅自己的 |

**接口级操作授权**

| 操作 | 管理员 | 监管人员 | 种植主体 | 收购商 |
| --- | --- | --- | --- | --- |
| 主体审核 | ✓ | ✓ | ✗ | ✗ |
| 合格证签发 | ✓ | ✓ | ✗ | ✗ |
| 合格证作废 | ✓ | ✓ | ✗ | ✗ |
| 监督检查录入 | ✓ | ✓ | ✗ | ✗ |
| 整改复查 | ✓ | ✓ | ✗ | ✗ |
| 批次/报告存证 | ✓ | ✓ | ✓ | ✗ |
| 发布供需信息 | ✓ | ✓ | ✓ | ✓ |
| 发起对接意向 | ✓ | ✓ | ✓ | ✓ |

实现方式：`permissions.py` 提供接口级 `BasePermission`（按 action 细分）与
数据级 `RoleScopedQuerySetMixin`（统一 `get_queryset()` 过滤）。
前端按登录返回的 `permissions` 清单控制菜单与按钮，**后端仍做兜底拦截**（防绕过）。
`GET /api/role-matrix/` 直接返回上表，前端「权限说明」页渲染展示。

### 5. 合格证编号自动生成（19 位规则）★

编号对标农业农村部实际使用的 19 位格式，**由后端在签发时自动分配**，前端不可填：

```
区域码(6)  +  日期(8)  +  流水(5)   =  19 位
 511402        20260923      00001
 东坡区        签发当日      当日第 1 张
```

**三个设计要点（`certno.py`）**

1. **防占号** —— 编号只在「签发」环节分配。
   草稿（`draft`）与待审核（`pending`）阶段 `cert_no` 为空。
   否则大量草稿会吃掉流水号，导致正式号码跳跃、废号无法回收。
2. **防撞号** —— 应用层「查最大流水 + 1」在并发下有竞态（两个请求可能查到同一最大值）。
   这里靠**数据库唯一索引 + `IntegrityError` 捕获重试**兜底：
   即使算重了，INSERT 也会被唯一约束拦住，捕获后重新取号。
   比 `select_for_update` 更简单，且不依赖数据库是否支持行锁（SQLite 就不支持）。
3. **可读可反解** —— 从编号本身就能看出「哪里发的、哪天发的、当天第几张」，
   基层监管人员核对纸质证时无需查库。`parse_cert_no()` 可反解三段。

**合格证状态流转**

```
draft 草稿 ──提交──> pending 待审核 ──签发(分配编号)──> issued 已签发
   ↑                     │                                  │
   └──────驳回───────────┘                   检查不合格 ┌────┴────┐ 复查合格
                                                      ↓         ↑
                                              suspended 暂停使用 ─┘
                                                      │
                                                  作废 ↓
                                                 revoked 已作废
```

「暂停使用」与「作废」是两种不同语义：**暂停不是作废**（证还在，只是暂缓流通），
复查合格后可恢复为「已签发」；作废则永久失效并留作废存证。

`GET /api/certificate/peek-cert-no/` 预看下一个可用编号（**不占用**），
前端在签发对话框里先展示「即将分配 5104022026092300001」，让规则可视化。

### 6. 监管现场监督检查与执法闭环 ★

《农产品质量安全承诺达标合格证管理办法》中，农业农村部门的**现场监督检查**
与第三方检测机构出具的技术报告是**两条独立主线**。本系统把检查建模为独立实体
`InspectionRecord`，并做成完整执法闭环：

```
现场检查（类型/时间/地点/部门/执法证号）
   ↓
检查结论（合格 / 不合格 / 待复检）+ 百分制评分 + 问题描述
   ↓
整改要求 + 整改期限 + 复查负责人
   ↓
整改状态机：待整改 → 整改中 → 已整改 / 已逾期
   ↓
行政处罚：罚款金额 + 处罚事由 + 法律依据
   ↓
★ 检查结论上链存证（EvidenceRecord.BizType.INSPECTION）
```

**两个关键设计**

- **检查结论与合格证状态联动**：判定不合格 → 关联合格证自动置为「暂停使用」；
  复查合格 → 自动恢复为「已签发」（若还有其他未闭环的不合格检查则不恢复）。
  这让「监督检查」与「可信存证」两条主线串起来。
- **逾期动态判定**：逾期是**随时间自然发生**的状态，不可能靠人工点击维护。
  因此 `is_overdue` 是计算属性，列表/详情查询时按当前日期判定并落库（`refresh_overdue`，幂等）。
- **存证原文只含结论性事实**：参与哈希的字段为「检查结果、评分、罚款、法律依据、整改期限」等，
  **不含「整改说明」这类后续补充的描述** —— 否则整改一填写哈希就变，
  存证就失去了「固化当时判定」的意义。

**公众公示**：`GET /api/public/verify/<cert_no>/` 会附带监督检查信息，
消费者扫码即可看到「这张证是否被查出过问题、整改是否闭环」，无需登录。

### 7. 检测报告附件上传

`TestReport.file` 以 `FileField` 落地，检测报告可附上第三方检测机构出具的 PDF 扫描件：

- **两处上传入口**：新建报告时随表单一并提交；已有报告行内「上传」补传。
- **后端可信边界**：序列化器校验扩展名白名单（pdf / jpg / jpeg / png）与 10 MB 上限，
  不依赖前端限制（前端可绕过）。
- **`file_name` 回显原始文件名**：Django 存盘会加随机后缀（`report_rBcOEvO.pdf`），
  展示层剥离，用户看到的是自己上传的名字。
- **附件随记录清理**：`FileField` 只存路径，删记录不会自动删磁盘文件。
  删除报告时连带删除附件、替换附件时清理旧文件，避免长期积累孤儿文件。
- **nginx `^~ /media/`**：附件由 nginx 直接托管。必须用 `^~` 前缀 ——
  按扩展名的正则 location 优先级更高，会把 `.jpg` 附件交给以「前端 dist」为基准的
  `try_files`，导致「上传成功但打不开」。

### 8. 操作审计日志

- 链上存证锚定**数据摘要**，审计日志锚定**操作行为**，二者互补构成完整可信链。
- 覆盖动作：`登录 / 新增 / 修改 / 删除 / 审核 / 签发 / 作废 / 存证 / 验真 / 产销对接 / 监督检查 / 整改复查`。
- 记录操作人、角色、IP（兼容 nginx `X-Forwarded-For`）、目标对象、执行结果与详情；
  **登录失败同样留痕**，满足安全审计需要。
- 日志本身按角色隔离：种植主体/收购商只能查看自己的操作记录。
- `GET /api/log/` 支持按 `action / operator / target_type / keyword` 组合筛选。

### 9. 单元测试

`backend/apps/traceability/tests.py` 含 **83 个用例**，覆盖 10 个测试类：

| 测试类 | 用例数 | 关注点 |
| --- | --- | --- |
| `HashConsistencyTest` | 5 | 哈希稳定性；**8000 与 8000.00 必须同哈希**（数值格式化回归） |
| `TamperDetectionTest` | 5 | 双层篡改检测，含"连摘要一起改"场景 |
| `PermissionTest` | 11 | 越权拦截 403、数据隔离、看板角色差异、角色矩阵 |
| `AuditLogTest` | 7 | 各动作留痕、日志隔离 |
| `MatchingTest` | 11 | 匹配算法各因子、排序、意向单、双向发起 |
| `EndToEndFlowTest` | 1 | 建档 → 提交 → 签发（分配编号）→ 公众验真 |
| `CertNoRuleTest` | 12 | 编号规则、区域码容错、流水递增、**防占号**、重复签发不换号 |
| `InspectionTest` | 19 | 执法闭环、合格证暂停/恢复、逾期判定、存证原文稳定性、数据隔离 |
| `ReportFileUploadTest` | 8 | 附件上传/替换/删除、扩展名白名单、大小上限、权限、文件清理 |
| `InspectionBizNoTest` | 4 | 检查存证的链上编号独立性（同证多条检查互不覆盖） |

运行：`python manage.py test apps.traceability apps.accounts`


## 四、目录结构

```
源代码/
├── backend/                       # Django 后端
│   ├── config/                    # 项目配置（settings/urls/wsgi）
│   ├── contracts/
│   │   └── CertificateEvidence.sol  # ★ 存证智能合约
│   ├── apps/
│   │   ├── accounts/              # 用户与角色（4 类角色）
│   │   └── traceability/          # 核心业务
│   │       ├── models.py          # 主体/批次/报告/合格证/存证/供需/意向单/监督检查/审计日志
│   │       ├── services.py        # ★ 哈希存证与验真核心
│   │       ├── chain.py           # ★ 联盟链客户端（web3.py）
│   │       ├── certno.py          # ★ 合格证编号生成器（19 位，防占号 + 并发安全）
│   │       ├── permissions.py     # ★ 角色权限（接口级 + 数据级）
│   │       ├── audit.py           # ★ 操作审计日志写入
│   │       ├── matching.py        # ★ 产销智能匹配算法
│   │       ├── serializers.py
│   │       ├── views.py           # ViewSet + 公众验真 + 链状态 + 看板 + 角色矩阵
│   │       ├── tests.py           # ★ 83 个单元测试
│   │       ├── urls.py
│   │       ├── admin.py
│   │       └── management/commands/init_data.py  # 演示数据
│   ├── media/                     # 上传附件（检测报告 PDF 等，不入库）
│   ├── .env.example               # 链配置模板（复制为 .env）
│   ├── requirements.txt
│   └── manage.py
└── frontend/                      # Vue3 前端
    ├── src/
    │   ├── api/                   # Axios 接口封装
    │   ├── layout/                # 后台布局（菜单按角色过滤）
    │   ├── router/                # 路由与守卫（含角色守卫）
    │   ├── store/                 # Pinia（含 permissions）
    │   ├── utils/request.js       # 请求拦截器（JWT）
    │   └── views/                 # 13 页面：登录/验真/看板/主体/批次/报告/合格证/
    │                              #   监督检查/存证/产销/意向单/操作日志/权限说明
    ├── package.json
    └── vite.config.js
```

## 五、快速启动

### 后端

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env          # 按需填写链配置（不填则自动降级为模拟存证）
python manage.py migrate
python manage.py init_data    # 初始化演示数据
python manage.py runserver 0.0.0.0:8000
```

### 前端

```bash
cd frontend
npm install --registry=https://registry.npmmirror.com
npm run dev
```

- 前端：http://localhost:5173
- 后端：http://localhost:8000
- 公众验真：http://localhost:5173/#/verify

## 六、联盟链配置

链节点已部署于 `<your-server-ip>`（FISCO BCOS v3.17.1，单机 2 节点，chain0/group0，PBFT）。

在 `backend/.env` 中配置：

```ini
FISCO_WEB3_RPC=http://<your-server-ip>:8545    # 同机部署用 127.0.0.1
FISCO_CHAIN_ID=20200
FISCO_CONTRACT_ADDRESS=0x9D6aAc145B199bc406Cc4AFb8cE1571ADC619820
FISCO_PRIVATE_KEY=<存证账户私钥>              # 生产环境走环境变量注入
FISCO_ENABLE_CHAIN=true
```

- **链不可用时自动降级**：`chain.py` 不会抛异常中断业务，而是返回 `mode=mock` 走模拟存证，
  保证无链环境也能正常开发演示（`.env` 里设 `FISCO_ENABLE_CHAIN=false` 可强制模拟）。
- 部署细节见项目根目录 `FISCO_BCOS部署说明.md`。

### 存证合约接口

| 函数 | 说明 |
| --- | --- |
| `addEvidence(bizType, bizNo, dataHash)` | 写入存证（**同编号重复调用会 revert**，天然防重复存证） |
| `getEvidence(bizType, bizNo)` | 读取存证（摘要/时间/操作人/区块高度） |
| `verifyEvidence(bizType, bizNo, dataHash)` | 验真比对，返回 `(matched, exists)` |
| `totalEvidence()` / `version()` | 存证总数 / 合约版本 |

## 七、默认账号

| 账号 | 密码 | 角色 | 说明 |
| --- | --- | --- | --- |
| admin | admin123 | 系统管理员 | 全量可见 + 全部操作 |
| regulator | admin123 | 监管人员 | 全量可见 + 审核/签发/作废 |
| producer | admin123 | 种植主体 | 仅自己数据；可存证、发供需、发起对接 |
| producer2 | admin123 | 种植主体 | 第二个主体账号，**用于演示数据隔离** |
| buyer | admin123 | 收购商 | 仅已审核主体 + 已签发合格证；可发供需、发起对接 |

> 用 producer 与 producer2 分别登录，可直观看到"各自只看到自己的主体/批次/日志"；
> 用 buyer 登录则看到"仅已审核通过的主体 + 仅已签发的合格证"。

演示合格证编号：`CERT-2026-1001`、`CERT-2026-1002`
演示批次编号：`BATCH-2026-1002`、`BATCH-2026-2001`

> 新库执行 `init_data` 时，合格证编号按 19 位规则自动分配，形如
> `5114022026092300001`（东坡区 / 2026-09-23 / 当日第 1 张）。

## 八、核心数据主线

```
生产主体(Producer) ── 含 region_code（编号前 6 位行政区划码）
   └─ 生产批次(ProductBatch)
         ├─ 检测报告(TestReport)
         └─ 合格证(Certificate) ── 含 cert_hash（SHA-256）
               ├─ 存证记录(EvidenceRecord) ── tx_hash / block_height
               │     └─ 上链 ──> FISCO BCOS（CertificateEvidence 合约）
               ├─ 监督检查(InspectionRecord) ── 结论上链 + 整改闭环
               │     └─ 不合格 ──> 合格证置「暂停使用」（复查合格可恢复）
               └─ 产销供需(SupplyDemand) ── 关联证书，先验真再对接
                     └─ 产销意向单(MatchIntent) ── 匹配度评分 + 状态流转

旁路：操作日志(OperationLog) ── 记录所有角色的一切操作行为
```

## 九、接口一览

> 业务路由统一为**单数**形式；除公众验真外均需 `Authorization: Bearer <JWT>`。

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/auth/login/` | 公开 | 登录，返回 `access/refresh/user`（含 `permissions`） |
| POST | `/api/auth/refresh/` | 公开 | 刷新 JWT |
| GET | `/api/auth/profile/` | 登录 | 当前用户信息与权限清单 |
| GET | `/api/public/verify/<cert_no>/` | **公开** | 公众扫码验真（含链上核验 + 监督检查公示） |
| GET | `/api/dashboard/` | 登录 | 看板统计（**口径随角色变化**，含检查执法指标） |
| GET | `/api/role-matrix/` | 登录 | 角色-资源/操作矩阵 + 编号规则 + 状态流转 |
| CRUD | `/api/producer/` | 登录（范围隔离） | 生产主体 |
| POST | `/api/producer/<id>/audit/` | 监管 | 主体审核 |
| CRUD | `/api/batch/` | 登录（范围隔离） | 生产批次 |
| POST | `/api/batch/<id>/anchor/` | 监管/主体 | 批次存证上链 |
| CRUD | `/api/report/` | 登录（范围隔离） | 检测报告（支持 `multipart` 附件上传） |
| POST | `/api/report/<id>/anchor/` | 监管/主体 | 报告存证上链 |
| CRUD | `/api/certificate/` | 登录（范围隔离） | 合格证（建档为草稿，不占号） |
| GET | `/api/certificate/peek-cert-no/` | 登录 | ★ 预看下一个 19 位编号（不占用） |
| POST | `/api/certificate/<id>/submit/` | 监管/主体 | 提交审核（草稿 → 待审核，仍不占号） |
| POST | `/api/certificate/<id>/reject/` | 监管 | 驳回（退回草稿，不占号） |
| POST | `/api/certificate/<id>/issue/` | 监管 | ★ 签发（**分配编号** + 算哈希 + 上链） |
| POST | `/api/certificate/<id>/revoke/` | 监管 | 作废（生成 REVOKE 存证） |
| GET | `/api/certificate/<id>/qrcode/` | 登录 | 生成合格证二维码 |
| GET | `/api/certificate/<id>/inspection-summary/` | 登录 | ★ 该证的监督检查汇总 |
| CRUD | `/api/inspection/` | 登录（范围隔离） | ★ 监督检查记录（写操作仅监管） |
| POST | `/api/inspection/<id>/rectify/` | 监管 | ★ 整改复查（联动合格证恢复/维持暂停） |
| POST | `/api/inspection/<id>/anchor/` | 监管 | ★ 检查结论重新存证 |
| GET | `/api/evidence/` | 登录（范围隔离） | 存证记录（含批次/报告/合格证/检查） |
| GET | `/api/evidence/chain-status/` | 登录 | 联盟链节点状态 |
| GET | `/api/evidence/verify/<cert_no>/` | 登录 | 按证号做哈希核验（`?chain=1` 加链上核验） |
| CRUD | `/api/supply/` | 登录（范围隔离） | 产销供需 |
| GET | `/api/supply/<id>/recommend/` | 四角色 | ★ 智能匹配推荐（含因子明细） |
| POST | `/api/supply/<id>/intent/` | 四角色 | ★ 发起产销意向单 |
| POST | `/api/supply/<id>/match/` | 四角色 | 标记已对接 |
| GET | `/api/intent/` | 登录（范围隔离） | 意向单列表 |
| POST | `/api/intent/<id>/respond/` | 登录 | 接受/拒绝/完成意向 |
| GET | `/api/log/` | 登录（范围隔离） | 操作审计日志（支持多条件筛选） |

## 十、切换 MySQL

修改 `backend/config/settings.py` 中的 `DATABASES`（注释里有 MySQL 配置示例），
并安装 `PyMySQL`（已在 requirements.txt 中）。`config/__init__.py` 已做兼容处理。

## 十一、生产部署

> **本系统已实际上线**：http://<your-server-ip>:8090/
> 架构为 nginx（前端静态 + /api 反代）→ gunicorn → Django，链与后端同机。
> 完整部署说明见项目根目录 `系统部署说明.md`，一键部署脚本 `源代码/deploy.py`。
> （80/8080 端口因未备案被云厂商拦截层丢包，故对外使用 8090。）

```bash
# 一键部署到云服务器（在本目录执行）
python deploy.py all          # 或分步：upload / setup / migrate / service / nginx / start / check
```

> ⚠️ `deploy.py upload` 的 skip 集合必须包含 `db.sqlite3` 与 `.env`，
> 否则本地演示库会覆盖服务器生产数据。

手动部署方式：

```bash
# 后端
gunicorn config.wsgi:application -b 127.0.0.1:8000 -w 3

# 前端
npm run build   # 产物 dist/ 交给 nginx
```

Nginx 配置见 `frontend/README.md` 与 `系统部署说明.md`。

> ⚠️ 安全提示：生产环境请设 `DJANGO_DEBUG=false`、替换 `DJANGO_SECRET_KEY`，
> 并把后端与链节点**同机部署**走 `127.0.0.1`，避免 web3 RPC（8545）暴露公网。