<template>
  <el-card shadow="never">
    <div class="toolbar">
      <el-button type="primary" @click="load">刷新</el-button>
      <el-button v-if="userStore.perms.can_issue" type="success" @click="openDialog()">
        新建合格证
      </el-button>
      <el-tag v-else type="info" effect="plain">
        当前角色为只读，签发/作废需监管人员权限
      </el-tag>
      <el-tag v-if="userStore.perms.can_issue" type="warning" effect="plain">
        编号规则：区域码6 + 日期8 + 流水5 = 19 位，签发时自动分配
      </el-tag>
      <!-- ★ 批量签发：仅在勾选了待审核的证时出现 -->
      <el-button v-if="userStore.perms.can_issue && selectedPending.length"
                 type="success" plain @click="openBatchIssue">
        批量签发（已选 {{ selectedPending.length }} 张）
      </el-button>
    </div>

    <el-table :data="list" v-loading="loading" border stripe
              ref="tableRef" @selection-change="onSelectionChange">
      <!-- ★ 批量选择：仅监管可用（其他角色看不到勾选框，避免误操作） -->
      <el-table-column v-if="userStore.perms.can_issue" type="selection"
                       width="46" :selectable="isSelectable" />
      <el-table-column label="合格证编号" width="190">
        <template #default="{ row }">
          <span v-if="row.cert_no" class="mono">{{ row.cert_no }}</span>
          <el-tag v-else size="small" type="info">未分配（{{ row.status_display }}）</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="producer_name_display" label="生产主体" min-width="150"
                       show-overflow-tooltip />
      <el-table-column prop="product_name" label="产品" width="80" />
      <el-table-column prop="quantity_kg" label="数量(kg)" width="95" />
      <el-table-column prop="issue_date" label="签发日期" width="110" />
      <el-table-column label="状态" width="95">
        <template #default="{ row }">
          <el-tag :type="certStatusType(row.status)" size="small">
            {{ row.status_display }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="监督检查" width="110">
        <template #default="{ row }">
          <el-tag v-if="row.latest_inspection_result" size="small"
                  :type="row.latest_inspection_result === '不合格' ? 'danger' : 'success'">
            {{ row.latest_inspection_result }}
          </el-tag>
          <span v-else class="text-muted">未检查</span>
        </template>
      </el-table-column>
      <el-table-column label="存证" width="85">
        <template #default="{ row }">
          <el-tag :type="row.is_anchored ? 'success' : 'info'" size="small">
            {{ row.is_anchored ? '已存证' : '未存证' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="哈希" width="130">
        <template #default="{ row }">
          <span class="mono hash-text">{{ (row.cert_hash || '').slice(0, 16) }}...</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="330" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="showDetail(row)">详情</el-button>
          <el-button v-if="row.cert_no" link type="primary" @click="showQrcode(row)">
            二维码
          </el-button>
          <!-- ★ 打印：仅已分配编号的证可打印（草稿无编号、证面不完整） -->
          <el-button v-if="row.cert_no" link type="success" @click="showPrint(row)">
            打印
          </el-button>
          <el-button v-if="userStore.perms.can_submit_cert && row.status === 'draft'"
                     link type="warning" @click="submit(row)">提交</el-button>
          <el-button v-if="userStore.perms.can_issue && row.status !== 'issued'"
                     link type="success" @click="issue(row)">签发</el-button>
          <el-button v-if="userStore.perms.can_issue && row.status === 'pending'"
                     link type="info" @click="reject(row)">驳回</el-button>
          <el-button v-if="userStore.perms.can_revoke && row.status !== 'revoked'"
                     link type="warning" @click="revoke(row)">作废</el-button>
          <el-button link type="danger" v-if="userStore.perms.can_issue"
                     @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建合格证：建档不占号 -->
    <el-dialog v-model="dialogVisible" title="新建合格证（建档）" width="620px">
      <el-alert type="info" :closable="false" show-icon class="tip">
        <template #title>
          建档阶段<strong>不分配编号</strong>（避免草稿吃掉流水号）。
          提交审核后由监管签发，签发时按「区域码 + 日期 + 流水」自动分配 19 位编号并上链存证。
        </template>
      </el-alert>
      <el-form :model="form" label-width="110px">
        <el-form-item label="生产批次" required>
          <el-select v-model="form.batch" style="width:100%" placeholder="选择批次"
                     @change="onBatchChange">
            <el-option v-for="b in batches" :key="b.id"
                       :label="`${b.batch_no} (${b.variety})`" :value="b.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="检测报告">
          <el-select v-model="form.report" style="width:100%" placeholder="选择报告（提交审核前必填）">
            <el-option v-for="r in reports" :key="r.id"
                       :label="`${r.report_no} (${r.conclusion_display})`" :value="r.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="主体名称">
          <el-input v-model="form.producer_name" />
        </el-form-item>
        <el-form-item label="产品名称">
          <el-input v-model="form.product_name" />
        </el-form-item>
        <el-form-item label="开具数量(kg)">
          <el-input-number v-model="form.quantity_kg" :min="0" style="width:100%" />
        </el-form-item>
        <el-form-item label="有效期至">
          <el-date-picker v-model="form.valid_until" type="date" value-format="YYYY-MM-DD"
                          style="width:100%" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="save">建档</el-button>
      </template>
    </el-dialog>

    <!-- 编号规则预览 -->
    <el-dialog v-model="previewVisible" title="即将分配的编号" width="520px">
      <div v-if="preview" class="preview-box">
        <div class="preview-no">{{ preview.preview_cert_no || '（无可分配编号）' }}</div>
        <div class="segments">
          <div class="seg">
            <div class="seg-val">{{ (preview.preview_cert_no || '').slice(0, 6) }}</div>
            <div class="seg-lbl">区域码</div>
          </div>
          <div class="seg">
            <div class="seg-val">{{ (preview.preview_cert_no || '').slice(6, 14) }}</div>
            <div class="seg-lbl">日期</div>
          </div>
          <div class="seg">
            <div class="seg-val">{{ (preview.preview_cert_no || '').slice(14) }}</div>
            <div class="seg-lbl">流水</div>
          </div>
        </div>
        <p class="text-muted">{{ preview.rule }}</p>
        <p class="text-muted">主体：{{ preview.producer_name || '—' }}（区域码 {{ preview.region_code }}）</p>
        <p class="text-muted">此编号仅为预览，实际分配在点击「确认签发」时发生。</p>
      </div>
      <template #footer>
        <el-button @click="previewVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmIssue">确认签发</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="qrVisible" title="合格证追溯二维码" width="380px" align-center>
      <div class="qr-box" v-if="qrCert">
        <div v-if="qrError" class="qr-fallback">
          <p class="text-danger">二维码生成失败</p>
          <p class="text-muted">
            {{ qrError }}<br />请复制下方链接在浏览器中打开验真页
          </p>
        </div>
        <img v-else-if="qrDataUrl" :src="qrDataUrl" alt="合格证追溯二维码" class="qr-img" />
        <div v-else class="qr-placeholder">二维码生成中…</div>

        <p class="qr-no mono">{{ qrCert.cert_no }}</p>
        <p class="text-muted">扫码进入公众验真页，可查看合格证、存证信息与监督检查公示</p>
        <el-input v-model="qrUrl" readonly size="small">
          <template #prepend>验真链接</template>
        </el-input>
      </div>
    </el-dialog>

    <!-- 详情：编号解析 + 检查汇总 -->
    <el-dialog v-model="detailVisible" title="合格证详情" width="700px">
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item label="合格证编号">
          <span v-if="detail.cert_no" class="mono">{{ detail.cert_no }}</span>
          <el-tag v-else size="small" type="info">未分配</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag size="small" :type="certStatusType(detail.status)">
            {{ detail.status_display }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="生产主体">{{ detail.producer_name_display }}</el-descriptions-item>
        <el-descriptions-item label="批次">{{ detail.batch_no || '—' }}</el-descriptions-item>
        <el-descriptions-item label="检测报告">{{ detail.report_no || '—' }}</el-descriptions-item>
        <el-descriptions-item label="开具数量">{{ detail.quantity_kg }} kg</el-descriptions-item>
        <el-descriptions-item label="签发日期">{{ detail.issue_date || '—' }}</el-descriptions-item>
        <el-descriptions-item label="有效期至">{{ detail.valid_until || '—' }}</el-descriptions-item>
        <el-descriptions-item v-if="detail.suspend_reason" label="暂停原因" :span="2">
          <span class="text-danger">{{ detail.suspend_reason }}</span>
        </el-descriptions-item>
      </el-descriptions>

      <div v-if="detail.cert_no_parsed && detail.cert_no_parsed.valid" class="parsed">
        <div class="parsed-title">编号构成（19 位）</div>
        <div class="segments">
          <div class="seg">
            <div class="seg-val">{{ detail.cert_no_parsed.region_code }}</div>
            <div class="seg-lbl">区域码</div>
          </div>
          <div class="seg">
            <div class="seg-val">{{ detail.cert_no_parsed.date }}</div>
            <div class="seg-lbl">签发日期</div>
          </div>
          <div class="seg">
            <div class="seg-val">{{ detail.cert_no_parsed.seq }}</div>
            <div class="seg-lbl">当日流水</div>
          </div>
        </div>
      </div>

      <div class="parsed" v-if="summary">
        <div class="parsed-title">监督检查情况</div>
        <el-descriptions :column="3" border size="small">
          <el-descriptions-item label="检查次数">{{ summary.total }}</el-descriptions-item>
          <el-descriptions-item label="不合格次数">{{ summary.unqualified_count }}</el-descriptions-item>
          <el-descriptions-item label="逾期未整改">{{ summary.overdue_count }}</el-descriptions-item>
        </el-descriptions>
        <el-descriptions v-if="summary.latest" :column="2" border size="small" class="mt">
          <el-descriptions-item label="最近结论">
            <el-tag size="small"
                    :type="summary.latest.check_result === 'unqualified' ? 'danger' : 'success'">
              {{ summary.latest.check_result_display }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="评分">{{ summary.latest.check_score }}</el-descriptions-item>
          <el-descriptions-item label="整改状态" :span="2">
            {{ summary.latest.rectify_status_display }}
          </el-descriptions-item>
        </el-descriptions>
      </div>

      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- ★ 证面预览与打印（子组件，新窗口 + 原生 CSS 打印方案） -->
    <CertPrint ref="printerRef" />

    <!-- ★ 批量签发确认 -->
    <el-dialog v-model="batchVisible" title="批量签发合格证" width="620px">
      <el-alert type="warning" :closable="false" show-icon class="tip">
        <template #title>
          将对下列 <strong>{{ selectedPending.length }}</strong> 张待审核合格证依次签发。
          每张证会<strong>独立分配 19 位编号</strong>并分别上链存证（编号不会连号跳号，逐张原子分配）。
        </template>
      </el-alert>
      <el-table :data="selectedPending" border size="small" max-height="300">
        <el-table-column prop="producer_name_display" label="生产主体" min-width="140"
                         show-overflow-tooltip />
        <el-table-column prop="product_name" label="产品" width="80" />
        <el-table-column prop="quantity_kg" label="数量(kg)" width="95" />
        <el-table-column label="检测报告" width="130">
          <template #default="{ row }">
            <span v-if="row.report_no" class="mono">{{ row.report_no }}</span>
            <el-tag v-else size="small" type="danger">缺失</el-tag>
          </template>
        </el-table-column>
      </el-table>

      <!-- 逐张签发的结果回执 -->
      <div v-if="batchResult.length" class="batch-result">
        <div class="parsed-title">签发结果</div>
        <el-table :data="batchResult" border size="small" max-height="220">
          <el-table-column prop="label" label="合格证" min-width="150" show-overflow-tooltip />
          <el-table-column label="结果" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="row.ok ? 'success' : 'danger'">
                {{ row.ok ? '成功' : '失败' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="cert_no" label="分配编号" width="180">
            <template #default="{ row }">
              <span class="mono">{{ row.cert_no || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="message" label="说明" min-width="150"
                           show-overflow-tooltip />
        </el-table>
      </div>

      <template #footer>
        <el-button @click="batchVisible = false">关闭</el-button>
        <el-button type="primary" :loading="batchLoading" @click="doBatchIssue">
          确认签发 {{ selectedPending.length }} 张
        </el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted, reactive, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import QRCode from 'qrcode'
import CertPrint from '@/components/CertPrint.vue'
import {
  listCertificate, createCertificate, issueCertificate, revokeCertificate,
  deleteCertificate, submitCertificate, rejectCertificate,
  peekCertNo, certInspectionSummary, listBatch, listReport,
  batchIssueCertificate
} from '@/api'
import { useUserStore } from '@/store/user'

const userStore = useUserStore()
const list = ref([])
const batches = ref([])
const reports = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const qrVisible = ref(false)
const previewVisible = ref(false)
const detailVisible = ref(false)
const qrCert = ref(null)
const detail = ref({})
const summary = ref(null)
const preview = ref(null)
const pendingIssue = ref(null)
const form = reactive({ product_name: '柑橘', quantity_kg: 0 })

// ★ 打印 / 批量签发
const printerRef = ref(null)
const tableRef = ref(null)
const selected = ref([])
const batchVisible = ref(false)
const batchLoading = ref(false)
const batchResult = ref([])

/** 勾选行中「待审核」的那些 —— 只有待审核的证才能批量签发。 */
const selectedPending = computed(() =>
  selected.value.filter((r) => r.status === 'pending'))

/**
 * 行是否可勾选：只有待审核状态能批量签发。
 * 草稿还没提交、已签发的不用重复签、已作废不可签。
 */
const isSelectable = (row) => row.status === 'pending'

const onSelectionChange = (rows) => { selected.value = rows }

const showPrint = (row) => {
  printerRef.value?.open(row)
}

const qrUrl = computed(() => {
  if (!qrCert.value) return ''
  return `${window.location.origin}/#/verify/${qrCert.value.cert_no}`
})

// 二维码由 qrcode 库在浏览器端生成 data URL。
// 注意：绝不能把验真页 URL 直接当 <img src>，那是 HTML 页面，浏览器按图片解码必然失败。
const qrDataUrl = ref('')
const qrError = ref('')

watch([qrVisible, qrUrl], async ([visible, url]) => {
  if (!visible) {
    qrDataUrl.value = ''
    qrError.value = ''
    return
  }
  if (!url) return
  try {
    qrDataUrl.value = await QRCode.toDataURL(url, {
      width: 400,
      margin: 2,
      errorCorrectionLevel: 'M',
      color: { dark: '#000000', light: '#FFFFFF' }
    })
    qrError.value = ''
  } catch (e) {
    qrDataUrl.value = ''
    qrError.value = e?.message || '请复制下方链接在浏览器中打开'
    console.error('[certificate] 二维码生成失败', e)
  }
}, { immediate: true })

const certStatusType = (s) => ({
  draft: 'info', pending: 'warning', issued: 'success',
  suspended: 'danger', revoked: 'danger'
}[s] || 'info')

const load = async () => {
  loading.value = true
  try {
    const res = await listCertificate({ page_size: 100 })
    list.value = res.results || res.data || []
  } finally {
    loading.value = false
  }
}

const loadRefs = async () => {
  const [b, r] = await Promise.all([listBatch({ page_size: 100 }), listReport({ page_size: 100 })])
  batches.value = b.results || b.data || []
  reports.value = r.results || r.data || []
}

const onBatchChange = (id) => {
  const b = batches.value.find((x) => x.id === id)
  if (b) {
    form.producer_name = b.producer_name
    form.product_name = b.product_name
    form.quantity_kg = Number(b.weight_kg)
  }
}

const openDialog = () => {
  Object.keys(form).forEach((k) => delete form[k])
  Object.assign(form, { product_name: '柑橘', quantity_kg: 0 })
  dialogVisible.value = true
}

const save = async () => {
  if (!form.batch) {
    ElMessage.warning('请选择生产批次')
    return
  }
  await createCertificate(form)
  ElMessage.success('已建档（草稿不占号，提交审核后由监管签发分配编号）')
  dialogVisible.value = false
  load()
}

const submit = async (row) => {
  try {
    await submitCertificate(row.id)
    ElMessage.success('已提交审核，等待监管签发')
    load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || '提交失败（需先关联检测报告）')
  }
}

const issue = async (row) => {
  // 先预看即将分配的编号，让监管对编号规则有直观认识
  const producerId = row.batch ? (batches.value.find((b) => b.id === row.batch) || {}).producer : null
  try {
    const res = await peekCertNo(producerId)
    preview.value = res.data
  } catch (e) {
    preview.value = null
  }
  pendingIssue.value = row
  previewVisible.value = true
}

const confirmIssue = async () => {
  await issueCertificate(pendingIssue.value.id)
  ElMessage.success('签发成功：编号已分配并完成可信存证')
  previewVisible.value = false
  load()
}

const reject = async (row) => {
  const { value } = await ElMessageBox.prompt('请填写驳回原因', '驳回合格证申请',
    { type: 'warning', inputPlaceholder: '如：主体名称有误 / 缺少检测报告' })
  await rejectCertificate(row.id, value || '')
  ElMessage.success('已驳回，退回草稿状态（未占号）')
  load()
}

const revoke = async (row) => {
  const { value } = await ElMessageBox.prompt(
    `确认作废合格证「${row.cert_no || '草稿#' + row.id}」？请填写作废原因（将记入审计日志与链上存证）`,
    '作废合格证',
    { type: 'warning', inputPlaceholder: '如：抽检不合格 / 信息填报有误' }
  )
  await revokeCertificate(row.id, value || '')
  ElMessage.success('已作废，作废记录已存证并留痕')
  load()
}

const remove = async (row) => {
  await ElMessageBox.confirm(`确认删除合格证「${row.cert_no || '草稿#' + row.id}」？`,
    '提示', { type: 'warning' })
  await deleteCertificate(row.id)
  ElMessage.success('已删除')
  load()
}

const openBatchIssue = () => {
  batchResult.value = []
  batchVisible.value = true
}

/**
 * 批量签发。
 *
 * 后端逐张处理并逐张返回结果（不整体失败）——
 * 因为「一张证缺检测报告」不应该连累另外 9 张本来就合规的证。
 * 前端把回执逐条列出来，监管一眼能看到哪张成功了、哪张为什么失败。
 */
const doBatchIssue = async () => {
  const ids = selectedPending.value.map((r) => r.id)
  if (!ids.length) {
    ElMessage.warning('请先勾选待审核的合格证')
    return
  }
  batchLoading.value = true
  try {
    const res = await batchIssueCertificate(ids)
    const d = res.data || {}
    batchResult.value = d.results || []
    const ok = d.success_count ?? 0
    const fail = d.fail_count ?? 0
    if (fail === 0) {
      ElMessage.success(`批量签发完成：${ok} 张全部成功`)
    } else {
      ElMessage.warning(`批量签发完成：成功 ${ok} 张，失败 ${fail} 张（见下方明细）`)
    }
    tableRef.value?.clearSelection()
    load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || '批量签发失败')
  } finally {
    batchLoading.value = false
  }
}

const showQrcode = (row) => {
  qrCert.value = row
  qrVisible.value = true
}

const showDetail = async (row) => {
  detail.value = row
  summary.value = null
  detailVisible.value = true
  try {
    const res = await certInspectionSummary(row.id)
    summary.value = res.data
  } catch (e) {
    summary.value = null
  }
}

onMounted(() => { load(); loadRefs() })
</script>

<style scoped>
.toolbar { display: flex; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; align-items: center; }
.qr-box { text-align: center; }
.qr-img { width: 220px; height: 220px; display: block; margin: 0 auto; }
.qr-placeholder {
  width: 220px; height: 220px; margin: 0 auto; border-radius: 8px;
  border: 1px dashed #dcdfe6; color: #909399; font-size: 13px;
  display: flex; align-items: center; justify-content: center;
}
.qr-fallback {
  min-height: 120px; padding: 18px; border-radius: 8px;
  border: 1px dashed #f56c6c; background: #fef0f0;
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px;
}
.qr-no { font-weight: 600; margin: 10px 0 4px; }
.mono { font-family: Consolas, Monaco, monospace; font-size: 12px; }
.hash-text { font-size: 12px; color: #606266; }
.text-muted { color: #909399; font-size: 12px; }
.text-danger { color: #f56c6c; font-weight: 600; }
.tip { margin-bottom: 14px; }
.preview-box { text-align: center; }
.preview-no {
  font-family: Consolas, Monaco, monospace;
  font-size: 26px;
  font-weight: 700;
  letter-spacing: 2px;
  color: #E8743B;
  margin-bottom: 16px;
}
.segments {
  display: flex;
  justify-content: center;
  gap: 10px;
  margin-bottom: 12px;
}
.seg {
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  padding: 8px 14px;
  min-width: 92px;
}
.seg-val { font-family: Consolas, Monaco, monospace; font-size: 16px; font-weight: 600; }
.seg-lbl { font-size: 11px; color: #909399; margin-top: 4px; }
.parsed { margin-top: 16px; }
.parsed-title { font-weight: 600; margin-bottom: 10px; font-size: 14px; }
.batch-result { margin-top: 16px; }
.mt { margin-top: 10px; }
</style>
