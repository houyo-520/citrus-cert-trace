<template>
  <el-card shadow="never">
    <div class="toolbar">
      <el-button type="primary" @click="load">刷新</el-button>
      <el-button v-if="userStore.perms.can_write_production" type="success"
                 @click="openDialog()">新增检测报告</el-button>
      <el-tag v-if="userStore.isBuyer" type="info" effect="plain">
        仅显示合格结论的报告
      </el-tag>
      <el-tag v-else-if="userStore.isProducer" type="info" effect="plain">
        仅显示我的报告数据
      </el-tag>
    </div>

    <el-table :data="list" v-loading="loading" border stripe>
      <el-table-column prop="report_no" label="报告编号" width="150" />
      <el-table-column prop="batch_no" label="所属批次" width="160" />
      <el-table-column prop="test_org" label="检测机构" min-width="180" />
      <el-table-column prop="test_date" label="检测日期" width="120" />
      <el-table-column label="结论" width="90">
        <template #default="{ row }">
          <el-tag :type="row.conclusion === 'qualified' ? 'success' : 'danger'" size="small">
            {{ row.conclusion_display }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="pesticide_residue" label="农药残留" min-width="150" />
      <el-table-column label="报告附件" width="120">
        <template #default="{ row }">
          <el-link v-if="row.file_url" type="primary" :href="row.file_url"
                   target="_blank" :underline="false">
            查看附件
          </el-link>
          <el-tag v-else type="info" size="small" effect="plain">未上传</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="报告哈希" width="150">
        <template #default="{ row }">
          <span class="hash-text">{{ (row.report_hash || '').slice(0, 16) }}...</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button v-if="userStore.perms.can_write_production" link type="primary"
                     @click="openUpload(row)">上传</el-button>
          <el-button v-if="userStore.perms.can_write_production" link type="danger"
                     @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" title="新增检测报告" width="560px">
      <el-form :model="form" label-width="110px">
        <el-form-item label="报告编号" required>
          <el-input v-model="form.report_no" placeholder="如 RPT-2026-1003" />
        </el-form-item>
        <el-form-item label="所属批次" required>
          <el-select v-model="form.batch" style="width:100%" placeholder="选择批次">
            <el-option v-for="b in batches" :key="b.id"
                       :label="`${b.batch_no} (${b.variety})`" :value="b.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="检测机构">
          <el-input v-model="form.test_org" />
        </el-form-item>
        <el-form-item label="检测日期">
          <el-date-picker v-model="form.test_date" type="date" value-format="YYYY-MM-DD"
                          style="width:100%" />
        </el-form-item>
        <el-form-item label="检测结论">
          <el-radio-group v-model="form.conclusion">
            <el-radio value="qualified">合格</el-radio>
            <el-radio value="unqualified">不合格</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="农药残留">
          <el-input v-model="form.pesticide_residue" />
        </el-form-item>
        <el-form-item label="重金属">
          <el-input v-model="form.heavy_metal" />
        </el-form-item>
        <el-form-item label="报告附件">
          <el-upload
            ref="createUploadRef"
            :auto-upload="false"
            :limit="1"
            :file-list="createFileList"
            accept=".pdf,.jpg,.jpeg,.png,.doc,.docx"
            :on-change="onCreateFileChange"
            :on-exceed="onCreateExceed">
            <el-button type="primary" plain>选择文件</el-button>
            <template #tip>
              <div class="upload-tip">
                支持 pdf / jpg / png / doc / docx，单个文件不超过 10 MB；保存报告后自动一并提交
              </div>
            </template>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="save">保存并自动存证</el-button>
      </template>
    </el-dialog>

    <!-- 给已有报告补传附件 -->
    <el-dialog v-model="uploadVisible" title="上传检测报告附件" width="520px">
      <el-descriptions :column="1" border size="small" class="upload-meta">
        <el-descriptions-item label="报告编号">{{ uploadRow.report_no }}</el-descriptions-item>
        <el-descriptions-item label="所属批次">{{ uploadRow.batch_no }}</el-descriptions-item>
        <el-descriptions-item label="当前附件">
          <el-link v-if="uploadRow.file_url" type="primary" :href="uploadRow.file_url"
                   target="_blank" :underline="false">已上传，点击查看</el-link>
          <span v-else class="muted">暂无</span>
        </el-descriptions-item>
      </el-descriptions>
      <el-upload
        ref="uploadRef"
        drag
        :auto-upload="false"
        :limit="1"
        :file-list="uploadFileList"
        accept=".pdf,.jpg,.jpeg,.png,.doc,.docx"
        :on-change="onUploadFileChange"
        :on-exceed="onUploadExceed">
        <div class="el-upload__text">将文件拖到此处，或<em>点击选择</em></div>
        <template #tip>
          <div class="upload-tip">
            支持 pdf / jpg / png / doc / docx，单个文件不超过 10 MB
          </div>
        </template>
      </el-upload>
      <template #footer>
        <el-button @click="uploadVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="confirmUpload">
          上传附件
        </el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted, reactive } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listReport, createReport, deleteReport, listBatch, uploadReportFile } from '@/api'
import { useUserStore } from '@/store/user'

const userStore = useUserStore()

const list = ref([])
const batches = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const form = reactive({ conclusion: 'qualified', pesticide_residue: '符合标准', heavy_metal: '符合标准' })

// ---- 附件上传：新建报告时随表单一并提交 ----
const createUploadRef = ref()
const createFileList = ref([])
const pendingFile = ref(null)

// ---- 附件上传：给已有报告补传 ----
const uploadRef = ref()
const uploadVisible = ref(false)
const uploadFileList = ref([])
const uploading = ref(false)
const uploadRow = ref({})
const uploadTarget = ref(null)

const load = async () => {
  loading.value = true
  try {
    const res = await listReport({ page_size: 100 })
    list.value = res.results || res.data || []
  } finally {
    loading.value = false
  }
}

const loadBatches = async () => {
  const res = await listBatch({ page_size: 100 })
  batches.value = res.results || res.data || []
}

const openDialog = () => {
  Object.keys(form).forEach((k) => delete form[k])
  Object.assign(form, { conclusion: 'qualified', pesticide_residue: '符合标准', heavy_metal: '符合标准' })
  createFileList.value = []
  pendingFile.value = null
  dialogVisible.value = true
}

// el-upload 的非自动上传模式：on-change 只在选文件时触发，不会真的发请求
const onCreateFileChange = (file) => {
  pendingFile.value = file.raw
}
const onCreateExceed = () => {
  ElMessage.warning('只能上传 1 个附件，请先移除已选文件')
}

const save = async () => {
  if (!form.report_no || !form.batch) {
    ElMessage.warning('请填写报告编号并选择批次')
    return
  }
  // multipart 一步到位：有附件时用 FormData，无附件时走原 JSON
  let payload = form
  if (pendingFile.value) {
    const fd = new FormData()
    Object.keys(form).forEach((k) => {
      if (form[k] !== undefined && form[k] !== null && form[k] !== '') fd.append(k, form[k])
    })
    fd.append('file', pendingFile.value)
    payload = fd
  }
  await createReport(payload)
  ElMessage.success('报告已保存并完成存证')
  dialogVisible.value = false
  createFileList.value = []
  pendingFile.value = null
  load()
}

// ---- 给已有报告补传附件 ----
const openUpload = (row) => {
  uploadRow.value = row
  uploadTarget.value = null
  uploadFileList.value = []
  uploadVisible.value = true
  // ref 在弹窗刚打开时可能还没挂载，延后一拍清空内部文件列表
  requestAnimationFrame(() => uploadRef.value?.clearFiles?.())
}

const onUploadFileChange = (file) => {
  uploadTarget.value = file.raw
}
const onUploadExceed = () => {
  ElMessage.warning('只能上传 1 个附件，请先移除已选文件')
}

const confirmUpload = async () => {
  if (!uploadTarget.value) {
    ElMessage.warning('请先选择要上传的附件')
    return
  }
  uploading.value = true
  try {
    await uploadReportFile(uploadRow.value.id, uploadTarget.value)
    ElMessage.success('附件已上传')
    uploadVisible.value = false
    load()
  } finally {
    uploading.value = false
  }
}

const remove = async (row) => {
  await ElMessageBox.confirm(`确认删除报告「${row.report_no}」？`, '提示', { type: 'warning' })
  await deleteReport(row.id)
  ElMessage.success('已删除')
  load()
}

onMounted(() => { load(); loadBatches() })
</script>

<style scoped>
.toolbar { display: flex; gap: 10px; margin-bottom: 14px; }
.hash-text { font-family: monospace; font-size: 12px; color: var(--el-text-color-secondary); }
.upload-tip { font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.6; }
.upload-meta { margin-bottom: 14px; }
.muted { color: var(--el-text-color-secondary); }
</style>