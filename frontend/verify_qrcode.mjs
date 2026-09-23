/**
 * 二维码专项验收：验证合格证追溯二维码能真正渲染出来
 *
 * 背景（本次线上事故）：
 *   原实现把「验真页 URL」直接当成 <img src>，那是 HTML 页面而非图片，
 *   浏览器按图片解码必然失败 ⇒ 二维码永远显示不出来（破图）。
 *   修法：用 qrcode 库在浏览器端生成 PNG data URL 再喂给 <img>。
 *
 * 本脚本要断言的硬事实（不能只看「没报错」）：
 *   1) .qr-img 元素存在
 *   2) img.src 以 data:image/png;base64, 开头（不是 http 页面 URL）
 *   3) img.naturalWidth > 0（★真正的加载成功标志；破图时为 0）
 *   4) 把 data URL 还原成字节，校验 PNG 魔数与 IHDR 声明的尺寸
 *   5) 控制台零报错
 *
 * 用法：node verify_qrcode.mjs
 */
import { chromium } from 'file:///C:/Users/houyo/.workbuddy/binaries/node/workspace/node_modules/playwright/index.mjs'

const BASE = 'http://<your-server-ip>:8090'
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
const USER = { u: 'admin', p: 'admin123' }

let pass = 0, fail = 0
const ok = (name, extra = '') => { pass++; console.log(`  [PASS] ${name}${extra ? ' -> ' + extra : ''}`) }
const bad = (name, extra = '') => { fail++; console.log(`  [FAIL] ${name}${extra ? ' -> ' + extra : ''}`) }
const assert = (cond, name, extra = '') => cond ? ok(name, extra) : bad(name, extra)

const browser = await chromium.launch({ executablePath: EDGE, headless: true })
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await ctx.newPage()

const errors = []
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message))

console.log('=== 二维码专项验收 ===\n')

// ---- 登录 ----
await page.goto(`${BASE}/#/login`, { waitUntil: 'networkidle' })
await page.fill('input[placeholder*="用户名"], input[type="text"]', USER.u)
await page.fill('input[type="password"]', USER.p)
await page.keyboard.press('Enter')
await page.waitForTimeout(2200)
assert(page.url().includes('/dashboard') || page.url().includes('/producer'),
  '登录成功', page.url())

// ---- 进入合格证页面 ----
await page.goto(`${BASE}/#/certificate`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1500)

// 找到「二维码」按钮（只有已分配编号的证才有）
const qrBtn = page.locator('button:has-text("二维码")').first()
const btnCount = await page.locator('button:has-text("二维码")').count()
assert(btnCount > 0, '存在「二维码」按钮', `${btnCount} 个`)
if (btnCount === 0) {
  console.log('\n无法继续：页面没有二维码入口')
  await browser.close(); process.exit(1)
}

await qrBtn.click()
await page.waitForTimeout(1800)

// ---- 断言图片真的渲染成功 ----
const imgInfo = await page.evaluate(() => {
  const el = document.querySelector('.qr-img')
  if (!el) return null
  return {
    src: el.getAttribute('src') || '',
    naturalWidth: el.naturalWidth,
    naturalHeight: el.naturalHeight,
    offsetWidth: el.offsetWidth,
    offsetHeight: el.offsetHeight,
    complete: el.complete,
  }
})

if (!imgInfo) {
  bad('二维码 <img> 元素存在', '.qr-img 未找到（可能落到了 fallback 分支）')
  const fb = await page.locator('.qr-fallback').count()
  if (fb > 0) console.log('  -> 命中了 qr-fallback 分支：' + (await page.locator('.qr-fallback').innerText()))
} else {
  ok('二维码 <img> 元素存在')

  assert(imgInfo.src.startsWith('data:image/png;base64,'),
    'src 是 PNG data URL（不是 HTML 页面 URL）',
    imgInfo.src.slice(0, 34) + '…')

  assert(!imgInfo.src.startsWith('http'),
    'src 不是 http 页面地址（防回归：原 bug 就是错把验真页 URL 当图片）')

  assert(imgInfo.complete === true, 'img.complete = true')

  assert(imgInfo.naturalWidth > 0 && imgInfo.naturalHeight > 0,
    'naturalWidth/Height > 0（★真正加载成功，破图时为 0）',
    `${imgInfo.naturalWidth}x${imgInfo.naturalHeight}`)

  assert(imgInfo.naturalWidth === 400 && imgInfo.naturalHeight === 400,
    '二维码位图尺寸 = 400x400', `${imgInfo.naturalWidth}x${imgInfo.naturalHeight}`)

  assert(imgInfo.offsetWidth > 100 && imgInfo.offsetHeight > 100,
    '渲染尺寸正常（CSS 未压成 0）',
    `${imgInfo.offsetWidth}x${imgInfo.offsetHeight}`)

  // ---- 还原字节校验 PNG 魔数与 IHDR ----
  const png = await page.evaluate(async (dataUrl) => {
    const res = await fetch(dataUrl)
    const buf = new Uint8Array(await res.arrayBuffer())
    const hex = Array.from(buf.slice(0, 8)).map(b => b.toString(16).padStart(2, '0')).join('')
    const dv = new DataView(buf.buffer)
    return { size: buf.length, magic: hex, w: dv.getUint32(16), h: dv.getUint32(20) }
  }, imgInfo.src)

  assert(png.magic === '89504e470d0a1a0a', 'PNG 魔数正确', png.magic)
  assert(png.w === 400 && png.h === 400, 'IHDR 声明尺寸 = 400x400', `${png.w}x${png.h}`)
  assert(png.size > 500 && png.size < 200000, '文件体积合理', `${png.size} 字节`)
}

// ---- 验真链接文本框 ----
const linkVal = await page.locator('.qr-box input').first().inputValue().catch(() => '')
assert(linkVal.includes('/#/verify/'), '验真链接正确回填', linkVal)

// ---- 控制台报错 ----
const real = errors.filter(e => !e.includes('favicon') && !e.includes('404'))
assert(real.length === 0, '控制台零报错', real.length ? real.slice(0, 3).join(' | ') : '')

console.log(`\n=== 结果：${pass} 通过 / ${fail} 失败 ===`)
await browser.close()
process.exit(fail ? 1 : 0)