<template>
  <div class="page">
    <!-- 统计条 -->
    <el-row :gutter="12" class="stat-row">
      <el-col :span="6" v-for="s in statCards" :key="s.label">
        <el-card shadow="never" class="stat-card">
          <div class="stat-value">{{ s.value }}</div>
          <div class="stat-label">{{ s.label }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>操作审计日志</span>
          <div class="filters">
            <el-select v-model="query.action" placeholder="操作类型" clearable
                       size="small" style="width: 130px" @change="load">
              <el-option v-for="a in actions" :key="a.value"
                         :label="a.label" :value="a.value" />
            </el-select>
            <el-input v-model="query.operator" placeholder="操作人" clearable
                      size="small" style="width: 130px" @keyup.enter="load" />
            <el-input v-model="query.keyword" placeholder="详情关键词" clearable
                      size="small" style="width: 160px" @keyup.enter="load" />
            <el-button type="primary" size="small" @click="load">查询</el-button>
            <el-button size="small" @click="reset">重置</el-button>
          </div>
        </div>
      </template>

      <el-table :data="rows" v-loading="loading" stripe size="small">
        <el-table-column prop="created_at" label="时间" width="170">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="operator_name" label="操作人" width="110" />
        <el-table-column label="角色" width="100">
          <template #default="{ row }">
            <el-tag size="small" type="info">{{ roleLabel(row.operator_role) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="actionType(row.action)">
              {{ row.action_display }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="target_type" label="对象类型" width="100" />
        <el-table-column prop="target_no" label="对象编号" width="150" show-overflow-tooltip />
        <el-table-column prop="detail" label="详情" min-width="240" show-overflow-tooltip />
        <el-table-column prop="ip" label="来源IP" width="130" />
        <el-table-column label="结果" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.success ? 'success' : 'danger'">
              {{ row.success ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        class="pager"
        background
        layout="total, prev, pager, next"
        :total="total"
        :page-size="pageSize"
        :current-page="page"
        @current-change="onPage"
      />
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { listLog } from '@/api'
import { useUserStore } from '@/store/user'

const userStore = useUserStore()
const loading = ref(false)
const rows = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const query = reactive({ action: '', operator: '', keyword: '' })

const actions = [
  { value: 'login', label: '登录' },
  { value: 'create', label: '新增' },
  { value: 'update', label: '修改' },
  { value: 'delete', label: '删除' },
  { value: 'audit', label: '审核' },
  { value: 'issue', label: '签发' },
  { value: 'revoke', label: '作废' },
  { value: 'anchor', label: '存证' },
  { value: 'verify', label: '验真' },
  { value: 'match', label: '产销对接' }
]

const roleMap = {
  admin: '系统管理员', regulator: '监管人员',
  producer: '种植主体', buyer: '收购商'
}
const roleLabel = (r) => roleMap[r] || r || '-'

const actionType = (a) => ({
  login: 'info', create: 'success', update: 'warning', delete: 'danger',
  audit: 'primary', issue: 'success', revoke: 'danger', anchor: 'success',
  verify: 'info', match: 'primary'
}[a] || 'info')

const fmtTime = (s) => (s ? String(s).replace('T', ' ').slice(0, 19) : '')

// 本地统计（按当前已加载数据；完整统计以后端看板为准）
const statCards = computed(() => {
  const list = rows.value
  const cnt = (a) => list.filter((x) => x.action === a).length
  return [
    { label: '本页记录', value: list.length },
    { label: '签发/作废', value: cnt('issue') + cnt('revoke') },
    { label: '存证动作', value: cnt('anchor') },
    { label: '产销对接', value: cnt('match') }
  ]
})

const load = async () => {
  loading.value = true
  try {
    const params = { page: page.value }
    if (query.action) params.action = query.action
    if (query.operator) params.operator = query.operator
    if (query.keyword) params.keyword = query.keyword
    const res = await listLog(params)
    rows.value = res.results || res.data || []
    total.value = res.count ?? rows.value.length
  } finally {
    loading.value = false
  }
}

const onPage = (p) => {
  page.value = p
  load()
}

const reset = () => {
  query.action = ''
  query.operator = ''
  query.keyword = ''
  page.value = 1
  load()
}

onMounted(load)
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 12px; }
.stat-row { margin-bottom: 0; }
.stat-card { text-align: center; }
.stat-value { font-size: 22px; font-weight: 700; color: #303133; }
.stat-label { font-size: 12px; color: #909399; margin-top: 4px; }
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
}
.filters { display: flex; gap: 8px; align-items: center; }
.pager { margin-top: 12px; justify-content: flex-end; }
</style>
