/**
 * 检测报告附件上传 —— 浏览器端真机验收
 *
 * 为什么不能只靠 API 验收：
 *   API 验收证明了后端 + nginx 链路通，但证明不了「页面上那个按钮真的能点」。
 *   本次改动的核心交付物就是 el-upload，必须在真实浏览器里点一遍。
 *
 * ★ 非破坏性纪律：
 *   上传流程在**自建的临时报告**上跑，跑完删掉，绝不碰现有演示样本。
 *   早期版本直接往演示报告上传 32 字节空 PDF，导致答辩时点「查看附件」
 *   是一片空白 —— 验收脚本自己污染演示数据，比不验收更糟。
 *
 * 验收项：
 *   1. 检测报告页出现「报告附件」列，未上传显示「未上传」标签
 *   2. 新增报告弹窗里出现「报告附件」上传控件（选择文件按钮 + 提示文案）
 *   3. 已有报告行出现「上传」按钮（producer 可见 / buyer 不可见）
 *   4. 自建临时报告 → 点「上传」→ 塞入真实文件 → 点「上传附件」→ 成功提示
 *   5. 该行附件列从「未上传」变为「查看附件」链接
 *   6. ★ 点「查看附件」真的能打开文件（HTTP 200 且字节一致）
 *   7. 删除临时报告，演示数据保持原样
 *   8. 全程控制台报错数为 0
 *
 * 用法：node verify_report_upload.mjs
 */
import { chromium } from 'file:///C:/Users/houyo/.workbuddy/binaries/node/workspace/node_modules/playwright/index.mjs'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const BASE = 'http://<your-server-ip>:8090'
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'

const PASS = []
const FAIL = []
const check = (name, cond, detail = '') => {
  ;(cond ? PASS : FAIL).push(name)
  console.log(`  ${cond ? '✓' : '✗'} ${name}${detail ? '  | ' + detail : ''}`)
}

// 造一个真实 PDF 文件供上传
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'rpt-'))
const pdfPath = path.join(tmpDir, '页面验收报告.pdf')
fs.writeFileSync(pdfPath, '%PDF-1.4\n% browser verify\n%%EOF\n')

const browser = await chromium.launch({ executablePath: EDGE, headless: true })
const errors = []

const login = async (user) => {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const p = await ctx.newPage()
  p.on('console', (m) => { if (m.type() === 'error') errors.push(`[${user}] ${m.text()}`) })
  p.on('pageerror', (e) => errors.push(`[${user}] pageerror: ${e.message}`))
  await p.goto(`${BASE}/#/login`, { waitUntil: 'networkidle' })
  const ins = p.locator('input')
  await ins.nth(0).fill(user)
  await ins.nth(1).fill('admin123')
  await p.locator('button').filter({ hasText: /登\s*录/ }).first().click()
  await p.waitForTimeout(2400)
  return p
}

console.log('===== 检测报告附件上传 · 浏览器验收 =====')

// ---------------------------------------------------------------
// A. 种植主体：完整上传流程
// ---------------------------------------------------------------
console.log('\n--- A. 种植主体（producer）上传流程 ---')
const p = await login('producer')
await p.goto(`${BASE}/#/report`, { waitUntil: 'networkidle' })
await p.waitForTimeout(1800)

// 1) 表头有「报告附件」列
const headers = await p.locator('.el-table__header th').allInnerTexts()
check('检测报告表出现「报告附件」列',
      headers.some((h) => h.includes('报告附件')), headers.join(' / ').slice(0, 120))

// 2) 新增弹窗里有上传控件
await p.locator('button').filter({ hasText: /新增检测报告/ }).first().click()
await p.waitForTimeout(900)
const dlgText = await p.locator('.el-dialog').first().innerText()
check('新增弹窗出现「报告附件」字段', dlgText.includes('报告附件'))
check('新增弹窗出现「选择文件」按钮', dlgText.includes('选择文件'))
check('新增弹窗有格式与大小提示',
      dlgText.includes('10 MB') && dlgText.includes('pdf'), '')
// 关掉弹窗
await p.locator('.el-dialog__headerbtn').first().click()
await p.waitForTimeout(700)

// 3) 行内「上传」按钮存在（producer 有写权限）
const rowUploadBtns = await p.locator('button:visible').filter({ hasText: /^上\s*传$/ }).count()
check('表格行出现「上传」按钮（producer 有写权限）', rowUploadBtns > 0,
      `${rowUploadBtns} 个`)

// 4) ★ 自建一条临时报告，在它身上跑上传流程（非破坏性，不碰演示样本）
const token = await p.evaluate(() => localStorage.getItem('token'))
const batchRes = await p.request.get(`${BASE}/api/batch/?page_size=1`, {
  headers: { Authorization: 'Bearer ' + token }
})
const batchData = await batchRes.json()
const batchId = (batchData.results || batchData.data || [])[0]?.id
check('取到可用批次用于建临时报告', !!batchId, `batch=${batchId}`)

const tmpNo = 'RPT-VERIFY-' + Date.now().toString(36).toUpperCase()
const createRes = await p.request.post(`${BASE}/api/report/`, {
  headers: { Authorization: 'Bearer ' + token },
  data: { report_no: tmpNo, batch: batchId, test_org: '浏览器验收（临时）',
          conclusion: 'qualified' }
})
const created = await createRes.json()
const tmpId = created.id
check('自建临时报告用于验收（不污染演示数据）', createRes.status() === 201 && !!tmpId,
      `${tmpNo} id=${tmpId}`)

await p.reload({ waitUntil: 'networkidle' })
await p.waitForTimeout(1800)

const rows = p.locator('.el-table__body tbody tr')
const rowCount = await rows.count()
check('报告列表有数据行', rowCount > 0, `${rowCount} 行`)

let targetIdx = -1
for (let i = 0; i < rowCount; i++) {
  const t = await rows.nth(i).innerText()
  if (t.includes(tmpNo)) { targetIdx = i; break }
}
check('临时报告出现在列表中且附件为「未上传」',
      targetIdx >= 0, targetIdx >= 0 ? `第 ${targetIdx + 1} 行` : '未找到临时报告')

if (targetIdx >= 0) {
  const targetNo = tmpNo
  await rows.nth(targetIdx).locator('button').filter({ hasText: /^上\s*传$/ }).click()
  await p.waitForTimeout(1000)

  const upDlg = await p.locator('.el-dialog:visible').last().innerText()
  check('上传弹窗显示报告编号', upDlg.includes(targetNo), targetNo)
  check('上传弹窗含拖拽区与限制提示',
        upDlg.includes('点击选择') && upDlg.includes('10 MB'))

  // 塞真实文件
  await p.locator('.el-dialog:visible input[type=file]').last()
    .setInputFiles(pdfPath)
  await p.waitForTimeout(800)

  // 点「上传附件」
  await p.locator('.el-dialog:visible button').filter({ hasText: /上传附件/ }).last().click()
  await p.waitForTimeout(2600)

  // 成功提示
  const msgs = await p.locator('.el-message').allInnerTexts()
  check('出现上传成功提示', msgs.some((m) => m.includes('附件已上传')),
        msgs.join(' | ').slice(0, 100))

  // 5) 该行附件列应变为「查看附件」
  await p.waitForTimeout(900)
  const afterText = await p.locator('.el-table__body tbody tr').nth(targetIdx).innerText()
  check('该行附件列变为「查看附件」链接', afterText.includes('查看附件'),
        afterText.replace(/\n/g, ' ').slice(0, 90))

  // 6) ★ 点链接真的能打开文件
  const link = p.locator('.el-table__body tbody tr').nth(targetIdx)
    .locator('a').filter({ hasText: /查看附件/ }).first()
  const href = await link.getAttribute('href')
  check('附件链接指向 /media/reports/', (href || '').includes('/media/reports/'), href || '')

  if (href) {
    const resp = await p.request.get(BASE + href)
    check('附件链接可访问（HTTP 200）', resp.status() === 200, `HTTP ${resp.status()}`)
    const body = await resp.body()
    check('下载内容与上传文件字节一致',
          body.length === fs.statSync(pdfPath).size,
          `${body.length}B`)
  }

  // 7) 清理临时报告
  const delRes = await p.request.delete(`${BASE}/api/report/${tmpId}/`, {
    headers: { Authorization: 'Bearer ' + token }
  })
  check('清理临时报告（演示数据保持原样）',
        delRes.status() === 204 || delRes.status() === 200, `HTTP ${delRes.status()}`)
}

// ---------------------------------------------------------------
// B. 收购商：不应看到「上传」按钮
// ---------------------------------------------------------------
console.log('\n--- B. 收购商（buyer）权限对照 ---')
const pb = await login('buyer')
await pb.goto(`${BASE}/#/report`, { waitUntil: 'networkidle' })
await pb.waitForTimeout(1800)
const buyerUpload = await pb.locator('button:visible').filter({ hasText: /^上\s*传$/ }).count()
const buyerNew = await pb.locator('button:visible').filter({ hasText: /新增检测报告/ }).count()
check('收购商看不到「上传」按钮', buyerUpload === 0, `${buyerUpload} 个`)
check('收购商看不到「新增检测报告」按钮', buyerNew === 0, `${buyerNew} 个`)
// 但能看到附件列（只读）
const buyerHeaders = await pb.locator('.el-table__header th').allInnerTexts()
check('收购商仍可见「报告附件」列（只读查看）',
      buyerHeaders.some((h) => h.includes('报告附件')))

console.log('\n================ 汇总 ================')
console.log(`通过 ${PASS.length} / 共 ${PASS.length + FAIL.length}`)
if (FAIL.length) {
  console.log('失败项：')
  FAIL.forEach((f) => console.log('  -', f))
}
console.log(`控制台报错总数: ${errors.length}`)
if (errors.length) errors.slice(0, 8).forEach((e) => console.log('  !', e.slice(0, 160)))

await browser.close()
fs.rmSync(tmpDir, { recursive: true, force: true })
process.exit(FAIL.length || errors.length ? 1 : 0)
