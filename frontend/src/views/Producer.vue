<template>
  <el-card shadow="never">
    <div class="toolbar">
      <el-input v-model="keyword" placeholder="搜索主体名称/联系人" style="width:240px"
                clearable @keyup.enter="load" />
      <el-button type="primary" @click="load">查询</el-button>
      <!-- 新增主体：种植主体可录入自己的主体信息；收购商只读 -->
      <el-button v-if="userStore.perms.can_write_production" type="success"
                 @click="openDialog()">新增主体</el-button>
      <el-tag v-if="!userStore.perms.can_view_all" type="info" effect="plain">
        {{ userStore.isBuyer ? '仅显示已审核通过的主体' : '仅显示我的主体数据' }}
      </el-tag>
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
      <!-- ★ 主体信用评分：监管快速识别高风险主体，点击查看扣分明细 -->
      <el-table-column v-if="userStore.perms.can_audit" label="信用评分" width="130">
        <template #default="{ row }">
          <el-tag v-if="row.credit_grade" :type="creditTagType(row.credit_grade)"
                  size="small" effect="light" @click="showCredit(row)">
            {{ row.credit_grade }} {{ row.credit_score }}
          </el-tag>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button v-if="userStore.perms.can_write_production" link type="primary"
                     @click="openDialog(row)">编辑</el-button>
          <!-- 审核按钮仅监管/管理员可见（后端同步拦截，防绕过） -->
          <el-button v-if="userStore.perms.can_audit && row.audit_status !== 'approved'"
                     link type="success" @click="audit(row, 'approved')">通过</el-button>
          <el-button v-if="userStore.perms.can_audit && row.audit_status !== 'rejected'"
                     link type="warning" @click="audit(row, 'rejected')">驳回</el-button>
          <el-button v-if="userStore.perms.can_write_production" link type="danger"
                     @click="remove(row)">删除</el-button>
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

    <!-- ★ 信用评分明细（负向扣分制，逐条可回溯） -->
    <el-dialog v-model="creditVisible" :title="`信用评分 - ${creditData.producer_name || ''}`" width="620px">
      <div v-if="creditLoading" v-loading="creditLoading" style="min-height:120px" />
      <template v-else>
        <div class="credit-head">
          <div class="credit-score" :class="'grade-' + creditData.grade">
            <span class="num">{{ creditData.score }}</span>
            <span class="label">/ 100 · {{ creditData.grade }}</span>
          </div>
          <div class="credit-stats">
            <div>作废证 {{ creditData.stats?.revoked_certificates ?? 0 }}</div>
            <div>检查不合格 {{ creditData.stats?.unqualified_inspections ?? 0 }}</div>
            <div>整改逾期 {{ creditData.stats?.overdue_rectifies ?? 0 }}</div>
            <div>不合格报告 {{ creditData.stats?.unqualified_reports ?? 0 }}</div>
          </div>
        </div>
        <el-divider content-position="left">扣分明细</el-divider>
        <el-table :data="creditData.deductions" size="small" border>
          <el-table-column prop="item" label="扣分项" min-width="120" />
          <el-table-column prop="points" label="分值" width="90">
            <template #default="{ row }">
              <span class="deduct">{{ row.points }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="ref" label="依据" min-width="120" />
        </el-table>
        <el-empty v-if="!creditData.deductions?.length" description="无扣分项，信用良好" :image-size="60" />
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted, reactive } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listProducer, createProducer, updateProducer, deleteProducer, auditProducer, getProducerCredit } from '@/api'
import { useUserStore } from '@/store/user'

const userStore = useUserStore()

const list = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 10
const keyword = ref('')
const loading = ref(false)
const dialogVisible = ref(false)
const form = reactive({})

// ★ 信用评分弹窗
const creditVisible = ref(false)
const creditLoading = ref(false)
const creditData = reactive({ score: 0, grade: '', deductions: [], stats: {} })

const statusType = (s) => ({ pending: 'warning', approved: 'success', rejected: 'danger' }[s] || 'info')

const creditTagType = (g) => ({ 优秀: 'success', 良好: 'primary', 一般: 'warning', 警示: 'danger' }[g] || 'info')

const showCredit = async (row) => {
  creditVisible.value = true
  creditLoading.value = true
  try {
    const res = await getProducerCredit(row.id)
    Object.assign(creditData, res.data || {})
  } finally {
    creditLoading.value = false
  }
}

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
.muted { color: #c0c4cc; }
.credit-head { display: flex; gap: 24px; align-items: center; margin-bottom: 8px; }
.credit-score { display: flex; align-items: baseline; gap: 6px; }
.credit-score .num { font-size: 36px; font-weight: 700; }
.credit-score .label { color: #909399; }
.credit-score.grade-优秀 .num { color: #67c23a; }
.credit-score.grade-良好 .num { color: #409eff; }
.credit-score.grade-一般 .num { color: #e6a23c; }
.credit-score.grade-警示 .num { color: #f56c6c; }
.credit-stats { display: flex; flex-wrap: wrap; gap: 12px; color: #606266; font-size: 13px; }
.deduct { color: #f56c6c; font-weight: 600; }
</style>