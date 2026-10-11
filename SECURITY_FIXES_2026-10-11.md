# 安全审计修复报告

> 审计时间：2026-10-11　　修复范围：柑橘合格证追溯系统
> 项目：`源代码/backend/`（Django 5.2 + DRF）、`源代码/deploy.py`、合约 `CertificateEvidence.sol`

## 总览

| # | 问题 | 审计等级 | 状态 |
| --- | --- | --- | --- |
| 1 | 供需单跨用户修改/删除 | 高 | ✅ 已修复 |
| 2 | 意向单回应无核验、状态可任意流转 | 高 | ✅ 已修复 |
| 3 | 签发只查报告存在、不查结论 | 高 | ✅ 已修复 |
| 4 | 暂停证可重新送审/签发（状态绕过） | 高 | ✅ 已修复 |
| 5 | 验真未覆盖全部关键关联数据 | 高 | ✅ 已修复 |
| 6 | 哈希原文 `\|` 拼接可致歧义碰撞 | 中 | ✅ 已修复 |
| 7 | 合约写入接口无权限控制、重复写清作废态 | 中高 | ✅ 已修复（v3.0） |
| 8 | 演示账号口令写在代码/文档 | 中高 | ✅ 已修复 |
| 9 | 部署脚本自动信任未知 SSH 主机密钥 | 中 | ✅ 已修复 |
| 10 | 前端 JWT 存 localStorage | 建议评估 | ⚠️ 缓解（CSP + 时效收紧） |
| 11 | OCR 同步处理可被拖垮 | 建议评估 | ✅ 已加固（总时限） |

**回归测试**：229 → **257**（新增 28 个安全回归测试），全部通过。

---

## 高危问题

### #1 供需单越权写（`views.py`）

**漏洞**：`SupplyDemandViewSet.scope_queryset` 为支持「浏览对方公开单」，返回范围
包含对方发布的单；而通用 `update/partial_update/destroy` **直接复用这份范围**，
`CanTrade` 只校验角色不校验归属 → 种植主体可改/删买家求购单，反之亦然。

**修复**：`get_queryset` 中按 action 收紧 —— 写动作只保留 `publisher=当前用户`。
- 浏览/匹配/发起对接的授权**不变**（业务需要）。
- 取不到对象返回 404，不泄露「该 id 存在」。
- 管理员/监管不受限。

**测试**：`SupplyDemandWriteScopeTest`（5 例，含「本人仍可改」「浏览不受影响」回归）

### #2 意向单回应无鉴权（`views.py`）

**漏洞**：`respond` 仅 `get_object()` 取范围内对象，未校验回应人身份：
- 发起人 `initiator` 也在范围内 → **可替对方接受/拒绝自己的意向**；
- 状态可任意跳转（已拒绝可改回 pending）。

另发现 **`partial_update` 未拦**（原只拦了 `update`/PUT）→ PATCH 可绕过全部校验。

**修复**：
- 新增 `rules.check_intent_respond()`：状态机 + 身份校验
  - `pending → accepted/rejected`：**仅供需单发布方**
  - `accepted → done`：发起方或发布方确认
  - `rejected` / `done` 为终态
- 补 `partial_update` 拦截（405）。

**测试**：`MatchIntentRespondAuthTest`（5 例）

### #3 签发不校验报告结论（`views.py`）

**漏洞**：单张 `issue` 与 `batch_issue` 均只判断 `report_id` 是否存在；
**不合格报告也能签发**。且未校验报告是否属于同一批次。

**修复**：抽出 `rules.check_cert_issue()`，**单张与批量共用**：
1. 状态可签发（draft / pending / issued 幂等重签）
2. 必须有关联报告
3. **报告结论必须为「合格」**
4. **报告与本证必须同批次**
5. 批次必须有生产主体

**测试**：`CertIssueReportConclusionTest`（5 例，含批量入口）

### #4 暂停证状态绕过（`views.py`）

**漏洞**：`submit` 显式允许 `SUSPENDED → PENDING`；`issue` 又未限制来源状态
→ **被暂停的证可重新变为已签发**，与「整改后原证作废（终态）」的设计冲突。

**修复**：状态机集中到 `rules.py`（唯一来源）：

```
draft     → pending
pending   → issued / draft / revoked
issued    → suspended / revoked
suspended → revoked          ← 只能走整改→作废，不可回退
revoked   → （终态）
```

`submit` 明确拒绝 suspended（并给出整改流程指引）；`issue` 只允许 `draft/pending/issued`。

**测试**：`CertStateMachineTest` + `CertIssueReportConclusionTest`（暂停证送审/签发各一例）

### #5 验真未覆盖关联数据（`services.py` / `views.py`）

**漏洞**：证书摘要只含「批次编号、报告编号」这类**引用值**；批次产地/品种、
报告结论/日期/机构被改，**证书摘要不变、验真仍显可信**，而公众页展示值已变。

**修复**（双重保障）：
1. **关联字段纳入摘要**：`build_certificate_payload` 现编码批次的
   `batch_no/product_name/variety/origin/weight_kg` 与报告的
   `report_no/conclusion/test_org/test_date` → 任一被改，摘要必变。
2. **独立核验定位**：新增 `_verify_related_evidence()`，对批次/报告**各自的存证记录**
   做独立比对，返回 `related[]` 明细，公开验真接口透出 `related_evidence` 字段。
   - 已单独存证且不符 → 整证判不可信；
   - **未单独存证 → `checked=false`，不参与判定**（正确性已由摘要间接保障，避免误伤）。

**测试**：`VerifyRelatedEvidenceTest`（4 例）+ `test_batch_tamper_makes_cert_untrusted`

---

## 中高危问题

### #6 哈希拼接歧义（`services.py`）

**漏洞**：`"|".join([...])` 未转义字段 → `("张三|李四","柑橘")` 与
`("张三","李四|柑橘")` 拼出**相同原文**，语义已变而摘要不变。

**修复**：改为**长度前缀编码** `<字符数>:<内容>`（netstring），边界由长度判定，
字段含任何字符（含 `|`、换行）都不歧义。批次/报告/合格证三处统一。
末尾追加格式版本号（`CERT/BATCH/REPORT_PAYLOAD_VERSION = "v2"`）便于识别历史数据。

**测试**：`test_payload_no_delimiter_ambiguity`

### #7 合约写入权限与覆盖（`CertificateEvidence.sol` v3.0）

**漏洞**：
- `addEvidence` / `revokeEvidence` 均为 **public**，任何地址可写；
- **重复 `addEvidence` 会覆盖**并把 `revoked` 重置为 `false`
  → 已作废的证可被「重复写入复活」；
- 根 README 称「重复调用会 revert」，与实现不符。

**修复**：
- 新增 `owner` + `writers` 白名单，`onlyWriter` 修饰器；
  `setWriter()` / `transferOwnership()` 仅 owner 可调。
- `addEvidence` **已存在则 revert**；
- 新增 `updateEvidence()` 供合法重存证，**保留 revoked/revokedAt/revokeReason**，
  版本号 +1；新增 `EvidenceUpdated` 事件。
- 后端 `chain.send_evidence()` 改为**先查存在性**决定 add / update。
- README 同步更正。合约版本 → `v3.0`。

**部署注意**：需重新部署合约并更新 `FISCO_CONTRACT_ADDRESS`，随后跑 `rehash_all`。

### #8 演示账号口令（`init_data.py` / README）

**漏洞**：五类角色账号口令 `admin123` 硬编码在命令与两处 README；
若生产误跑且未改密 → 直接可登录。

**修复**：
- **生产闸门**：`DEBUG=False` 时 `init_data` 默认 `CommandError` 拒绝，
  需显式 `--force`（并警告）。
- **口令不落源码**：优先读环境变量 `DEMO_PASSWORD`；未设置则
  `secrets.token_urlsafe(12)` 生成随机口令并**只打印一次**；
  若模型有 `must_change_password` 则置位强制首改。
- 两处 README 删去明文口令，改为说明获取方式。

**测试**：`InitDataSafetyTest`（2 例，含源码扫描 `admin123` 防回归）

---

## 中危问题

### #9 部署脚本信任未知主机密钥（`deploy.py`）

**漏洞**：`AutoAddPolicy()` 无条件接受任何主机密钥 → 中间人可冒充服务器，
把 `.env`（含 DB 口令 / JWT 密钥 / 存证私钥）连同代码全部收走。

**修复**：新增 `_host_key_policy()`，三种放行方式，**无默认信任**：
1. `CITRUS_SSH_HOST_FINGERPRINT`（`SHA256:...`）—— 指纹白名单，不符即拒；
2. `known_hosts` 严格校验（`RejectPolicy`）—— 未登记即拒；
3. `CITRUS_SSH_TRUST_NEW=1` —— 显式临时放行（打印风险提示）。

拒绝时输出中文指引（含 `ssh-keyscan` 命令）。

**实测**：① 未知主机 → 拒绝；② 正确指纹 → 放行；③ 错误指纹 → 拒绝并报出实际指纹。

---

## 建议评估项

### #10 前端 JWT 存 localStorage

**现状**：token 存 localStorage，XSS 或依赖投毒时可被读取外传。

**决策**：不改架构（改 HttpOnly Cookie 需重写整条认证链，风险大于收益）。
采用**低成本高收益缓解**：
- 新增 `ContentSecurityPolicyMiddleware`，支持 `DJANGO_CSP=1` 开启 CSP：
  `script-src 'self'`（注入脚本不执行）、`connect-src 'self'`（难以外传 token）、
  `object-src 'none'`、`base-uri 'self'`。
- 默认关闭（本地 Vite 开发需 eval/内联，不受影响）。
- JWT 时效此前已加固：access 2h、refresh 7d、轮换 + 黑名单。

**生产启用**：`.env` 加 `DJANGO_CSP=1`（**上线前需实测前端无 inline script 依赖**）。

### #11 OCR 同步处理拖垮 worker

**现状**：已有单图/单页上限（40MP、3 页、dpi 200），但多页累加仍可能长时间占住
gunicorn worker。

**修复**：新增进程级**整单总时限** `OCR_TOTAL_BUDGET_SECONDS`（默认 90s）：
- 每页识别前检查截止时间，超时**立即停止后续页**；
- 已识别部分照常返回，并在 `warnings` 明确告知「超时未处理完，请手工补录」；
- 超预算时**不进入**重 OCR 分支（保护 worker）。

**测试**：`OcrBudgetTest`（3 例）

---

## 上线前检查清单

- [ ] 重新部署合约（v3.0）并更新 `FISCO_CONTRACT_ADDRESS`，跑 `manage.py rehash_all`
- [ ] 生产 `.env` 设 `DJANGO_CSP=1` 并实测前端正常
- [ ] 确认服务器 `known_hosts` 已登记（或在部署环境设指纹变量）
- [ ] 修改所有演示账号口令（若线上存在）
- [ ] 因哈希原文格式升级（`v2`），线上需**重新存证**（`rehash_all`）后再对外验真

---

# 续审（第二轮审计）修复

> 时间：2026-10-11　　在第一轮 11 项修复基础上，又处理 8 个新问题 + 2 项补充。
> 测试：257 → **277**（新增 20 个回归用例），全部通过。

## 清单

| 指纹 | 问题 | 等级 | 状态 |
| --- | --- | --- | --- |
| TRACE-MATCH-OWNER | `match` 动作可改他人公开单状态 | 高/10 | ✅ |
| TRACE-PAYLOAD-COVERAGE | 摘要未覆盖时间轴展示的农事记录/残留/重金属 | 高/9 | ✅ |
| CHAIN-UPDATE-ABI | 内联 ABI 缺 `updateEvidence` → 存证静默降级 | 中高/10 | ✅ |
| CSP-STATIC-SPA | CSP 只加在 API，SPA 静态页（nginx）没有 | 中/10 | ✅ |
| OCR-DEADLINE-NONINTERRUPT | 总时限不能中断正在跑的 OCR | 中/9 | ✅ |
| CONFIG-BOOL-DRIFT | 布尔环境变量判断不一致（`FISCO_ENABLE_CHAIN=1` 会关链）| 中/10 | ✅ |
| REHASH-SALT-LOG | `rehash_all` 把盐值原文打进日志 | 中/9 | ✅ |
| CHAIN-OWNER-TRANSFER | 合约转移所有权未撤销旧 owner 权限 | 中/9 | ✅ |
| （补充）状态机未接入业务入口 | `can_transition_cert` 定义了但没被调用 | — | ✅ |
| （补充）缺少强制改密机制 | User 无 `must_change_password` 字段 | — | ✅ |

## 关键修复说明

### TRACE-MATCH-OWNER（高）
`SupplyDemandViewSet.match` 用 `self.get_object()`（公开读取范围），任意可交易角色
可把**别人的**供需单标记为 matched —— 对方的单会从「待对接」候选消失，等于被下架。
**修复**：仅发布者本人（或管理员/监管）可操作，否则 403 并提示走意向单流程。

### TRACE-PAYLOAD-COVERAGE（高）
时间轴/公众页展示的字段未进摘要，改了仍显「可信」。**补齐**：
- 批次 payload：`variety` / `origin` / `plant_area_mu` / `farming_record`
- 报告 payload：`test_org` / `pesticide_residue` / `heavy_metal`
- 证书 payload 同步纳入上述关联字段（改任一 → 证书摘要必变）

### CHAIN-UPDATE-ABI（中高，上轮自己引入）
上轮把 `send_evidence` 改为「已存在走 `updateEvidence`」，但**没同步补 ABI**
→ web3 找不到函数名、抛异常被吞 → **存证静默降级为 mock 且无任何报错**。
**修复**：补齐 v3 ABI（`updateEvidence` / `setWriter` / `transferOwnership` /
`writers` / `owner` / `getEvidenceWithVersion`），并加 web3 可解析性测试。

### CSP-STATIC-SPA（中）
CSP 加在 Django 响应上，但 SPA 的 `index.html` 是 **nginx 直接返回**，
真正需要保护的前端页面反而没有 CSP。**修复**：`deploy.py` 的 nginx 配置中
在 server 级 + `/index.html` + 附件 location（location 内 `add_header`
会覆盖继承，故三处都要写）加 CSP 头。

### OCR-DEADLINE-NONINTERRUPT（中）
总时限只在页与页之间检查，`pytesseract.image_to_string` 是**阻塞调用无超时**。
**修复**：
- tesseract 命令行路线原生支持 `timeout=`，优先走它；
- 无命令行时把 pytesseract 放到**子进程**执行，父进程按剩余预算 kill；
- 每页把**剩余预算**传下去，单页超时即放弃该页（不重试，避免再卡一次）。

### CONFIG-BOOL-DRIFT（中）
`DJANGO_DEBUG` / `DJANGO_HTTPS` / `FISCO_ENABLE_CHAIN` 用 `== "true"`，
与 `1/true/yes/on` 兼容处理不一致 —— 例 `FISCO_ENABLE_CHAIN=1` 被判 False，
**真实上链被意外关闭而运维以为开着**。
**修复**：抽出 `env_bool()` 统一解析，全量替换。

### REHASH-SALT-LOG（中）
`rehash_all` 打印 `存证盐值：<原文>`，在 CI/部署日志会永久留存（等于泄露加盐密钥）。
**修复**：只显示「已配置」+ 盐值的**不可逆指纹**（sha256 前 16 位）+ 长度。

### CHAIN-OWNER-TRANSFER（中）
`transferOwnership` 只给新 owner 授权，**旧 owner 仍在白名单** —— 转移后仍能写。
**修复**：转移时撤销旧 owner 的 writer 状态（如需保留请显式 `setWriter`）。

### 补充：状态机接入业务入口
上轮把状态机放进 `rules.py`，但业务视图仍各自硬编码判断（规则不是唯一来源）。
**修复**：新增 `rules.check_cert_transition()`，接入 **6 个**状态写入点
（submit / reject / issue / revoke / 检查暂停联动 / 复查作废）。
⚠️ 过程中发现设计不一致：状态机表缺 `draft → issued`，而
`CERT_ISSUABLE_FROM` 允许草稿直接签发 → 已补上并加「两表一致性」测试。

### 补充：强制改密落地
`User` 无 `must_change_password` 字段 → 上轮的 `hasattr` 分支永不生效。
**修复**：
- 模型加字段 + 迁移 `0005_user_must_change_password`
- 新增 `POST /api/auth/change-password/`（校验旧密码、长度 ≥8、不得同旧）
- `init_data` 对**已存在**的演示账号也轮换口令并置标记

---

# 第三轮审计修复

> 时间：2026-10-11　　4 项新问题（含 3 项高/中高，直接影响「可信存证」语义）。
> 测试：277 → **296**，全部通过。

| 指纹 | 问题 | 等级 | 状态 |
| --- | --- | --- | --- |
| 上链失败仍显示签发成功 | mock 被表述为「已完成可信存证」| 高/10 | ✅ |
| 状态机检查存在并发窗口 | 检查-写入非原子，可能覆盖并发状态 | 高/9 | ✅ |
| 强制改密未真正强制 | 登录照样发 JWT，可跳过改密 | 中/10 | ✅ |
| OCR 总时限未覆盖 PDF 解析渲染 | 解析/渲染仍在 worker 内同步跑 | 中/9 | ✅ |

## 修复说明

### ① 上链失败不得显示签发成功（高）
**问题**：链不可用时 `chain.send_evidence` 返回 mock，但业务照常落本地存证、
证书标 `is_anchored=True`，签发接口一律回 `"签发成功并完成可信存证"`；
公众验真在本地哈希一致时也显示「可信」——**「可信存证」这个核心卖点被 mock 冒充**。

**修复**：
- 新增 `services.describe_anchor(record)` —— 统一把存证记录转成
  `{anchored, mode, label, detail}`；**只有 `chain_status == "success"` 才算 anchored**。
- 签发接口按真实结果分三种措辞：
  - 真上链 → 「签发成功并完成链上存证」
  - 降级 mock → 「签发成功（本地存证，链上未核验）」
  - **开了链却写失败** → 「签发成功，但链上存证失败…请检查链节点后重新签发」
  且响应 `data.anchor` 透出真实状态。
- 验真措辞拆分：本地一致 → 「**本地摘要一致**」（不再说「数据可信」）；
  仅当**链上核验也通过**才升级为「数据可信：本地摘要一致，且已通过链上核验」。
- 公开验真接口新增 `anchored` / `anchor_mode` / `anchor_label` / `anchor_detail`。
- 批量签发的 message 由「签发并存证成功」改为「签发成功」+ 逐张 `anchor` 明细。

### ② 状态机检查并发窗口（高）
**问题**：`issue` 与检查暂停都是「读对象 → 检查状态 → 保存」，三步无原子性。
并发时签发可能依据旧状态通过校验，**覆盖另一请求刚写入的 `suspended`**。

**修复**：新增 `rules.atomic_cert_transition(cert, to, extra)` —— **带原状态条件的
UPDATE**（乐观并发控制）：
```
UPDATE certificate SET status=<to>,... WHERE id=? AND status=<from>
```
- `rowcount==1` 成功；`rowcount==0` 说明状态已被并发修改 → 放弃（签发返回 **409**）。
- ⚠️ **不用 `select_for_update`**：项目默认 SQLite，SQLite 会**忽略** FOR UPDATE；
  条件更新跨数据库可靠。
- 已接入：`issue` / `batch_issue` / 检查暂停联动。

### ③ 强制改密未真正强制（中）
**问题**：加了字段与改密接口，但登录照样发 JWT，用户可直接用业务接口跳过改密；
改密后旧 refresh 也仍然有效。

**修复**：
- 新增 `ForcePasswordChangeMiddleware`：待改密用户访问**非白名单**接口一律
  403 + `must_change_password: true`。
  白名单：改密 / 注销 / 刷新 / profile / 登录 / 公开接口 / 静态资源。
  （自行解析 Authorization 头，因 DRF 认证在视图层、中间件早于它。）
- 改密成功后**拉黑该用户全部未过期 refresh**（`OutstandingToken` 遍历 + blacklist），
  旧会话立即失效。
- 响应返回 `revoked_sessions` 数量。

### ④ OCR 总时限未覆盖 PDF 解析/渲染（中）
**问题**：上轮只让 tesseract 可中断；PDF 文本提取与渲染仍在 worker 内同步执行，
预算只在提取**之后**检查 → 畸形 PDF 仍能卡住 worker。

**修复**：新增 `_run_pdf_subprocess()`，把 `extract_text_from_pdf` 与
`render_pdf_to_images` 都放进**子进程**并受剩余预算约束。
- 结果经**临时文件**回传（base64 PNG 可达数 MB，走 stdout 会被截断 → 实测踩坑）。
- 两个函数均新增 `budget` 参数，由 `extract_from_file` 传剩余秒数。
- 子进程异常/超时一律降级为「无文本层」，不抛异常、不挂住。

## 本轮踩坑记录
1. `ocr.py` 缺 `import json` —— 子进程结果解析抛 `NameError`，被 `except Exception`
   吞成 `bad-output`，表现为**渲染静默返回 0 页**（功能等于不存在）。
   教训：宽 except 会吞掉「自己忘 import」这类低级错误，调试时要打印真实异常。
2. 子进程回传大体积结果**不要走 stdout**（管道容量/缓冲），改用临时文件。
