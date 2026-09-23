/**
 * 业务流程按钮清点：确认各角色在各页面能看到哪些关键操作入口。
 * 只读检查，不真正提交数据，避免污染演示库。
 */
import { chromium } from 'file:///C:/Users/houyo/.workbuddy/binaries/node/workspace/node_modules/playwright/index.mjs'

const BASE = 'http://<your-server-ip>:8090'
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'

const browser = await chromium.launch({ executablePath: EDGE, headless: true })

const login = async (user) => {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const p = await ctx.newPage()
  await p.goto(`${BASE}/#/login`, { waitUntil: 'networkidle' })
  const ins = p.locator('input')
  await ins.nth(0).fill(user)
  await ins.nth(1).fill('admin123')
  await p.locator('button').filter({ hasText: /登\s*录/ }).first().click()
  await p.waitForTimeout(2400)
  return p
}

// 页面 → 期望可见的操作按钮关键词
// ★ 按钮是否出现往往取决于「数据状态 + 当前角色」，断言必须连角色和数据一起选对：
//   「提交」按钮的条件是 perms.can_submit_cert && row.status==='draft'，
//   而演示库里唯一那张草稿挂在 producer2 名下 → 必须用 producer2 登录才看得到。
const SPEC = [
  { role: 'regulator', path: '/#/producer', name: '生产主体', want: ['通过', '驳回'] },
  { role: 'producer', path: '/#/batch', name: '生产批次', want: ['新增', '存证'] },
  { role: 'producer', path: '/#/report', name: '检测报告', want: ['新增检测报告'] },
  { role: 'regulator', path: '/#/certificate', name: '合格证管理', want: ['签发', '作废'] },
  { role: 'producer2', path: '/#/certificate', name: '合格证-主体(有草稿)', want: ['提交'] },
  { role: 'regulator', path: '/#/inspection', name: '监督检查', want: ['录入检查'] },
  { role: 'producer', path: '/#/supply', name: '产销对接', want: ['发布'] },
  { role: 'producer', path: '/#/intent', name: '产销意向单', want: [] }
]

console.log('===== 业务流程操作入口清点 =====')
for (const s of SPEC) {
  const p = await login(s.role)
  await p.goto(BASE + s.path, { waitUntil: 'networkidle' })
  await p.waitForTimeout(1800)
  const btns = await p.locator('button:visible').allInnerTexts()
  const uniq = [...new Set(btns.map((b) => b.trim()).filter(Boolean))]
  const found = s.want.map((w) => [w, uniq.some((b) => b.includes(w))])
  const allOk = found.every(([, ok]) => ok)
  console.log(`\n${allOk ? '✓' : '·'} ${s.name}（${s.role}）`)
  if (s.want.length) {
    found.forEach(([w, ok]) => console.log(`    ${ok ? '✓' : '✗'} 含「${w}」`))
  }
  console.log(`    可见按钮: ${uniq.slice(0, 12).join(' / ')}`)
  await p.context().close()
}

await browser.close()
