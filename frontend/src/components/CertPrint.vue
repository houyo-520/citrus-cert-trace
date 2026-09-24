<template>
  <el-dialog v-model="visible" title="合格证证面预览与打印" width="880px" top="4vh"
             @opened="onOpened">
    <el-alert type="info" :closable="false" show-icon class="tip">
      <template #title>
        证面样式依据农业农村部<strong>《农产品质量安全承诺达标合格证内容及样式》公告（第989号）</strong>设计。
        点击「打印 / 另存 PDF」后在浏览器打印对话框中可选择打印机或「另存为 PDF」。
      </template>
    </el-alert>

    <div v-loading="loading" class="stage">
      <!-- ★ 打印区域：单独一个容器，打印时只输出它（见下方 @media print） -->
      <div v-if="data" id="cert-print-area" class="cert">
        <div class="cert-frame">
          <!-- 标题 -->
          <div class="cert-title">农产品质量安全承诺达标合格证</div>
          <div class="cert-subtitle">
            编号：<span class="mono cert-no">{{ data.cert_no }}</span>
          </div>

          <div class="cert-body">
            <table class="cert-table">
              <tbody>
                <tr>
                  <th>生产主体</th>
                  <td colspan="3">{{ data.producer_name || '—' }}</td>
                </tr>
                <tr>
                  <th>统一社会<br />信用代码</th>
                  <td colspan="3" class="mono">{{ data.producer_credit_code || '—' }}</td>
                </tr>
                <tr>
                  <th>产地地址</th>
                  <td colspan="3">{{ data.producer_address || '—' }}</td>
                </tr>
                <tr>
                  <th>产品名称</th>
                  <td>{{ data.product_name || '—' }}</td>
                  <th>品种</th>
                  <td>{{ data.variety || '—' }}</td>
                </tr>
                <tr>
                  <th>产地</th>
                  <td>{{ data.origin || '—' }}</td>
                  <th>批次编号</th>
                  <td class="mono">{{ data.batch_no || '—' }}</td>
                </tr>
                <tr>
                  <th>采收日期</th>
                  <td>{{ data.harvest_date || '—' }}</td>
                  <th>开具数量</th>
                  <td>{{ data.quantity_kg }} kg</td>
                </tr>
                <tr>
                  <th>检测报告</th>
                  <td>{{ data.report_no || '—' }}</td>
                  <th>检测结论</th>
                  <td>
                    <span :class="data.conclusion === '合格' ? 'ok' : 'bad'">
                      {{ data.conclusion || '—' }}
                    </span>
                  </td>
                </tr>
                <tr>
                  <th>检测机构</th>
                  <td>{{ data.test_org || '—' }}</td>
                  <th>检测日期</th>
                  <td>{{ data.test_date || '—' }}</td>
                </tr>
                <tr>
                  <th>签发日期</th>
                  <td>{{ data.issue_date || '—' }}</td>
                  <th>有效期至</th>
                  <td>{{ data.valid_until || '—' }}</td>
                </tr>
                <tr v-if="data.suspend_reason">
                  <th class="bad">暂停原因</th>
                  <td colspan="3" class="bad">{{ data.suspend_reason }}</td>
                </tr>
                <tr>
                  <th>承诺内容</th>
                  <td colspan="3" class="promise">
                    本主体郑重承诺：上述产品在生产过程中<b>未使用禁用农药、兽药及其他禁用物质</b>，
                    严格执行农业投入品使用安全间隔期或休药期规定，产品符合农产品质量安全标准。
                  </td>
                </tr>
              </tbody>
            </table>

            <!-- 底部：二维码 + 可信存证 -->
            <div class="cert-foot">
              <div class="foot-qr">
                <img v-if="qrDataUrl" :src="qrDataUrl" class="qr-img"
                     alt="合格证追溯二维码" />
                <div v-else class="qr-ph">二维码<br />生成中</div>
                <div class="qr-cap">扫码验真</div>
              </div>
              <div class="foot-info">
                <div class="info-line">
                  <span class="lbl">验真链接</span>
                  <span class="mono val-url">{{ data.verify_url }}</span>
                </div>
                <div class="info-line">
                  <span class="lbl">数据摘要</span>
                  <span class="mono val-hash">{{ (data.cert_hash || '').slice(0, 32) }}…</span>
                </div>
                <div class="info-line">
                  <span class="lbl">存证状态</span>
                  <span class="val">
                    <template v-if="data.is_anchored">
                      已上链（FISCO BCOS 联盟链）
                      <span class="mono">· 区块 #{{ data.block_height }}</span>
                    </template>
                    <template v-else>未存证</template>
                  </span>
                </div>
                <div class="info-line" v-if="data.tx_hash">
                  <span class="lbl">交易哈希</span>
                  <span class="mono val-tx">{{ data.tx_hash }}</span>
                </div>
              </div>
            </div>

            <div class="cert-sign">
              <div class="sign-item">
                签字（盖章）：<span class="sign-line"></span>
              </div>
              <div class="sign-item">
                日期：<span class="year">{{ data.issue_date || '' }}</span>
              </div>
            </div>
          </div>

          <div class="cert-note">{{ data.notice }}</div>
        </div>
      </div>
      <el-empty v-else-if="!loading" description="暂无证面数据" />
    </div>

    <template #footer>
      <span class="foot-hint">
        <el-tag size="small" type="info" effect="plain">
          建议打印尺寸 A5 横向；浏览器打印时请勾选「背景图形」
        </el-tag>
      </span>
      <el-button @click="visible = false">关闭</el-button>
      <el-button type="primary" :disabled="!data" @click="print">
        <el-icon><Printer /></el-icon> 打印 / 另存 PDF
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { Printer } from '@element-plus/icons-vue'
import QRCode from 'qrcode'
import { certPrintData } from '@/api'

const visible = ref(false)
const loading = ref(false)
const data = ref(null)
const qrDataUrl = ref('')

const qrText = computed(() => data.value?.verify_url || '')

/**
 * 打开证面预览。
 *
 * 注意执行顺序：先把 loading 打开、再取数、再生成二维码。
 * 二维码必须在**数据到位之后**生成 —— 否则 verify_url 还是空串，
 * qrcode 会对空串编码出一个「能扫但扫出来是空白」的二维码，
 * 而且这种 bug 不报错、图也有 naturalWidth > 0，肉眼看不出来。
 */
const open = async (row) => {
  visible.value = true
  loading.value = true
  data.value = null
  qrDataUrl.value = ''
  try {
    const res = await certPrintData(row.id)
    data.value = res.data
    // 数据到位后再生成二维码，确保链接非空
    if (qrText.value) {
      qrDataUrl.value = await QRCode.toDataURL(qrText.value, {
        width: 320, margin: 1, errorCorrectionLevel: 'M',
        color: { dark: '#000000', light: '#FFFFFF' }
      })
    }
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || '证面数据获取失败')
    visible.value = false
  } finally {
    loading.value = false
  }
}

/**
 * ★ 打印：新窗口 + 克隆 DOM + 内联样式的方案。
 *
 * 为什么不用 html2canvas / el-dialog 直接 window.print()：
 *   - html2canvas 会把页面转成位图，打印出来是「图片」，文字不可选、不清晰，
 *     且要新增一个 ~200KB 的依赖（本项目已刻意保持零多余依赖）。
 *   - 直接在弹窗里 print() 会把遮罩、按钮、侧边栏一起打进纸面。
 * 新窗口方案只复制证面 DOM 并附带一段专用 CSS，输出的是**原生矢量文本**，
 * 打印机可选、可缩放、体积小。
 */
const print = async () => {
  if (!data.value) return
  await nextTick()
  const area = document.getElementById('cert-print-area')
  if (!area) {
    ElMessage.error('打印区域未渲染，请稍后重试')
    return
  }
  const win = window.open('', '_blank', 'width=900,height=720')
  if (!win) {
    ElMessage.error('浏览器拦截了新窗口，请允许弹出窗口后重试')
    return
  }
  const html = `<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8" />
<title>合格证 ${data.value.cert_no}</title>
<style>
  @page { size: A5 landscape; margin: 8mm; }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 10px; background: #fff;
         font-family: "Microsoft YaHei", "PingFang SC", "SimSun", sans-serif;
         color: #000; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  .cert-frame { border: 2px solid #1f6b4a; border-radius: 4px; padding: 14px 18px; }
  .cert-title { text-align: center; font-size: 26px; font-weight: 700;
                letter-spacing: 4px; color: #1f6b4a; margin: 4px 0 6px; }
  .cert-subtitle { text-align: center; font-size: 13px; margin-bottom: 12px; }
  .mono { font-family: Consolas, "Courier New", monospace; }
  .cert-no { font-size: 17px; font-weight: 700; letter-spacing: 1px; }
  .cert-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  .cert-table th, .cert-table td { border: 1px solid #333; padding: 5px 8px;
                                   vertical-align: middle; text-align: left; }
  .cert-table th { background: #eef6f1; font-weight: 600; width: 96px;
                   white-space: nowrap; text-align: center; }
  .promise { line-height: 1.6; }
  .ok { color: #1f6b4a; font-weight: 700; }
  .bad { color: #c0392b; font-weight: 700; }
  .cert-foot { display: flex; gap: 16px; margin-top: 12px; align-items: flex-start; }
  .foot-qr { text-align: center; width: 118px; flex: none; }
  .qr-img { width: 108px; height: 108px; }
  .qr-ph { width: 108px; height: 108px; border: 1px dashed #999; font-size: 11px;
           display: flex; align-items: center; justify-content: center; }
  .qr-cap { font-size: 11px; color: #555; margin-top: 2px; }
  .foot-info { flex: 1; font-size: 12px; line-height: 1.9; min-width: 0; }
  .info-line { display: flex; gap: 8px; }
  .lbl { color: #555; flex: none; width: 62px; }
  .val, .val-url, .val-hash, .val-tx { word-break: break-all; }
  .val-tx { font-size: 10.5px; }
  .cert-sign { display: flex; justify-content: space-between; margin-top: 14px;
               font-size: 13px; }
  .sign-line { display: inline-block; width: 180px; border-bottom: 1px solid #333; }
  .cert-note { margin-top: 12px; font-size: 10.5px; color: #666; text-align: center; }
  /* 打印时去掉可能的交互元素 */
  button { display: none !important; }
</style></head>
<body>${area.outerHTML}
<script>
  window.onload = function () {
    setTimeout(function () { window.focus(); window.print(); }, 260);
  };
<\/script>
</body></html>`
  win.document.open()
  win.document.write(html)
  win.document.close()
}

const onOpened = () => { /* 预留：弹窗动画结束后可做尺寸校正 */ }

defineExpose({ open })
</script>

<style scoped>
.tip { margin-bottom: 12px; }
.stage { max-height: 62vh; overflow: auto; background: #f5f7fa; padding: 16px; }
.cert { background: #fff; max-width: 760px; margin: 0 auto; }
.cert-frame { border: 2px solid #1f6b4a; border-radius: 4px; padding: 14px 18px; }
.cert-title {
  text-align: center; font-size: 24px; font-weight: 700;
  letter-spacing: 4px; color: #1f6b4a; margin: 2px 0 6px;
}
.cert-subtitle { text-align: center; font-size: 13px; margin-bottom: 12px; }
.mono { font-family: Consolas, Monaco, monospace; }
.cert-no { font-size: 17px; font-weight: 700; letter-spacing: 1px; }
.cert-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.cert-table th, .cert-table td {
  border: 1px solid #333; padding: 5px 8px; vertical-align: middle; text-align: left;
}
.cert-table th {
  background: #eef6f1; font-weight: 600; width: 96px;
  white-space: nowrap; text-align: center;
}
.promise { line-height: 1.6; }
.ok { color: #1f6b4a; font-weight: 700; }
.bad { color: #c0392b; font-weight: 700; }
.cert-foot { display: flex; gap: 16px; margin-top: 12px; align-items: flex-start; }
.foot-qr { text-align: center; width: 118px; flex: none; }
.qr-img { width: 108px; height: 108px; display: block; margin: 0 auto; }
.qr-ph {
  width: 108px; height: 108px; border: 1px dashed #999; font-size: 11px;
  color: #999; background: #fafafa;
  display: flex; align-items: center; justify-content: center; text-align: center;
}
.qr-cap { font-size: 11px; color: #666; margin-top: 2px; }
.foot-info { flex: 1; font-size: 12px; line-height: 1.9; min-width: 0; }
.info-line { display: flex; gap: 8px; }
.lbl { color: #666; flex: none; width: 62px; }
.val, .val-url, .val-hash, .val-tx { word-break: break-all; }
.val-tx { font-size: 11px; }
.val-url { color: #1f6b4a; }
.cert-sign {
  display: flex; justify-content: space-between; margin-top: 14px; font-size: 13px;
}
.sign-line { display: inline-block; width: 180px; border-bottom: 1px solid #333; }
.cert-note { margin-top: 12px; font-size: 10.5px; color: #777; text-align: center; }
.foot-hint { margin-right: 12px; }
</style>