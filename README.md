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
- 平台记录对接状态，打通"产 — 证 — 销"闭环。

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
│   │       ├── models.py          # 主体/批次/报告/合格证/存证/供需
│   │       ├── services.py        # ★ 哈希存证与验真核心
│   │       ├── chain.py           # ★ 联盟链客户端（web3.py）
│   │       ├── serializers.py
│   │       ├── views.py           # ViewSet + 公众验真 + 链状态 + 看板
│   │       ├── urls.py
│   │       ├── admin.py
│   │       └── management/commands/init_data.py  # 演示数据
│   ├── .env.example               # 链配置模板（复制为 .env）
│   ├── requirements.txt
│   └── manage.py
└── frontend/                      # Vue3 前端
    ├── src/
    │   ├── api/                   # Axios 接口封装
    │   ├── layout/                # 后台布局
    │   ├── router/                # 路由与守卫
    │   ├── store/                 # Pinia
    │   ├── utils/request.js       # 请求拦截器（JWT）
    │   └── views/                 # 页面（登录/验真/看板/主体/批次/报告/合格证/存证/产销）
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

| 账号 | 密码 | 角色 |
| --- | --- | --- |
| admin | admin123 | 系统管理员 |
| regulator | admin123 | 监管人员 |
| producer | admin123 | 种植主体 |
| buyer | admin123 | 收购商 |

演示合格证编号：`CERT-2026-1001`、`CERT-2026-1002`

## 八、核心数据主线

```
生产主体(Producer)
   └─ 生产批次(ProductBatch)
         ├─ 检测报告(TestReport)
         └─ 合格证(Certificate) ── 含 cert_hash（SHA-256）
               ├─ 存证记录(EvidenceRecord) ── tx_hash / block_height
               │     └─ 上链 ──> FISCO BCOS（CertificateEvidence 合约）
               └─ 产销供需(SupplyDemand) ── 关联证书，先验真再对接
```

## 九、切换 MySQL

修改 `backend/config/settings.py` 中的 `DATABASES`（注释里有 MySQL 配置示例），
并安装 `PyMySQL`（已在 requirements.txt 中）。`config/__init__.py` 已做兼容处理。

## 十、生产部署

> **本系统已实际上线**：http://<your-server-ip>/
> 架构为 nginx（前端静态 + /api 反代）→ gunicorn → Django，链与后端同机。
> 完整部署说明见项目根目录 `系统部署说明.md`，一键部署脚本 `源代码/deploy.py`。

```bash
# 一键部署到云服务器（在本目录执行）
python deploy.py all          # 或分步：upload / setup / migrate / service / nginx / start / check
```

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