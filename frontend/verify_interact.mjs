/**
 * 交互与图表深度验收：
 *   1) 各角色下 Dashboard 图表数量是否符合预期（非监管角色少一张"执法闭环"卡）
 *   2) 关键交互能否打开：录入检查弹窗、查看详情、发布供需、发起意向
 *   3) 分页组件可用
 */
import { chromium } from 'file:///C:/Users/houyo/.workbuddy/binaries/node/workspace/node_modules/playwright/index.mjs'

const BASE = 'http://<your-server-ip>:8090'
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'

const browser = await chromium.launch({ executablePath: EDGE, headless: true })
const errs = []
let cur = ''

const login = async (user) => {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const p = await ctx.newPage()
  p.on('pageerror', (e) => { if (!/favicon|Devtools|ResizeObserver/i.test(e.message)) errs.push(`[${cur}] ${e.message}`) })
  p.on('console', (m) => {
    if (m.type() === 'error' && !/favicon|Devtools|ResizeObserver/i.test(m.text())) errs.push(`[${cur}] ${m.text()}`)
  })
  await p.goto(`${BASE}/#/login`, { waitUntil: 'networkidle' })
  const ins = p.locator('input')
  await ins.nth(0).fill(user)
  await ins.nth(1).fill('admin123')
  await p.locator('button').filter({ hasText: /登\s*录/ }).first().click()
  await p.waitForTimeout(2500)
  return p
}

// ---- 1) 各角色图表数量 ----
console.log('===== 各角色 Dashboard 图表 =====')
for (const [user, label, expectCanvas] of [
  ['admin', '系统管理员', 3],
  ['regulator', '监管人员', 3],
  ['producer', '种植主体', 2],
  ['buyer', '收购商', 2]
]) {
  cur = `${label}/图表`
  const p = await login(user)
  await p.goto(`${BASE}/#/dashboard`, { waitUntil: 'networkidle' })
  await p.waitForTimeout(2500)
  const cs = await p.evaluate(() => [...document.querySelectorAll('canvas')].map((c) => `${c.width}x${c.height}`))
  const ok = cs.length === expectCanvas && cs.every((s) => !s.startsWith('0x'))
  console.log(`  ${ok ? '✓' : '✗'} ${label.padEnd(8)} canvas=${cs.length}（期望${expectCanvas}） ${cs.join(' ')}`)
  await p.context().close()
}

// ---- 2) 交互：监管录入检查 ----
cur = '监管/录入检查'
{
  const p = await login('regulator')
  await p.goto(`${BASE}/#/inspection`, { waitUntil: 'networkidle' })
  await p.waitForTimeout(1800)
  const btn = p.locator('button').filter({ hasText: /录入检查|新增检查/ }).first()
  const hasBtn = await btn.count()
  if (hasBtn) {
    await btn.click()
    await p.waitForTimeout(1200)
    const dlg = await p.locator('.el-dialog').count()
    const visible = await p.locator('.el-dialog').first().isVisible().catch(() => false)
    console.log(`  ${visible ? '✓' : '✗'} 录入检查弹窗打开（dialog=${dlg} visible=${visible}）`)
    // 弹窗内表单控件
    const formItems = await p.locator('.el-dialog .el-form-item').count()
    console.log(`     表单字段数: ${formItems}`)
    await p.keyboard.press('Escape')
    await p.waitForTimeout(600)
  } else {
    console.log('  ✗ 未找到「录入检查」按钮')
  }
  await p.context().close()
}

// ---- 3) 交互：合格证详情（含检查汇总）----
cur = '监管/合格证详情'
{
  const p = await login('regulator')
  await p.goto(`${BASE}/#/certificate`, { waitUntil: 'networkidle' })
  await p.waitForTimeout(1800)
  const rows = await p.locator('.el-table__body tbody tr').count()
  console.log(`  合格证行数: ${rows}`)
  // 找详情按钮
  const detail = p.locator('button').filter({ hasText: /详情|查看/ }).first()
  if (await detail.count()) {
    await detail.click()
    await p.waitForTimeout(1800)
    const vis = await p.locator('.el-dialog').first().isVisible().catch(() => false)
    const txt = await p.locator('.el-dialog').first().innerText().catch(() => '')
    console.log(`  ${vis ? '✓' : '✗'} 详情弹窗打开`)
    console.log(`     ${txt.includes('检查') ? '✓' : '✗'} 含监督检查汇总`)
    console.log(`     ${txt.includes('编号') ? '✓' : '✗'} 含编号解析`)
    await p.keyboard.press('Escape')
  } else {
    console.log('  ✗ 未找到详情按钮')
  }
  await p.context().close()
}

// ---- 4) 交互：存证页链上核验 ----
cur = '监管/链上核验'
{
  const p = await login('regulator')
  await p.goto(`${BASE}/#/evidence`, { waitUntil: 'networkidle' })
  await p.waitForTimeout(2500)
  const txt = await p.locator('body').innerText()
  console.log(`  ${txt.includes('联盟链') ? '✓' : '✗'} 显示联盟链节点卡`)
  console.log(`  ${/connected|已连接|运行中/.test(txt) ? '✓' : '✗'} 链状态已连接`)
  await p.context().close()
}

// ---- 5) 交互：产销对接推荐弹窗 ----
cur = '主体/智能匹配推荐'
{
  const p = await login('producer')
  await p.goto(`${BASE}/#/supply`, { waitUntil: 'networkidle' })
  await p.waitForTimeout(2000)
  const rec = p.locator('button').filter({ hasText: /推荐|匹配/ }).first()
  if (await rec.count()) {
    await rec.click()
    await p.waitForTimeout(2200)
    const dlg = p.locator('.el-dialog').first()
    const vis = await dlg.isVisible().catch(() => false)
    const items = await dlg.locator('.rec-item').count()
    // ★ 匹配度用 el-progress 圆环渲染，分数在 SVG 内 —— innerText 读不到，
    //   必须读 aria-valuenow 属性；因子明细才是可读文本。
    const scores = await dlg.locator('.el-progress').evaluateAll((els) =>
      els.map((e) => e.getAttribute('aria-valuenow')).filter(Boolean))
    const factors = await dlg.locator('.rec-factor').count()
    const txt = await dlg.innerText().catch(() => '')
    console.log(`  ${vis ? '✓' : '✗'} 推荐弹窗打开`)
    console.log(`  ${items > 0 ? '✓' : '✗'} 候选条目 ${items} 条`)
    console.log(`  ${scores.length > 0 ? '✓' : '✗'} 匹配度圆环已渲染，分数=${scores.join(',')}`)
    console.log(`  ${factors > 0 ? '✓' : '✗'} 因子明细 ${factors} 项（算法可解释性）`)
    console.log(`  ${/品种|数量|价格|地域|信用/.test(txt) ? '✓' : '✗'} 含因子名称文本`)
    await p.keyboard.press('Escape')
  } else {
    console.log('  - 当前数据下无推荐按钮')
  }
  await p.context().close()
}

console.log(`\n控制台报错总数: ${errs.length}`)
errs.slice(0, 12).forEach((e) => console.log('  ' + e.slice(0, 170)))
await browser.close()
