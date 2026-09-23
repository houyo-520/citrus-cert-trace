/**
 * 用 Edge 把演示检测报告 HTML 打印成 PDF，然后替换线上演示报告 RPT-2026-1002 的附件。
 *
 * 为什么这么做：验收脚本为了快，传的是 32 字节的空 PDF；
 * 答辩时点「查看附件」会是一片空白，反而减分。
 * 这里换成内容完整的检测报告，让演示附件是真的能看的东西。
 */
import { chromium } from 'file:///C:/Users/houyo/.workbuddy/binaries/node/workspace/node_modules/playwright/index.mjs'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
// ★ 必须用 fileURLToPath —— import.meta.url 是 percent-encoded 的 URL，
//   手工 pathname.replace 在中文路径下会生成 `%E6%AF%95...` 这种乱码目录，
//   文件被写到一个看起来不存在的地方（已踩坑）。
const HERE = path.dirname(fileURLToPath(import.meta.url))
const htmlPath = path.join(HERE, '_demo_report.html')
const pdfPath = path.join(HERE, '_demo_report.pdf')
const BASE = 'http://<your-server-ip>:8090'

// ---- 1) HTML -> PDF ----
const browser = await chromium.launch({ executablePath: EDGE, headless: true })
const page = await browser.newPage()
await page.goto('file:///' + htmlPath.replace(/\\/g, '/'), { waitUntil: 'load' })
await page.pdf({
  path: pdfPath,
  format: 'A4',
  printBackground: true,
  margin: { top: '0mm', bottom: '0mm', left: '0mm', right: '0mm' }
})
await browser.close()

const size = fs.statSync(pdfPath).size
console.log(`已生成 PDF：${pdfPath}  (${(size / 1024).toFixed(1)} KB)`)
if (size < 3000) {
  console.error('PDF 体积过小，可能没渲染出内容')
  process.exit(1)
}

// ---- 2) 用 API 替换线上演示报告附件 ----
const login = async (user) => {
  const r = await fetch(`${BASE}/api/auth/login/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: user, password: 'admin123' })
  })
  const d = await r.json()
  return (d.data || d).access || (d.data || d).token
}
const token = await login('producer')

const listRes = await fetch(`${BASE}/api/report/?page_size=100`, {
  headers: { Authorization: 'Bearer ' + token }
})
const listData = await listRes.json()
const reports = listData.results || listData.data || []

// 优先替换「合格」结论的演示报告
const target = reports.find((r) => r.conclusion === 'qualified') || reports[0]
if (!target) {
  console.error('线上没有检测报告可替换')
  process.exit(1)
}
console.log(`目标报告：${target.report_no} (id=${target.id})`)

const fd = new FormData()
fd.append('file', new Blob([fs.readFileSync(pdfPath)], { type: 'application/pdf' }),
          '眉山市农产品质量安全检测中心检验报告.pdf')
const upRes = await fetch(`${BASE}/api/report/${target.id}/`, {
  method: 'PATCH',
  headers: { Authorization: 'Bearer ' + token },
  body: fd
})
const upData = await upRes.json()
console.log(`上传结果：HTTP ${upRes.status}  file_name=${upData.file_name}`)
console.log(`附件地址：${upData.file_url}`)

// ---- 3) 回读验证 ----
const dl = await fetch(BASE + upData.file_url)
const bytes = Buffer.from(await dl.arrayBuffer())
console.log(`回读验证：HTTP ${dl.status}  ${(bytes.length / 1024).toFixed(1)} KB  ` +
            `PDF魔数=${bytes.slice(0, 5).toString()}`)
const ok = dl.status === 200 && bytes.length === size &&
           bytes.slice(0, 5).toString() === '%PDF-'
console.log(ok ? '\n演示附件替换成功 ✅' : '\n替换失败 ❌')
process.exit(ok ? 0 : 1)
