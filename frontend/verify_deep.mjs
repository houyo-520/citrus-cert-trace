/**
 * 深度验收：确认 Dashboard 三个 ECharts 图表真的渲染出 canvas（而非只是没报错），
 * 并检查各页面关键 DOM 节点与数据行数。
 */
import { chromium } from 'file:///C:/Users/houyo/.workbuddy/binaries/node/workspace/node_modules/playwright/index.mjs'

const BASE = 'http://<your-server-ip>:8090'
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'

const b = await chromium.launch({ executablePath: EDGE, headless: true })
const ctx = await b.newContext({ viewport: { width: 1440, height: 1000 } })
const p = await ctx.newPage()

const errs = []
p.on('pageerror', (e) => errs.push('PAGEERROR: ' + e.message))
p.on('console', (m) => {
  if (m.type() === 'error' && !/favicon|Devtools|ResizeObserver/i.test(m.text())) {
    errs.push(m.text())
  }
})

// 登录
await p.goto(`${BASE}/#/login`, { waitUntil: 'networkidle' })
const ins = p.locator('input')
await ins.nth(0).fill('admin')
await ins.nth(1).fill('admin123')
await p.locator('button').filter({ hasText: /登\s*录/ }).first().click()
await p.waitForTimeout(2500)

// ---- Dashboard 图表 ----
await p.goto(`${BASE}/#/dashboard`, { waitUntil: 'networkidle' })
await p.waitForTimeout(2500)

const canvases = await p.evaluate(() => {
  return [...document.querySelectorAll('canvas')].map((c) => ({
    w: c.width, h: c.height,
    cssW: c.clientWidth, cssH: c.clientHeight
  }))
})
console.log('=== Dashboard canvas 元素 ===')
canvases.forEach((c, i) => console.log(`  #${i + 1} 内部 ${c.w}x${c.h}  显示 ${c.cssW}x${c.cssH}  ${c.w > 0 && c.h > 0 ? '✓' : '✗ 未渲染'}`))
console.log(`  合计 ${canvases.length} 个（期望 3：柱状/饼图/检查柱状）`)

// 关键卡片文本
// 注意：饼图图例（已签发/草稿/暂停使用）渲染在 canvas 内，innerText 读不到，
// 因此这里只断言 DOM 文案；canvas 渲染情况由上面的尺寸检查覆盖。
const dashText = await p.locator('body').innerText()
const checks = ['监督检查与执法闭环', '检查总数', '检查不合格', '罚款合计', '19 位']
console.log('\n=== Dashboard 关键文案 ===')
checks.forEach((k) => console.log(`  ${dashText.includes(k) ? '✓' : '✗'} ${k}`))

// ---- 各页面数据行数 ----
// 注意：列表页有两种布局 —— el-table 表格（检查/意向/日志/合格证）与
// 卡片列表（产销对接用 .supply-card）。选择器要分别适配，否则会误判为 0 行。
console.log('\n=== 列表页数据渲染 ===')
const LISTS = [
  { name: '监督检查', path: '/#/inspection', sel: '.el-table__body tbody tr' },
  { name: '产销意向单', path: '/#/intent', sel: '.el-table__body tbody tr' },
  { name: '操作日志', path: '/#/log', sel: '.el-table__body tbody tr' },
  { name: '合格证管理', path: '/#/certificate', sel: '.el-table__body tbody tr' },
  { name: '产销对接', path: '/#/supply', sel: '.supply-card' }
]
for (const it of LISTS) {
  await p.goto(BASE + it.path, { waitUntil: 'networkidle' })
  await p.waitForTimeout(1500)
  const n = await p.locator(it.sel).count()
  const empty = await p.locator('.el-empty').count()
  console.log(`  ${it.name.padEnd(8)} 条目=${n}${empty ? ' (含空状态)' : ''}  ${n > 0 ? '✓' : '✗'}`)
}

// ---- 权限说明：编号规则与状态流转 ----
await p.goto(`${BASE}/#/roles`, { waitUntil: 'networkidle' })
await p.waitForTimeout(1500)
const roleText = await p.locator('body').innerText()
console.log('\n=== 权限说明页 ===')
for (const k of ['编号规则', '19', '监督检查', '资源访问矩阵', '合格证状态流转', '已签发']) {
  console.log(`  ${roleText.includes(k) ? '✓' : '✗'} 含「${k}」`)
}

console.log(`\n总报错数: ${errs.length}`)
errs.slice(0, 10).forEach((e) => console.log('  ' + e.slice(0, 160)))

await b.close()
