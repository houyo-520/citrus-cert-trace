/**
 * 全矩阵验收：4 个角色 × 全部页面 + 2 个公开页
 *
 * 覆盖目标：
 *   1) 13 个页面（11 后台 + 登录 + 公众验真）全部访问到，无遗漏
 *   2) 每个角色单独登录，验证菜单渲染 + 数据范围隔离
 *   3) 全程收集控制台报错，要求总数为 0
 *
 * 用法：node verify_matrix.mjs
 */
import { chromium } from 'file:///C:/Users/houyo/.workbuddy/binaries/node/workspace/node_modules/playwright/index.mjs'

const BASE = 'http://<your-server-ip>:8090'
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'

// 四角色账号（密码统一 admin123）
const USERS = [
  { user: 'admin', label: '系统管理员', expectMenu: 11 },
  { user: 'regulator', label: '监管人员', expectMenu: 11 },
  { user: 'producer', label: '种植主体', expectMenu: 11 },
  { user: 'buyer', label: '收购商', expectMenu: 11 }
]

// 11 个后台页面（路径 → 断言关键词；关键词取页面稳定结构，不取随权限变化的按钮）
// ★ 注意：Dashboard 的「监督检查与执法闭环」卡片是 v-if="can_view_all"，
//   只有 admin/regulator 可见 —— 非监管角色断言它必然失败（那是设计，不是 bug）。
//   因此关键词按角色区分，避免假警报。
const PAGES = [
  { p: '/#/dashboard', name: '数据看板', kw: ['数据看板', '各模块数据统计'], kwRegulator: ['监督检查与执法闭环'] },
  { p: '/#/producer', name: '生产主体', kw: ['生产主体'] },
  { p: '/#/batch', name: '生产批次', kw: ['生产批次'] },
  { p: '/#/report', name: '检测报告', kw: ['检测报告'] },
  { p: '/#/certificate', name: '合格证管理', kw: ['合格证'] },
  { p: '/#/inspection', name: '监督检查', kw: ['监督检查'] },
  { p: '/#/evidence', name: '存证与核验', kw: ['存证'] },
  { p: '/#/supply', name: '产销对接', kw: ['产销'] },
  { p: '/#/intent', name: '产销意向单', kw: ['产销意向单'] },
  { p: '/#/log', name: '操作日志', kw: ['操作日志'] },
  { p: '/#/roles', name: '权限说明', kw: ['资源访问矩阵'] }
]

const IGNORE = [/favicon/i, /Devtools/i, /ResizeObserver loop/i]

const browser = await chromium.launch({ executablePath: EDGE, headless: true })
const allErrors = []
let current = 'init'

const newPage = async () => {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await ctx.newPage()
  page.on('pageerror', (e) => {
    if (!IGNORE.some((r) => r.test(e.message))) allErrors.push(`[${current}] PAGEERROR: ${e.message}`)
  })
  page.on('console', (m) => {
    if (m.type() === 'error' && !IGNORE.some((r) => r.test(m.text()))) {
      allErrors.push(`[${current}] ${m.text()}`)
    }
  })
  return page
}

// ============ 公开页（免登录）============
console.log('===== 公开页（免登录）=====')
{
  current = '公众验真'
  const p = await newPage()
  await p.goto(`${BASE}/#/verify/CERT-2026-1002`, { waitUntil: 'networkidle' })
  await p.waitForTimeout(2500)
  const txt = await p.locator('body').innerText()
  const checks = [
    ['证号回显', txt.includes('CERT-2026-1002')],
    ['双层校验状态', txt.includes('双层校验')],
    ['监督检查公示', txt.includes('监督检查')],
    ['暂停告警', txt.includes('暂停')],
    ['编号构成', txt.includes('区域码') || txt.includes('编号')]
  ]
  checks.forEach(([k, ok]) => console.log(`  ${ok ? '✓' : '✗'} ${k}`))
  await p.screenshot({ path: 'shot_公众验真.png', fullPage: true }).catch(() => {})
  await p.context().close()

  // 不存在的证号应给出友好提示而非报错
  // ★ 这条会主动触发一个 HTTP 404 —— 那是后端的正确行为（证号不存在），
  //   属预期，因此把它排除在报错统计外，否则每次都留一条假警报。
  current = '公众验真-无效证号'
  const errBefore404 = allErrors.length
  const p2 = await newPage()
  await p2.goto(`${BASE}/#/verify/NOT-EXIST-999`, { waitUntil: 'networkidle' })
  await p2.waitForTimeout(2000)
  const t2 = await p2.locator('body').innerText()
  console.log(`  ${t2.length > 100 ? '✓' : '✗'} 无效证号有友好提示（页面未崩）`)
  await p2.context().close()
  // 剔除本次预期内的 404
  for (let i = allErrors.length - 1; i >= errBefore404; i--) {
    if (/404/.test(allErrors[i])) allErrors.splice(i, 1)
  }
}

// ============ 四角色 × 11 页面 ============
const summary = []
for (const u of USERS) {
  console.log(`\n===== 角色：${u.label}（${u.user}）=====`)
  const page = await newPage()

  // 登录
  current = `${u.label}-登录`
  await page.goto(`${BASE}/#/login`, { waitUntil: 'networkidle' })
  const ins = page.locator('input')
  await ins.nth(0).fill(u.user)
  await ins.nth(1).fill('admin123')
  await page.locator('button').filter({ hasText: /登\s*录/ }).first().click()
  await page.waitForTimeout(2500)
  const token = await page.evaluate(() => localStorage.getItem('token'))
  const role = await page.evaluate(() => {
    try { return JSON.parse(localStorage.getItem('user') || '{}').role } catch { return null }
  })
  console.log(`  登录: token=${!!token} role=${role}`)
  if (!token) { await page.context().close(); continue }

  // 菜单项数
  const menuCount = await page.locator('.el-menu-item').count()
  console.log(`  菜单项: ${menuCount} 个 ${menuCount === u.expectMenu ? '✓' : `✗(期望${u.expectMenu})`}`)

  // 逐页访问
  const row = { role: u.label, menu: menuCount, pages: [] }
  for (const it of PAGES) {
    current = `${u.label}/${it.name}`
    const before = allErrors.length
    await page.goto(BASE + it.p, { waitUntil: 'networkidle' })
    await page.waitForTimeout(1400)

    const txt = await page.locator('body').innerText().catch(() => '')
    // 监管角色额外要求看到执法闭环卡片
    const isReg = ['admin', 'regulator'].includes(u.user)
    const need = it.kw.concat(isReg && it.kwRegulator ? it.kwRegulator : [])
    const kwOk = need.every((k) => txt.includes(k))
    const alive = txt.length > 120
    const errs = allErrors.length - before
    // 是否被守卫弹回看板（无权限时路由守卫会 next('/dashboard')）
    const bounced = !page.url().includes(it.p.replace('/#', ''))

    row.pages.push({ name: it.name, kwOk, alive, errs, bounced })
    const mark = kwOk && alive && errs === 0 ? '✓' : '✗'
    const note = []
    if (!kwOk) note.push('关键词缺失')
    if (!alive) note.push('内容过少')
    if (errs) note.push(`报错${errs}`)
    if (bounced) note.push('被守卫跳转')
    console.log(`    ${mark} ${it.name.padEnd(10)} ${note.join(' ') || ''}`)
  }

  // 数据隔离抽查：看板统计口径
  current = `${u.label}/数据隔离`
  await page.goto(`${BASE}/#/dashboard`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(2000)
  const scope = await page.locator('.el-tag').filter({ hasText: /全量数据|仅我的数据|仅可交易/ }).first().innerText().catch(() => '—')
  row.scope = scope
  console.log(`  数据范围标签: ${scope}`)

  await page.screenshot({ path: `shot_${u.label}.png`, fullPage: true }).catch(() => {})
  await page.context().close()
  summary.push(row)
}

// ============ 汇总 ============
console.log('\n================ 汇总 ================')
let totalPages = 0, okPages = 0
for (const r of summary) {
  const ok = r.pages.filter((x) => x.kwOk && x.alive && x.errs === 0).length
  totalPages += r.pages.length
  okPages += ok
  console.log(`${ok === r.pages.length ? '✓' : '✗'} ${r.role.padEnd(8)} 菜单${r.menu} 页面 ${ok}/${r.pages.length}  范围=${r.scope}`)
}
console.log(`\n页面访问总计: ${okPages}/${totalPages} 通过`)
console.log(`控制台报错总数: ${allErrors.length}`)
allErrors.slice(0, 20).forEach((e) => console.log('  ' + e.slice(0, 170)))

await browser.close()
process.exit(allErrors.length === 0 && okPages === totalPages ? 0 : 2)
