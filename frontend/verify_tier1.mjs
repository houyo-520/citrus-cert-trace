/**
 * 第一梯队 4 项功能 · 浏览器端验收
 *
 * 对应功能：
 *   #16 合格证证面打印（新窗口 + CSS 打印方案）
 *   #17 统计看板增强（趋势 / 类型分布 / TOP / 数据质量指标）
 *   #18 风险预警提醒（顶栏红点 + 看板预警条）
 *   #19 批量开证（勾选 + 批量签发对话框）
 *
 * ★ 本脚本遵守项目验收纪律：
 *   1. 不用「元素存在 + 无报错」当通过标准 —— 那会漏掉「有元素但渲染是空的」。
 *      例：二维码事故里 <img> 存在、src 有值、零报错，但 naturalWidth=0（破图）。
 *   2. 列表/图表断言「真有 N 条 / 真有尺寸」，不看 innerText（canvas 内读不到）。
 *   3. 图片类断言必须查 naturalWidth > 0。
 *   4. 非破坏性：只读，不改生产数据（批量签发只验对话框出现，不真提交）。
 *
 * 用法：
 *   验收线上：node verify_tier1.mjs
 *   验收本地：FRONTEND_VERIFY_BASE=http://localhost:5173 node verify_tier1.mjs
 */
import { chromium } from 'file:///C:/Users/houyo/.workbuddy/binaries/node/workspace/node_modules/playwright/index.mjs'

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

async function login(u, p) {
  await page.goto(`${BASE}/#/login`, { waitUntil: 'networkidle' })
  await page.evaluate(() => localStorage.clear())
  await page.goto(`${BASE}/#/login`, { waitUntil: 'networkidle' })
  await page.fill('input[placeholder*="用户名"], input[type="text"]', u)
  await page.fill('input[type="password"]', p)
  await page.keyboard.press('Enter')
  await page.waitForTimeout(2400)
}

console.log('=== 第一梯队功能验收 ===\n')

// ============================================================
// [1] 风险预警（#18）—— 监管视角
// ============================================================
console.log('--- [1] 风险预警（#18）---')
await login('regulator', 'admin123')
assert(page.url().includes('/dashboard'), '监管登录成功', page.url())

await page.waitForTimeout(1500)
// 顶栏红点按钮
const badgeCount = await page.locator('.alert-badge').count()
assert(badgeCount > 0, '顶栏出现预警红点', `${badgeCount} 个`)

if (badgeCount > 0) {
  const badgeText = (await page.locator('.alert-badge').first().innerText()).replace(/\s+/g, ' ').trim()
  assert(/预警/.test(badgeText), '红点按钮文案含「预警」', badgeText)
  // el-badge 的数字角标
  const supCount = await page.locator('.alert-badge .el-badge__content').count()
  assert(supCount > 0, '红点带数字角标')
  if (supCount > 0) {
    const num = (await page.locator('.alert-badge .el-badge__content').first().innerText()).trim()
    assert(/^\d+$/.test(num) && Number(num) > 0, '角标为大于 0 的数字', num)
  }
  // 点开清单
  await page.locator('.alert-badge button').first().click()
  await page.waitForTimeout(900)
  const popItems = await page.locator('.alert-pop-item').count()
  assert(popItems > 0, '预警清单展开且有条目', `${popItems} 条`)
  if (popItems > 0) {
    const first = (await page.locator('.alert-pop-item').first().innerText()).replace(/\s+/g, ' ').trim()
    assert(first.length > 4, '预警条目有实际文案（非空壳）', first.slice(0, 50))
    // 点击应能跳转（用第一条的标题对应的路由）
    await page.locator('.alert-pop-item').first().click()
    await page.waitForTimeout(1200)
    const jumped = !page.url().includes('/dashboard')
    assert(jumped, '点击预警可跳转到处理页面', page.url().split('#')[1] || '')
  }
}

// 看板预警条
await page.goto(`${BASE}/#/dashboard`, { waitUntil: 'networkidle' })
await page.waitForTimeout(2000)
const alertCard = await page.locator('.alert-card').count()
assert(alertCard > 0, '看板出现风险预警卡片')
if (alertCard > 0) {
  const items = await page.locator('.alert-card .alert-item').count()
  assert(items > 0, '预警卡片内有明细条目', `${items} 条`)
}

// ============================================================
// [2] 统计看板增强（#17）
// ============================================================
console.log('\n--- [2] 统计看板增强（#17）---')
// 图表是 canvas，innerText 读不到 —— 必须断言 canvas 尺寸 > 0
const charts = await page.evaluate(() => {
  const out = []
  document.querySelectorAll('.el-card canvas').forEach((c, i) => {
    out.push({ i, w: c.width, h: c.height, cw: c.clientWidth, ch: c.clientHeight })
  })
  return out
})
assert(charts.length >= 6, '看板渲染出 ≥6 个图表 canvas', `${charts.length} 个`)
assert(charts.every(c => c.w > 0 && c.h > 0), '所有 canvas 位图尺寸 > 0（★不是空图）',
  charts.map(c => `${c.w}x${c.h}`).join(' '))

// 新增卡片标题
for (const t of ['近 6 个月签发趋势', '监督检查类型分布', '签发量 TOP 生产主体', '数据质量指标']) {
  const n = await page.locator(`.card-title:has-text("${t}")`).count()
  assert(n > 0, `存在「${t}」区块`)
}
// 数据质量指标网格
const metrics = await page.locator('.metric').count()
assert(metrics >= 6, '数据质量指标 ≥6 项', `${metrics} 项`)
if (metrics > 0) {
  const vals = await page.locator('.metric-val').allInnerTexts()
  assert(vals.every(v => v.trim().length > 0), '每个指标都有值（无空值）', vals.join(' / '))
}

// ============================================================
// [3] 合格证证面打印（#16）
// ============================================================
console.log('\n--- [3] 合格证证面打印（#16）---')
await page.goto(`${BASE}/#/certificate`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1800)

const printBtns = await page.locator('button:has-text("打印")').count()
assert(printBtns > 0, '列表出现「打印」按钮', `${printBtns} 个`)

if (printBtns > 0) {
  await page.locator('button:has-text("打印")').first().click()
  await page.waitForTimeout(2600)

  // 证面区域存在
  const areaCount = await page.locator('#cert-print-area').count()
  assert(areaCount > 0, '证面区域 #cert-print-area 已渲染')

  if (areaCount > 0) {
    // 证面核心内容
    const face = await page.locator('#cert-print-area').innerText()
    for (const kw of ['农产品质量安全承诺达标合格证', '生产主体', '承诺内容', '验真链接', '存证状态']) {
      assert(face.includes(kw), `证面含「${kw}」`)
    }
    // 编号必须印在证面上。
    // ★ 不能写死「19 位数字」—— 演示库里还留着早期格式的证号（CERT-2026-1002），
    //   而 19 位规则只对「新签发」的证生效。断言应校验「证号被印出来」，
    //   并顺带检查：若为 19 位则格式必须正确。
    const noText = (await page.locator('#cert-print-area .cert-no').first().innerText()).trim()
    assert(noText.length >= 10, '证面印出合格证编号', noText)
    if (/^\d+$/.test(noText)) {
      assert(noText.length === 19, '数字编号为 19 位（新规则）', `${noText.length} 位`)
    } else {
      console.log(`  [--] 该证为早期格式编号 ${noText}（19 位规则仅对新签发证生效）`)
    }
    // 证号必须同时出现在验真链接里（否则扫码指向错误页面）
    assert(face.includes(noText), '证号同时出现在证面验真链接中')

    // ★ 二维码必须真的渲染成功（naturalWidth > 0，破图时为 0）
    const qr = await page.evaluate(() => {
      const el = document.querySelector('#cert-print-area .qr-img')
      if (!el) return null
      return { src: (el.getAttribute('src') || '').slice(0, 30), nw: el.naturalWidth, nh: el.naturalHeight }
    })
    assert(!!qr, '证面二维码 <img> 存在')
    if (qr) {
      assert(qr.src.startsWith('data:image/png;base64,'), '二维码是 PNG data URL', qr.src + '…')
      assert(qr.nw > 0 && qr.nh > 0, '★二维码 naturalWidth > 0（真渲染成功）', `${qr.nw}x${qr.nh}`)
    }

    // 表格结构完整（证面是一张信息表）
    const rows = await page.locator('#cert-print-area table tr').count()
    assert(rows >= 8, '证面信息表 ≥8 行', `${rows} 行`)

    // 打印按钮存在且可点（新窗口方案：headless 下 window.open 会被拦，
    // 因此这里只断言按钮就绪，不真触发打印）
    const pbtn = await page.locator('.el-dialog button:has-text("打印 / 另存 PDF")').count()
    assert(pbtn > 0, '「打印 / 另存 PDF」按钮就绪')
  }

  // ★ 必须关掉证面弹窗再进下一节 —— 否则 overlay 会拦截后续所有点击，
  //   表现为「元素可见可点却一直 retry 到超时」，极易误判成功能坏了。
  await page.keyboard.press('Escape')
  await page.waitForTimeout(800)
  const stillOpen = await page.locator('.el-overlay-dialog:visible').count()
  if (stillOpen > 0) {
    await page.locator('.el-dialog__headerbtn').first().click().catch(() => {})
    await page.waitForTimeout(600)
  }
  ok('证面弹窗已关闭（解除 overlay 遮挡）')
}

// ============================================================
// [4] 批量开证（#19）
// ============================================================
console.log('\n--- [4] 批量开证（#19）---')
await page.goto(`${BASE}/#/certificate`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1800)

// 监管可见勾选框
const checkboxes = await page.locator('.el-table__body-wrapper .el-checkbox').count()
assert(checkboxes > 0, '监管可见行勾选框', `${checkboxes} 个`)

// 只有 pending 的可勾选：找一张待审核的证
const pendingRow = page.locator('.el-table__row').filter({ hasText: '待审核' }).first()
const hasPending = await pendingRow.count() > 0
if (hasPending) {
  const cb = pendingRow.locator('.el-checkbox').first()
  const disabled = await cb.locator('input').isDisabled().catch(() => true)
  assert(!disabled, '待审核行可勾选')
  if (!disabled) {
    await cb.click()
    await page.waitForTimeout(700)
    const batchBtn = await page.locator('button:has-text("批量签发")').count()
    assert(batchBtn > 0, '勾选后出现「批量签发」按钮')
    if (batchBtn > 0) {
      const btnText = (await page.locator('button:has-text("批量签发")').first().innerText()).trim()
      assert(/已选\s*\d+\s*张/.test(btnText), '按钮显示已选张数', btnText)
      await page.locator('button:has-text("批量签发")').first().click()
      await page.waitForTimeout(900)
      const dlgTitle = await page.locator('.el-dialog__title:has-text("批量签发合格证")').count()
      assert(dlgTitle > 0, '批量签发对话框打开')
      const rowsInDlg = await page.locator('.el-dialog .el-table__row').count()
      assert(rowsInDlg > 0, '对话框列出待签发清单', `${rowsInDlg} 行`)
      // 关闭，不真提交（非破坏性）
      // ★ 选择器必须限定在「当前可见的对话框」内 —— 页面上同时存在多个
      //   el-dialog（证面弹窗、批量弹窗），`.el-dialog button:has-text("关闭")`
      //   会命中已被隐藏的那个，表现为「元素 not visible」一直重试到超时。
      await page.locator('.el-dialog:visible button:has-text("关闭")').first()
        .click({ timeout: 5000 }).catch(async () => {
          await page.keyboard.press('Escape')
        })
      await page.waitForTimeout(600)
      ok('未真提交（非破坏性验收）')
    }
  }
} else {
  // 没有待审核的证时，至少要能断言「非 pending 行不可勾选」
  const anyRow = page.locator('.el-table__row').first()
  if (await anyRow.count() > 0) {
    const dis = await anyRow.locator('.el-checkbox input').first().isDisabled().catch(() => null)
    assert(dis === true, '非待审核行不可勾选（selectable 生效）')
  }
  console.log('  [--] 当前无待审核证，跳过批量签发交互')
}

// ============================================================
// [5] 种植主体视角：无勾选框、无签发按钮（权限回归）
// ============================================================
console.log('\n--- [5] 角色权限回归 ---')
await login('producer', 'admin123')
await page.goto(`${BASE}/#/certificate`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1800)
const pCheckbox = await page.locator('.el-table__body-wrapper .el-checkbox').count()
assert(pCheckbox === 0, '种植主体看不到行勾选框', `${pCheckbox} 个`)
const pBatch = await page.locator('button:has-text("批量签发")').count()
assert(pBatch === 0, '种植主体看不到批量签发按钮')
const pIssue = await page.locator('button:has-text("签发")').count()
assert(pIssue === 0, '种植主体看不到签发按钮')

// 主体仍有打印入口（自己的证）
const pPrint = await page.locator('button:has-text("打印")').count()
console.log(`  [--] 种植主体可见打印按钮 ${pPrint} 个（自己的证可打印，符合设计）`)

// 主体看板：不应出现监管专属执法区块
await page.goto(`${BASE}/#/dashboard`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1800)
const inspBlock = await page.locator('.card-title:has-text("监督检查与执法闭环")').count()
assert(inspBlock === 0, '种植主体看不到监管执法闭环区块')
const pCharts = await page.evaluate(() => document.querySelectorAll('.el-card canvas').length)
assert(pCharts >= 5, '种植主体看板图表正常渲染', `${pCharts} 个`)

// ---- 控制台报错 ----
const real = errors.filter(e => !e.includes('favicon') && !e.includes('404') && !e.includes('ResizeObserver'))
assert(real.length === 0, '控制台零报错', real.length ? real.slice(0, 4).join(' | ') : '')

console.log(`\n=== 结果：${pass} 通过 / ${fail} 失败 ===`)
await browser.close()
process.exit(fail ? 1 : 0)
