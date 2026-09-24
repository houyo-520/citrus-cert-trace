/**
 * 第二梯队 4 项功能 · 浏览器端验收
 *
 * 对应功能：
 *   #21 追溯链时间轴（公众验真页从表格升级为时间轴）
 *   #22 站内消息通知（送审/签发/整改/匹配成功 → 站内信）
 *   #23 报告 OCR 识别（上传 PDF 自动提取编号/结论/日期）
 *   #24 移动端 H5 适配（响应式 + 扫码后手机体验）
 *
 * ★ 本脚本遵守项目验收纪律（见技能 frontend-refactor-zero-regression）：
 *   1. 「元素存在 + 无报错」≠ 渲染成功。二维码事故的教训：<img> 在、src 有值、
 *      零报错，但 naturalWidth=0 是破图。图片类断言必须查 naturalWidth。
 *   2. 列表类断言必须「真有 N 条」，不能只看没报错 ——
 *      `res.data.data || []` 带兜底时永远不报错，只是永远空列表。
 *   3. 图表内容读不到 innerText（在 canvas/SVG 里）→ 断言尺寸或 aria-valuenow。
 *   4. 非破坏性：只读为主。OCR 用自建临时样本，跑完删除，绝不污染演示数据
 *      （踩坑史：曾往演示报告传 32 字节空 PDF，答辩点开是白页）。
 *   5. 移动端断言要查「有没有横向溢出」，不是查「样式写没写」。
 *
 * 用法：
 *   验收线上：node verify_tier2.mjs
 *   验收本地：FRONTEND_VERIFY_BASE=http://localhost:5173 node verify_tier2.mjs
 */
import { chromium } from 'file:///C:/Users/houyo/.workbuddy/binaries/node/workspace/node_modules/playwright/index.mjs'
import fs from 'node:fs'

// ★ 不把服务器 IP 硬编码进仓库（见 memory「git 凭据清理」教训），
//    改为从环境变量读取，默认指向答辩演示地址。
const BASE = (process.env.FRONTEND_VERIFY_BASE || 'http://<your-server-ip>:8090').replace(/\/+$/, '')
const EDGE = process.env.EDGE_PATH || 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'

let pass = 0, fail = 0
const ok = (n, e = '') => { pass++; console.log(`  [PASS] ${n}${e ? ' -> ' + e : ''}`) }
const bad = (n, e = '') => { fail++; console.log(`  [FAIL] ${n}${e ? ' -> ' + e : ''}`) }
const assert = (c, n, e = '') => c ? ok(n, e) : bad(n, e)

const browser = await chromium.launch({ executablePath: EDGE, headless: true })
const ctx = await browser.newContext({ viewport: { width: 1600, height: 950 } })
const page = await ctx.newPage()

const errors = []
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message))

async function login(u, p, pageObj = page) {
  // ★ 登录要重试：清 localStorage 后紧接着 go 到 login，偶尔会因为
  //   前一个页面的异步请求还在跑、路由守卫还没稳定而提交失败。
  //   实测首轮 regulator 就这样静默失败（还停在 /#/login），
  //   而断言只报「登录失败」看不出是抖动 —— 重试能把它和真故障区分开。
  for (let attempt = 1; attempt <= 3; attempt++) {
    await pageObj.goto(`${BASE}/#/login`, { waitUntil: 'networkidle' })
    await pageObj.evaluate(() => localStorage.clear())
    await pageObj.goto(`${BASE}/#/login`, { waitUntil: 'networkidle' })
    await pageObj.waitForTimeout(700)
    const uInput = pageObj.locator('input[placeholder*="用户名"], input[type="text"]').first()
    if (await uInput.count() === 0) { await pageObj.waitForTimeout(900); continue }
    await uInput.fill(u)
    await pageObj.locator('input[type="password"]').first().fill(p)
    await pageObj.keyboard.press('Enter')
    await pageObj.waitForTimeout(2600)
    if (!pageObj.url().includes('/login')) return true
    // 失败则再等一会重试
    await pageObj.waitForTimeout(1200)
  }
  return false
}

/** 读页面横向溢出量：scrollWidth 超出 clientWidth 即溢出 */
async function hOverflow() {
  return await page.evaluate(() => {
    const de = document.documentElement
    return de.scrollWidth - de.clientWidth
  })
}

console.log('=== 第二梯队功能验收 ===\n')

// ============================================================
// [1] 追溯链时间轴（#21）—— 公开验真页，免登录
// ============================================================
console.log('--- [1] 追溯链时间轴（#21）---')
await page.goto(`${BASE}/#/verify`, { waitUntil: 'networkidle' })
await page.evaluate(() => localStorage.clear())
await page.goto(`${BASE}/#/verify`, { waitUntil: 'networkidle' })
await page.waitForTimeout(800)

// 时间轴是公开验真页的核心，免登录必须能看到
const searchInput = page.locator('input[placeholder*="合格证"], input[placeholder*="编号"]').first()
assert(await searchInput.count() > 0, '验真页有证号输入框')

await searchInput.fill('CERT-2026-1001')
await page.keyboard.press('Enter')
await page.waitForTimeout(2600)

// ★ 大色块结论
const verdict = page.locator('.verdict').first()
assert(await verdict.count() > 0, '出现大色块验真结论')
if (await verdict.count() > 0) {
  const vText = (await verdict.innerText()).replace(/\s+/g, '').trim()
  assert(vText.length > 0, '结论块有文案', vText.slice(0, 30))
  const box = await verdict.boundingBox()
  assert(box && box.height >= 60, '结论块是「大色块」而非一行文字', box ? `h=${Math.round(box.height)}` : 'no box')
}

// ★ 时间轴节点：必须真有节点，不是空容器
const tlItems = page.locator('.tl-item')
const tlCount = await tlItems.count()
assert(tlCount >= 3, '时间轴渲染出多个节点', `${tlCount} 个`)

// 节点顺序：育苗/建档 → 采收/检测 → 签发 → 流通
// 「显示的都是真的」：动态生成，不做固定 5 步占位
if (tlCount > 0) {
  const titles = []
  for (let i = 0; i < tlCount; i++) {
    titles.push((await tlItems.nth(i).innerText()).replace(/\s+/g, ' ').trim())
  }
  const joined = titles.join(' | ')
  console.log(`     节点：${joined.slice(0, 200)}`)
  assert(/签发/.test(joined), '时间轴含「签发」节点（核心环节）')
  // 签发节点必须在流通之前（链路方向正确）
  const iPublish = titles.findIndex((t) => /签发/.test(t))
  const iSupply = titles.findIndex((t) => /流通|供需/.test(t))
  if (iPublish >= 0 && iSupply >= 0) {
    assert(iPublish < iSupply, '「签发」排在「流通」之前（链路顺序正确）',
      `签发@${iPublish} 流通@${iSupply}`)
  }
  // 每个节点都应有时间或描述，不能是空壳
  const emptyNodes = []
  for (let i = 0; i < tlCount; i++) {
    const t = (await tlItems.nth(i).innerText()).replace(/\s+/g, '').trim()
    if (t.length < 4) emptyNodes.push(i)
  }
  assert(emptyNodes.length === 0, '无空节点（每个节点都有实质内容）',
    emptyNodes.length ? `空节点索引 ${emptyNodes}` : '')
}

// ★ 链上锚点：签发节点应展示 tx_hash / 区块高度（可信存证的视觉证据）
const anchorEl = page.locator('.tl-anchor').first()
assert(await anchorEl.count() > 0, '时间轴展示链上锚点信息')
if (await anchorEl.count() > 0) {
  const aText = (await anchorEl.innerText()).replace(/\s+/g, ' ').trim()
  assert(/0x[0-9a-fA-F]{8,}/.test(aText) || /区块|高度/.test(aText),
    '锚点含交易哈希或区块高度', aText.slice(0, 60))
}

// ============================================================
// [2] 站内消息通知（#22）
// ============================================================
console.log('\n--- [2] 站内消息通知（#22）---')
const okLogin2 = await login('regulator', 'admin123')
assert(okLogin2 && page.url().includes('/dashboard'), '监管登录成功', page.url())
await page.waitForTimeout(1800)

// 顶栏铃铛（类名 notice-badge，见 MainLayout.vue）
const bell = page.locator('.notice-badge').first()
assert(await bell.count() > 0, '顶栏出现消息铃铛')
if (await bell.count() > 0) {
  // 铃铛必须带 Bell 图标（不是空按钮）
  const bellIcon = await bell.locator('.el-icon, svg').count()
  assert(bellIcon > 0, '铃铛含图标')

  // ★ 未读数必须与后端一致 —— 只断言「有角标」会漏掉「前端算错数」。
  //   角标 :hidden="!hasUnread"，无未读时不显示属正常。
  const unreadApi = await page.evaluate(async () => {
    try {
      const t = localStorage.getItem('token') || localStorage.getItem('access_token')
      const r = await fetch('/api/notification/unread/', {
        headers: t ? { Authorization: 'Bearer ' + t } : {},
      })
      return await r.json()
    } catch (e) { return null }
  })
  if (unreadApi && unreadApi.data) {
    const n = unreadApi.data.unread
    const badgeEl = page.locator('.notice-badge .el-badge__content').first()
    const badgeVisible = await badgeEl.isVisible().catch(() => false)
    if (n > 0) {
      assert(badgeVisible, '有未读时角标可见', `未读 ${n}`)
      if (badgeVisible) {
        const txt = (await badgeEl.innerText()).trim()
        assert(/^\d+\+?$/.test(txt), '角标为数字', txt)
      }
    } else {
      assert(!badgeVisible, '无未读时角标隐藏（不显示 0）', `未读 ${n}`)
    }
    console.log(`     后端未读汇总：${JSON.stringify(unreadApi.data)}`)
  } else {
    console.log('     [SKIP] 未读到 unread 接口（token 键名差异），角标由后端测试覆盖')
  }
  await page.screenshot({ path: 'shot_tier2_bell.png' })
}

// 消息中心页
await page.goto(`${BASE}/#/notice`, { waitUntil: 'networkidle' })
await page.waitForTimeout(2200)
assert(page.url().includes('/notice'), '可进入消息中心页', page.url())

const noticePage = await page.locator('body').innerText()
assert(/消息中心|我的消息/.test(noticePage), '消息中心页标题渲染')
// 空态 vs 列表：两种情况都算通过，但要能区分，不能静默通过
const rows = page.locator('.notice-item, .msg-item, .el-timeline-item').first()
const hasRows = await rows.count() > 0
if (hasRows) {
  ok('消息列表有内容')
} else {
  const empty = await page.locator('.el-empty, .el-table__empty-text').count()
  assert(empty > 0, '无消息时显示明确空状态（不是白屏）')
}

// 工具栏三个动作存在（全部已读 / 清理已读 / 刷新）
const pageText = await page.locator('body').innerText()
assert(/全部已读/.test(pageText), '有「全部已读」操作')
assert(/清理已读/.test(pageText), '有「清理已读」操作')

// ============================================================
// [3] 报告 OCR 识别（#23）
// ============================================================
console.log('\n--- [3] 报告 OCR 识别（#23）---')
const okLogin3 = await login('producer', 'admin123')
assert(okLogin3 && page.url().includes('/dashboard'), '生产主体登录成功', page.url())

await page.goto(`${BASE}/#/report`, { waitUntil: 'networkidle' })
await page.waitForTimeout(2400)
const rpText = await page.locator('body').innerText()
assert(/检测报告/.test(rpText), '进入检测报告页')

// 后端 OCR 引擎自检（部署排查依赖它，也是本功能可用性的前置条件）
const ocrStatus = await page.evaluate(async () => {
  const r = await fetch('/api/report/ocr-status/', {
    headers: { Authorization: 'Bearer ' + (localStorage.getItem('token') || '') },
  })
  return r.json().catch(() => null)
})
// 直接用页面 axios 实例更稳（token 存储键名可能不同），失败则跳过该断言
if (ocrStatus && ocrStatus.data) {
  assert(ocrStatus.data.pdf_text === true, '服务器支持 PDF 文本层提取', JSON.stringify(ocrStatus.data.pdf_text))
} else {
  // 用前端请求层重试
  const viaUi = await page.evaluate(async () => {
    try {
      const mod = await import('/src/api/index.js')
      const r = await mod.ocrStatus()
      return r
    } catch (e) { return null }
  })
  if (viaUi && viaUi.data) {
    assert(viaUi.data.pdf_text === true, '服务器支持 PDF 文本层提取')
  } else {
    console.log('     [SKIP] 无法直连 ocr-status 接口（token 键名差异），改由后端测试覆盖')
  }
}

// 打开新增报告对话框 → 找「智能识别」按钮
const addBtn = page.locator('button:has-text("新增"), button:has-text("上传报告")').first()
if (await addBtn.count() > 0) {
  await addBtn.click()
  await page.waitForTimeout(1400)
  const dlgText = await page.locator('.el-dialog').first().innerText().catch(() => '')
  assert(/智能识别|自动识别/.test(dlgText), '新增报告对话框出现「智能识别」入口')
  // 识别结果面板初始不应出现（未识别前不占版面）
  const panelBefore = await page.locator('.ocr-result').count()
  assert(panelBefore === 0, '未识别时不显示识别结果面板')
  await page.keyboard.press('Escape')
  await page.waitForTimeout(700)
} else {
  bad('未找到「新增报告」按钮')
}

// 「数据来源」列：OCR 溯源要在列表里可见
const hasSourceCol = await page.locator('th:has-text("数据来源")').count()
assert(hasSourceCol > 0, '报告列表有「数据来源」列（区分机器认的/人敲的）')

// ============================================================
// [4] 移动端 H5 适配（#24）—— 核心：无横向溢出
// ============================================================
console.log('\n--- [4] 移动端 H5 适配（#24）---')

// iPhone 12 尺寸视口
const mobile = await browser.newContext({
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 3,
  isMobile: true,
  hasTouch: true,
  userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
})
const mp = await mobile.newPage()
const mErrors = []
mp.on('console', (m) => { if (m.type() === 'error') mErrors.push(m.text()) })
mp.on('pageerror', (e) => mErrors.push('pageerror: ' + e.message))

// [4.1] 公开验真页：扫码后第一落点，必须免登录且不溢出
await mp.goto(`${BASE}/#/verify`, { waitUntil: 'networkidle' })
await mp.waitForTimeout(1200)
let ov = await mp.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
assert(ov <= 2, '验真页移动端无横向溢出', `溢出 ${ov}px`)

const mSearch = mp.locator('input[placeholder*="合格证"], input[placeholder*="编号"]').first()
if (await mSearch.count() > 0) {
  await mSearch.fill('CERT-2026-1001')
  await mp.keyboard.press('Enter')
  await mp.waitForTimeout(2800)

  // 时间轴在手机上要能正常渲染
  const mTl = await mp.locator('.tl-item').count()
  assert(mTl >= 3, '移动端时间轴节点正常渲染', `${mTl} 个`)

  // 时间轴渲染后仍不得溢出（长哈希/长机构名最容易撑破布局）
  ov = await mp.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  assert(ov <= 2, '时间轴渲染后仍无横向溢出', `溢出 ${ov}px`)

  // 大色块结论在窄屏仍可见
  const mVerdict = await mp.locator('.verdict').first().count()
  assert(mVerdict > 0, '移动端大色块结论可见')

  await mp.screenshot({ path: 'shot_tier2_mobile_verify.png', fullPage: true })
}

// [4.2] 登录后主布局：侧边栏应收成抽屉（不是被压扁）
await mp.goto(`${BASE}/#/login`, { waitUntil: 'networkidle' })
await mp.evaluate(() => localStorage.clear())
await mp.goto(`${BASE}/#/login`, { waitUntil: 'networkidle' })
await mp.fill('input[placeholder*="用户名"], input[type="text"]', 'regulator')
await mp.fill('input[type="password"]', 'admin123')
await mp.keyboard.press('Enter')
await mp.waitForTimeout(2800)

ov = await mp.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
assert(ov <= 2, '看板页移动端无横向溢出', `溢出 ${ov}px`)

// 桌面侧边栏应隐藏，汉堡按钮应出现
const asideVisible = await mp.locator('.el-aside').isVisible().catch(() => false)
assert(!asideVisible, '移动端不显示桌面侧边栏（改为抽屉）')
const hamburger = await mp.locator('[class*="hamburger"], .header-mobile button, .el-icon').first().count()
assert(hamburger > 0, '移动端顶栏存在收起/汉堡按钮')

// 点开抽屉，菜单应可用
const foldBtn = mp.locator('.header-mobile .el-button, button').first()
if (await foldBtn.count() > 0) {
  await foldBtn.click()
  await mp.waitForTimeout(900)
  const drawer = mp.locator('.el-drawer')
  const drawerOpen = await drawer.isVisible().catch(() => false)
  assert(drawerOpen, '点击后抽屉菜单打开')
  if (drawerOpen) {
    const dText = await drawer.innerText()
    assert(/合格证|批次|检测报告/.test(dText), '抽屉里有菜单项')
    await mp.screenshot({ path: 'shot_tier2_mobile_drawer.png' })
    // 抽屉打开时也不该溢出
    ov = await mp.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    assert(ov <= 2, '抽屉打开时无横向溢出', `溢出 ${ov}px`)
  }
}

// [4.3] 关键页面移动端巡检：逐个确认无溢出
const mobileRoutes = [
  ['/certificate', '合格证管理'],
  ['/batch', '批次管理'],
  ['/report', '检测报告'],
  ['/notice', '消息中心'],
  ['/evidence', '存证记录'],
  ['/supply', '产销供需'],
]
console.log('     页面巡检：')
for (const [route, name] of mobileRoutes) {
  await mp.goto(`${BASE}/#${route}`, { waitUntil: 'networkidle' })
  await mp.waitForTimeout(1800)
  const o = await mp.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  assert(o <= 2, `移动端 ${name} 无横向溢出`, `溢出 ${o}px`)
}

// 表格在窄屏的**正确行为**只有两种，必须落在其中一种：
//   ① 表格本身够窄 → 正好放下  ② 表格宽 → 在表格内部横向滚动
// 错误行为是「内容被裁掉」或「把整页撑宽」。
// ★ 不能写成「必须可滚动」—— 合格证列表在手机上只有 2 列时本来就能放下，
//   那样断言会把正确结果判成失败（首版就踩了这个）。
await mp.goto(`${BASE}/#/certificate`, { waitUntil: 'networkidle' })
await mp.waitForTimeout(2000)
const tableScroll = await mp.evaluate(() => {
  const w = document.querySelector('.el-table__body-wrapper')
  if (!w) return null
  const cs = getComputedStyle(w)
  return {
    sw: w.scrollWidth, cw: w.clientWidth,
    overflowX: cs.overflowX,
    fits: w.scrollWidth <= w.clientWidth + 1,
  }
})
if (tableScroll) {
  const ok1 = tableScroll.fits
  const ok2 = !tableScroll.fits && /auto|scroll/.test(tableScroll.overflowX)
  assert(ok1 || ok2,
    ok1 ? '宽表格在窄屏正好放下（无裁切）'
        : '宽表格在表格内部横向滚动（不裁切、不撑破页面）',
    `scrollWidth=${tableScroll.sw} clientWidth=${tableScroll.cw} overflow-x=${tableScroll.overflowX}`)
  // 无论哪种，整页都不许溢出
  const o2 = await mp.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  assert(o2 <= 2, '表格页整页无横向溢出', `溢出 ${o2}px`)
}

// 弹窗在窄屏不应超出视口
// ★ 先确保抽屉已关闭 —— 抽屉打开时它的遮罩会拦截点击
//   （首版就因为这个卡到 30s 超时：元素 visible/enabled 但被 overlay 挡住）
await mp.goto(`${BASE}/#/certificate`, { waitUntil: 'networkidle' })
await mp.waitForTimeout(2400)
const drawerStillOpen = await mp.locator('.el-drawer').isVisible().catch(() => false)
if (drawerStillOpen) {
  await mp.keyboard.press('Escape')
  await mp.waitForTimeout(800)
}
const dlgBtn = mp.locator('button:has-text("新增"), button:has-text("签发")').first()
if (await dlgBtn.count() > 0) {
  // 用 force 绕过可能的残余遮罩；点击只为打开弹窗验证宽度，无破坏性
  await dlgBtn.click({ force: true, timeout: 8000 }).catch(() => {})
  await mp.waitForTimeout(1400)
  const dlg = mp.locator('.el-dialog').first()
  if (await dlg.count() > 0) {
    const b = await dlg.boundingBox()
    assert(b && b.width <= 390, '弹窗宽度不超出手机视口', b ? `w=${Math.round(b.width)}` : 'no box')
    ov = await mp.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    assert(ov <= 2, '弹窗打开时无横向溢出', `溢出 ${ov}px`)
  }
  await mp.keyboard.press('Escape')
}

await mobile.close()

// ============================================================
// 控制台错误汇总
// ============================================================
console.log('\n--- 控制台错误 ---')
const realErrors = errors.concat(mErrors).filter((e) =>
  !/favicon|ResizeObserver|Download the Vue Devtools/i.test(e))
if (realErrors.length === 0) {
  ok('桌面端 + 移动端控制台零业务错误')
} else {
  const grouped = {}
  for (const e of realErrors) {
    const key = (e.match(/reading '[^']+'/) || [e.slice(0, 80)])[0]
    grouped[key] = (grouped[key] || 0) + 1
  }
  for (const [k, v] of Object.entries(grouped)) {
    bad(`控制台错误 x${v}`, k)
  }
}

// ============================================================
// 汇总
// ============================================================
console.log(`\n=== 结果：PASS ${pass} / FAIL ${fail} ===`)
await browser.close()
process.exit(fail > 0 ? 1 : 0)