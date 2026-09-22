<template>
  <el-card shadow="never">
    <div class="toolbar">
      <el-button type="primary" @click="load">刷新</el-button>
      <el-button type="success" @click="openDialog()">新增检测报告</el-button>
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
      <el-table-column label="报告哈希" width="150">
        <template #default="{ row }">
          <span class="hash-text">{{ (row.report_hash || '').slice(0, 16) }}...</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button link type="danger" @click="remove(row)">删除</el-button>
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
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="save">保存并自动存证</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted, reactive } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listReport, createReport, deleteReport, listBatch } from '@/api'

const list = ref([])
const batches = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const form = reactive({ conclusion: 'qualified', pesticide_residue: '符合标准', heavy_metal: '符合标准' })

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
  dialogVisible.value = true
}

const save = async () => {
  if (!form.report_no || !form.batch) {
    ElMessage.warning('请填写报告编号并选择批次')
    return
  }
  await createReport(form)
  ElMessage.success('报告已保存并完成存证')
  dialogVisible.value = false
  load()
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
</style>