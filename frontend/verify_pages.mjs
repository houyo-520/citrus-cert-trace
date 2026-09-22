/**
 * 真机验收：用本机 Edge 打开线上站点，逐个访问报错页面，
 * 断言「控制台零报错 + 页面关键内容已渲染」。
 * 用法：node verify_pages.mjs
 */
// ESM 不读 NODE_PATH，必须用绝对路径导入（playwright 装在托管 node workspace 下）
import { chromium } from 'file:///C:/Users/houyo/.workbuddy/binaries/node/workspace/node_modules/playwright/index.mjs'

const BASE = 'http://<your-server-ip>:8090'
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'

const PAGES = [
  { path: '/#/dashboard', name: '数据看板', expect: ['各模块数据统计', '合格证状态分布', '监督检查与执法闭环'] },
  { path: '/#/inspection', name: '监督检查', expect: ['监督检查', '检查总数', '录入检查'] },
  { path: '/#/intent', name: '产销意向单', expect: ['产销意向单'] },
  { path: '/#/log', name: '操作日志', expect: ['操作日志'] },
  { path: '/#/roles', name: '权限说明', expect: ['权限说明', '生产主体'] },
  { path: '/#/certificate', name: '合格证管理', expect: ['合格证'] },
  { path: '/#/supply', name: '产销对接', expect: ['产销对接'] }
]

const IGNORE = [
  /favicon/i,
  /Download the Vue Devtools/i,
  /ResizeObserver loop/i
]

const browser = await chromium.launch({ executablePath: EDGE, headless: true })
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await ctx.newPage()

const allErrors = []
page.on('console', (m) => {
  if (m.type() === 'error' && !IGNORE.some((r) => r.test(m.text()))) {
    allErrors.push({ page: currentName, text: m.text() })
  }
})
page.on('pageerror', (e) => {
  if (!IGNORE.some((r) => r.test(e.message))) {
    allErrors.push({ page: currentName, text: 'PAGEERROR: ' + e.message })
  }
})

let currentName = 'login'

// ---- 登录 ----
console.log('== 登录 ==')
await page.goto(`${BASE}/#/login`, { waitUntil: 'networkidle' })
await page.waitForSelector('input', { timeout: 15000 })
const inputs = page.locator('input')
await inputs.nth(0).fill('admin')
await inputs.nth(1).fill('admin123')
await page.locator('button').filter({ hasText: /登\s*录|登录/ }).first().click()
await page.waitForTimeout(2500)
const afterLogin = await page.evaluate(() => localStorage.getItem('token'))
console.log('  token 已写入:', !!afterLogin)
if (!afterLogin) {
  console.log('  !! 登录失败，无法继续')
  await browser.close()
  process.exit(1)
}

// ---- 逐页验证 ----
const results = []
for (const p of PAGES) {
  currentName = p.name
  const before = allErrors.length
  await page.goto(BASE + p.path, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1800)

  const body = await page.locator('body').innerText().catch(() => '')
  const found = p.expect.filter((k) => body.includes(k))
  const errs = allErrors.slice(before)

  results.push({
    name: p.name,
    path: p.path,
    rendered: found.length,
    expected: p.expect.length,
    missing: p.expect.filter((k) => !body.includes(k)),
    errors: errs
  })

  await page.screenshot({ path: `shot_${p.name}.png`, fullPage: true }).catch(() => {})
}

// ---- 报告 ----
console.log('\n===== 页面验收结果 =====')
for (const r of results) {
  const ok = r.rendered === r.expected && r.errors.length === 0
  console.log(`${ok ? '✓' : '✗'} ${r.name.padEnd(12)} 渲染 ${r.rendered}/${r.expected}` +
    (r.missing.length ? `  缺:${r.missing.join(',')}` : '') +
    (r.errors.length ? `  报错${r.errors.length}条` : ''))
}
console.log(`\n总报错数: ${allErrors.length}`)
for (const e of allErrors.slice(0, 15)) {
  console.log(`  [${e.page}] ${e.text.slice(0, 180)}`)
}

await browser.close()
process.exit(allErrors.length === 0 && results.every((r) => r.rendered === r.expected) ? 0 : 2)
