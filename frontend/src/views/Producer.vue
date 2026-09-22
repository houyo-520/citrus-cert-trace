<template>
  <el-card shadow="never">
    <div class="toolbar">
      <el-input v-model="keyword" placeholder="搜索主体名称/联系人" style="width:240px"
                clearable @keyup.enter="load" />
      <el-button type="primary" @click="load">查询</el-button>
      <el-button type="success" @click="openDialog()">新增主体</el-button>
    </div>

    <el-table :data="list" v-loading="loading" border stripe>
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="name" label="主体名称" min-width="180" />
      <el-table-column prop="contact" label="联系人" width="100" />
      <el-table-column prop="phone" label="联系电话" width="130" />
      <el-table-column prop="region" label="所属区域" min-width="150" />
      <el-table-column label="审核状态" width="110">
        <template #default="{ row }">
          <el-tag :type="statusType(row.audit_status)" size="small">
            {{ row.audit_status_display }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openDialog(row)">编辑</el-button>
          <el-button link type="success" v-if="row.audit_status !== 'approved'"
                     @click="audit(row, 'approved')">通过</el-button>
          <el-button link type="warning" v-if="row.audit_status !== 'rejected'"
                     @click="audit(row, 'rejected')">驳回</el-button>
          <el-button link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      style="margin-top:16px; justify-content:flex-end"
      layout="total, prev, pager, next"
      :total="total" :page-size="pageSize" :current-page="page"
      @current-change="onPage"
    />

    <el-dialog v-model="dialogVisible" :title="form.id ? '编辑主体' : '新增主体'" width="560px">
      <el-form :model="form" label-width="110px">
        <el-form-item label="主体名称" required>
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="信用代码">
          <el-input v-model="form.credit_code" />
        </el-form-item>
        <el-form-item label="联系人" required>
          <el-input v-model="form.contact" />
        </el-form-item>
        <el-form-item label="联系电话" required>
          <el-input v-model="form.phone" />
        </el-form-item>
        <el-form-item label="所属区域">
          <el-input v-model="form.region" />
        </el-form-item>
        <el-form-item label="产地地址">
          <el-input v-model="form.address" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted, reactive } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listProducer, createProducer, updateProducer, deleteProducer, auditProducer } from '@/api'

const list = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 10
const keyword = ref('')
const loading = ref(false)
const dialogVisible = ref(false)
const form = reactive({})

const statusType = (s) => ({ pending: 'warning', approved: 'success', rejected: 'danger' }[s] || 'info')

const load = async () => {
  loading.value = true
  try {
    const res = await listProducer({ page: page.value, search: keyword.value })
    list.value = res.results || res.data || []
    total.value = res.count ?? list.value.length
  } finally {
    loading.value = false
  }
}

const onPage = (p) => { page.value = p; load() }

const openDialog = (row) => {
  Object.keys(form).forEach((k) => delete form[k])
  if (row) Object.assign(form, row)
  dialogVisible.value = true
}

const save = async () => {
  if (!form.name || !form.contact || !form.phone) {
    ElMessage.warning('请填写主体名称、联系人、联系电话')
    return
  }
  if (form.id) {
    await updateProducer(form.id, form)
  } else {
    await createProducer(form)
  }
  ElMessage.success('保存成功')
  dialogVisible.value = false
  load()
}

const audit = async (row, status) => {
  await auditProducer(row.id, status)
  ElMessage.success('审核完成')
  load()
}

const remove = async (row) => {
  await ElMessageBox.confirm(`确认删除「${row.name}」？`, '提示', { type: 'warning' })
  await deleteProducer(row.id)
  ElMessage.success('已删除')
  load()
}

onMounted(load)
</script>

<style scoped>
.toolbar { display: flex; gap: 10px; margin-bottom: 14px; }
</style>