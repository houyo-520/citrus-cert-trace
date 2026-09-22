<template>
  <el-card shadow="never">
    <div class="toolbar">
      <el-button type="primary" @click="load">刷新</el-button>
      <el-button type="success" @click="openDialog()">签发合格证</el-button>
    </div>

    <el-table :data="list" v-loading="loading" border stripe>
      <el-table-column prop="cert_no" label="合格证编号" width="150" />
      <el-table-column prop="producer_name" label="生产主体" min-width="160" />
      <el-table-column prop="product_name" label="产品" width="90" />
      <el-table-column prop="quantity_kg" label="数量(kg)" width="100" />
      <el-table-column prop="issue_date" label="签发日期" width="120" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="certStatusType(row.status)" size="small">
            {{ row.status_display }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="存证" width="90">
        <template #default="{ row }">
          <el-tag :type="row.is_anchored ? 'success' : 'info'" size="small">
            {{ row.is_anchored ? '已存证' : '未存证' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="哈希" width="140">
        <template #default="{ row }">
          <span class="hash-text">{{ (row.cert_hash || '').slice(0, 16) }}...</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="230" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="showQrcode(row)">二维码</el-button>
          <el-button link type="success" v-if="row.status !== 'issued'"
                     @click="issue(row)">签发</el-button>
          <el-button link type="warning" v-if="row.status !== 'revoked'"
                     @click="revoke(row)">作废</el-button>
          <el-button link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" title="签发合格证" width="560px">
      <el-form :model="form" label-width="110px">
        <el-form-item label="合格证编号" required>
          <el-input v-model="form.cert_no" placeholder="如 CERT-2026-1003" />
        </el-form-item>
        <el-form-item label="生产批次" required>
          <el-select v-model="form.batch" style="width:100%" placeholder="选择批次"
                     @change="onBatchChange">
            <el-option v-for="b in batches" :key="b.id"
                       :label="`${b.batch_no} (${b.variety})`" :value="b.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="检测报告">
          <el-select v-model="form.report" style="width:100%" placeholder="选择报告（可选）">
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
        <el-form-item label="签发日期">
          <el-date-picker v-model="form.issue_date" type="date" value-format="YYYY-MM-DD"
                          style="width:100%" />
        </el-form-item>
        <el-form-item label="有效期至">
          <el-date-picker v-model="form.valid_until" type="date" value-format="YYYY-MM-DD"
                          style="width:100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="save">签发并存证</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="qrVisible" title="合格证追溯二维码" width="360px" align-center>
      <div class="qr-box" v-if="qrCert">
        <img :src="qrUrl" alt="二维码" class="qr-img" />
        <p class="qr-no">{{ qrCert.cert_no }}</p>
        <p class="text-muted">扫码进入公众验真页，可查看合格证及存证信息</p>
        <el-input v-model="qrUrl" readonly size="small">
          <template #prepend>验真链接</template>
        </el-input>
      </div>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted, reactive, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listCertificate, createCertificate, issueCertificate, revokeCertificate,
  deleteCertificate, listBatch, listReport
} from '@/api'

const list = ref([])
const batches = ref([])
const reports = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const qrVisible = ref(false)
const qrCert = ref(null)
const form = reactive({ product_name: '柑橘', quantity_kg: 0 })

const qrUrl = computed(() => {
  if (!qrCert.value) return ''
  return `${window.location.origin}/#/verify/${qrCert.value.cert_no}`
})

const certStatusType = (s) => ({ pending: 'warning', issued: 'success', revoked: 'danger' }[s] || 'info')

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
  if (!form.cert_no || !form.batch) {
    ElMessage.warning('请填写合格证编号并选择批次')
    return
  }
  await createCertificate(form)
  ElMessage.success('合格证已签发并完成可信存证')
  dialogVisible.value = false
  load()
}

const issue = async (row) => {
  await issueCertificate(row.id)
  ElMessage.success('签发成功并重新存证')
  load()
}

const revoke = async (row) => {
  await ElMessageBox.confirm(`确认作废合格证「${row.cert_no}」？`, '提示', { type: 'warning' })
  await revokeCertificate(row.id)
  ElMessage.success('已作废')
  load()
}

const remove = async (row) => {
  await ElMessageBox.confirm(`确认删除合格证「${row.cert_no}」？`, '提示', { type: 'warning' })
  await deleteCertificate(row.id)
  ElMessage.success('已删除')
  load()
}

const showQrcode = (row) => {
  qrCert.value = row
  qrVisible.value = true
}

onMounted(() => { load(); loadRefs() })
</script>

<style scoped>
.toolbar { display: flex; gap: 10px; margin-bottom: 14px; }
.qr-box { text-align: center; }
.qr-img { width: 200px; height: 200px; }
.qr-no { font-weight: 600; margin: 10px 0 4px; }
</style>