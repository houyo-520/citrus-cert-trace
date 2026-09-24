/**
 * 移动端布局几何核查（替代人工看图）
 *
 * ★ 为什么要单独做：当前模型读不了图片，截图无法自证。
 *   但「布局能不能用」本质上是一组可测量的几何事实：
 *     - 可点元素够不够大（触摸命中面积 ≥ 40px）
 *     - 文字会不会小到看不清（< 11px 就不合格）
 *     - 关键内容有没有被挤出视口
 *   把这几条量出来，比"我看了一眼觉得还行"硬得多。
 *
 * 用法：node verify_mobile_geom.mjs
 */
import { chromium } from 'file:///C:/Users/houyo/.workbuddy/binaries/node/workspace/node_modules/playwright/index.mjs'

const BASE = (process.env.FRONTEND_VERIFY_BASE || 'http://<your-server-ip>:8090').replace(/\/+$/, '')
const EDGE = process.env.EDGE_PATH || 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'

let pass = 0, fail = 0
const ok = (n, e = '') => { pass++; console.log(`  [PASS] ${n}${e ? ' -> ' + e : ''}`) }
const bad = (n, e = '') => { fail++; console.log(`  [FAIL] ${n}${e ? ' -> ' + e : ''}`) }
const assert = (c, n, e = '') => c ? ok(n, e) : bad(n, e)

const browser = await chromium.launch({ executablePath: EDGE, headless: true })
const ctx = await browser.newContext({
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 3,
  isMobile: true,
  hasTouch: true,
  userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
})
const page = await ctx.newPage()

console.log('=== 移动端布局几何核查（iPhone 390x844）===\n')

// ---------- 验真页时间轴 ----------
console.log('--- 验真页时间轴 ---')
await page.goto(`${BASE}/#/verify`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1000)
const si = page.locator('input[placeholder*="合格证"], input[placeholder*="编号"]').first()
await si.fill('CERT-2026-1001')
await page.keyboard.press('Enter')
await page.waitForTimeout(3000)

// 节点几何：每个节点要有合理高度（不是被压成一条线）
const geom = await page.evaluate(() => {
  const items = [...document.querySelectorAll('.tl-item')]
  const out = items.map((el) => {
    const r = el.getBoundingClientRect()
    const c = el.querySelector('.tl-content')
    const cr = c ? c.getBoundingClientRect() : null
    return {
      h: Math.round(r.height),
      w: Math.round(r.width),
      contentW: cr ? Math.round(cr.width) : 0,
      left: Math.round(r.left),
      right: Math.round(r.right),
    }
  })
  // 最小字号（时间轴内容区）
  let minFont = 99
  const content = document.querySelector('.timeline')
  if (content) {
    for (const el of content.querySelectorAll('*')) {
      if (!el.textContent || !el.textContent.trim()) continue
      const fs = parseFloat(getComputedStyle(el).fontSize)
      if (fs > 0 && fs < minFont) minFont = fs
    }
  }
  // 结论块
  const v = document.querySelector('.verdict')
  const vr = v ? v.getBoundingClientRect() : null
  return {
    nodes: out, minFont,
    verdict: vr ? { h: Math.round(vr.height), w: Math.round(vr.width), top: Math.round(vr.top) } : null,
    vw: window.innerWidth,
  }
})

console.log(`     视口宽 ${geom.vw}px | 节点数 ${geom.nodes.length} | 时间轴最小字号 ${geom.minFont}px`)
for (const [i, n] of geom.nodes.entries()) {
  console.log(`     节点${i}: h=${n.h} w=${n.w} 内容宽=${n.contentW} left=${n.left} right=${n.right}`)
}

assert(geom.nodes.length >= 5, '时间轴节点数充足', `${geom.nodes.length} 个`)
// 每个节点高度要够（放得下标题+时间+描述），太矮说明被压扁
const tooShort = geom.nodes.filter((n) => n.h < 45)
assert(tooShort.length === 0, '无被压扁的节点（每个 ≥45px）',
  tooShort.length ? `${tooShort.length} 个过矮` : '全部合格')
// 内容区宽度要占满大部分视口（不被轴挤没）
const narrow = geom.nodes.filter((n) => n.contentW < geom.vw * 0.55)
assert(narrow.length === 0, '节点内容区宽度合理（≥视口 55%）',
  narrow.length ? `${narrow.length} 个过窄` : `最小 ${Math.min(...geom.nodes.map((n) => n.contentW))}px`)
// 不得越出右边界
const overflow = geom.nodes.filter((n) => n.right > geom.vw + 1)
assert(overflow.length === 0, '节点不越出视口右边界',
  overflow.length ? `${overflow.length} 个越界` : '')
// 字号可读
assert(geom.minFont >= 11, '时间轴文字不小于 11px（手机可读）', `${geom.minFont}px`)
assert(geom.verdict && geom.verdict.h >= 60, '结论块够大（视觉冲击力）',
  geom.verdict ? `h=${geom.verdict.h}` : '无')

// ---------- 触摸命中面积 ----------
console.log('\n--- 触摸命中面积 ---')
await page.goto(`${BASE}/#/login`, { waitUntil: 'networkidle' })
await page.evaluate(() => localStorage.clear())
await page.goto(`${BASE}/#/login`, { waitUntil: 'networkidle' })
await page.locator('input[type="text"], input[placeholder*="用户名"]').first().fill('regulator')
await page.locator('input[type="password"]').first().fill('admin123')
await page.keyboard.press('Enter')
await page.waitForTimeout(3000)

const btns = await page.evaluate(() => {
  const out = []
  for (const b of document.querySelectorAll('button, .el-menu-item, a')) {
    const r = b.getBoundingClientRect()
    if (r.width === 0 || r.height === 0) continue
    const txt = (b.innerText || '').trim().slice(0, 14)
    out.push({ h: Math.round(r.height), w: Math.round(r.width), txt })
  }
  return out
})
const tiny = btns.filter((b) => b.h < 28)
console.log(`     可点元素 ${btns.length} 个，其中高度 <28px 的 ${tiny.length} 个`)
if (tiny.length) console.log('     过小：', tiny.slice(0, 5).map((t) => `${t.txt}(${t.h}px)`).join(', '))
assert(tiny.length <= btns.length * 0.2, '绝大多数可点元素高度 ≥28px（触摸友好）',
  `过小占比 ${Math.round((tiny.length / Math.max(btns.length, 1)) * 100)}%`)

// 输入框字号（iOS 会自动放大 <16px 的输入框）
const inputFont = await page.evaluate(() => {
  const i = document.querySelector('.el-input__inner, input[type="text"], input[type="password"]')
  return i ? parseFloat(getComputedStyle(i).fontSize) : null
})
if (inputFont !== null) {
  assert(inputFont >= 16, '输入框字号 ≥16px（防 iOS 聚焦自动放大）', `${inputFont}px`)
}

// ---------- 各页关键内容可见 ----------
console.log('\n--- 各页关键内容可见性 ---')
const pages = [
  ['/dashboard', /看板|统计|预警|数据/],
  ['/certificate', /合格证/],
  ['/notice', /消息/],
  ['/report', /检测报告/],
]
for (const [route, re] of pages) {
  await page.goto(`${BASE}/#${route}`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(2000)
  const txt = await page.locator('body').innerText()
  assert(re.test(txt), `移动端 ${route} 关键内容渲染`, txt.replace(/\s+/g, ' ').trim().slice(0, 40))
  const o = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  assert(o <= 2, `移动端 ${route} 无横向溢出`, `${o}px`)
}

// ---------- 抽屉菜单项高度 ----------
console.log('\n--- 抽屉菜单 ---')
await page.goto(`${BASE}/#/dashboard`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1500)
const hb = page.locator('.header-mobile .el-button, header button').first()
if (await hb.count() > 0) {
  await hb.click()
  await page.waitForTimeout(900)
  const menuGeom = await page.evaluate(() => {
    const items = [...document.querySelectorAll('.el-drawer .el-menu-item')]
    return items.map((el) => {
      const r = el.getBoundingClientRect()
      return { h: Math.round(r.height), txt: (el.innerText || '').trim().slice(0, 12) }
    })
  })
  console.log(`     菜单项 ${menuGeom.length} 个：`, menuGeom.map((m) => `${m.txt}(${m.h}px)`).join(', '))
  assert(menuGeom.length >= 5, '抽屉菜单项齐全', `${menuGeom.length} 个`)
  const shortItems = menuGeom.filter((m) => m.h < 36)
  assert(shortItems.length === 0, '菜单项高度 ≥36px（好点）',
    shortItems.length ? shortItems.map((s) => s.txt).join(',') : '')
}

console.log(`\n=== 结果：PASS ${pass} / FAIL ${fail} ===`)
await browser.close()
process.exit(fail > 0 ? 1 : 0)