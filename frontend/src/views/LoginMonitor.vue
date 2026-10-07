<template>
  <div class="page">
    <!-- 统计卡片 -->
    <el-row :gutter="12" class="stat-row">
      <el-col :xs="12" :sm="8" :md="4" v-for="s in statCards" :key="s.label">
        <el-card shadow="never" class="stat-card">
          <div class="stat-value" :style="{ color: s.color }">{{ s.value }}</div>
          <div class="stat-label">{{ s.label }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 异地登录告警 -->
    <el-card v-if="summary.anomaly && summary.anomaly.length" shadow="never"
             class="anomaly-card">
      <template #header>
        <div class="card-head">
          <span>
            <el-icon color="#e6a23c"><WarningFilled /></el-icon>
            异地登录告警（{{ summary.anomaly.length }} 个账号）
          </span>
          <span class="head-hint">同一账号在多个城市登录，请人工研判</span>
        </div>
      </template>
      <el-table :data="summary.anomaly" size="small" stripe>
        <el-table-column prop="username" label="账号" min-width="120" />
        <el-table-column label="登录城市数" width="140">
          <template #default="{ row }">
            <el-tag type="warning" size="small">{{ row.place_count }} 个城市</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 登录历史表格 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>登录历史</span>
          <div class="filters">
            <el-input v-model="query.username" placeholder="账号" clearable
                      size="small" style="width: 120px" @keyup.enter="load" />
            <el-input v-model="query.ip" placeholder="IP" clearable
                      size="small" style="width: 130px" @keyup.enter="load" />
            <el-input v-model="query.location" placeholder="地点" clearable
                      size="small" style="width: 120px" @keyup.enter="load" />
            <el-select v-model="query.success" placeholder="结果" clearable
                       size="small" style="width: 100px" @change="load">
              <el-option label="成功" value="1" />
              <el-option label="失败" value="0" />
            </el-select>
            <el-button type="primary" size="small" @click="load">查询</el-button>
            <el-button size="small" @click="reset">重置</el-button>
          </div>
        </div>
      </template>

      <el-table :data="rows" v-loading="loading" stripe size="small">
        <el-table-column prop="login_at" label="时间" width="170">
          <template #default="{ row }">{{ fmtTime(row.login_at) }}</template>
        </el-table-column>
        <el-table-column prop="username" label="账号" width="110" />
        <el-table-column label="角色" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="roleTag(row.role)" effect="plain">
              {{ row.role_display || roleLabel(row.role) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="来源IP" width="140">
          <template #default="{ row }">
            <span class="mono">{{ row.ip }}</span>
            <el-tag v-if="row.is_internal" size="small" type="info"
                    style="margin-left: 4px">内网</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="地理位置" width="150">
          <template #default="{ row }">
            <el-tag size="small" v-if="row.location && row.location !== '未知地区'"
                    type="success" effect="plain">{{ row.location }}</el-tag>
            <span v-else class="text-dim">{{ row.location || '—' }}</span>
            <span v-if="row.isp" class="text-dim"> {{ row.isp }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="user_agent" label="设备/浏览器" min-width="150"
                         show-overflow-tooltip />
        <el-table-column label="结果" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.success ? 'success' : 'danger'">
              {{ row.success ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="fail_reason" label="失败原因" width="140"
                         show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.fail_reason" class="text-danger">{{ row.fail_reason }}</span>
            <span v-else>—</span>
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
import { WarningFilled } from '@element-plus/icons-vue'
import { listLoginMonitor, loginMonitorSummary } from '@/api'

const loading = ref(false)
const rows = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const query = reactive({ username: '', ip: '', location: '', success: '' })
const summary = ref({
  total: 0, success_count: 0, fail_count: 0, active_users: 0,
  anomaly: [], ip_distribution: [], fail_targets: []
})

const roleMap = {
  admin: '系统管理员', regulator: '监管人员',
  producer: '种植主体', buyer: '收购商'
}
const roleLabel = (r) => roleMap[r] || r || '-'
const roleTag = (r) => ({
  admin: 'danger', regulator: 'warning', producer: 'primary', buyer: 'success'
}[r] || 'info')

const statCards = computed(() => [
  { label: '登录总次数', value: summary.value.total, color: '#303133' },
  { label: '成功', value: summary.value.success_count, color: '#67c23a' },
  { label: '失败', value: summary.value.fail_count, color: '#f56c6c' },
  { label: '活跃账号', value: summary.value.active_users, color: '#E8743B' },
  { label: '异地登录账号', value: (summary.value.anomaly || []).length, color: '#e6a23c' },
  { label: '来源IP数', value: (summary.value.ip_distribution || []).length, color: '#909399' }
])

const fmtTime = (s) => (s ? String(s).replace('T', ' ').slice(0, 19) : '')

const loadSummary = async () => {
  try {
    const res = await loginMonitorSummary()
    summary.value = res.data || {}
  } catch (e) { /* 汇总失败不阻塞列表 */ }
}

const load = async () => {
  loading.value = true
  try {
    const params = { page: page.value }
    if (query.username) params.username = query.username
    if (query.ip) params.ip = query.ip
    if (query.location) params.location = query.location
    if (query.success !== '') params.success = query.success
    const res = await listLoginMonitor(params)
    rows.value = res.results || res.data || []
    total.value = res.count ?? rows.value.length
  } finally {
    loading.value = false
  }
}

const onPage = (p) => { page.value = p; load() }
const reset = () => {
  query.username = ''; query.ip = ''; query.location = ''; query.success = ''
  page.value = 1
  load()
}

onMounted(() => { loadSummary(); load() })
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 12px; }
.stat-row { margin-bottom: 0; }
.stat-card { text-align: center; }
.stat-value { font-size: 22px; font-weight: 700; }
.stat-label { font-size: 12px; color: #909399; margin-top: 4px; }
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
  gap: 12px;
  flex-wrap: wrap;
}
.head-hint { font-size: 12px; color: #909399; font-weight: 400; }
.filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.pager { margin-top: 12px; justify-content: flex-end; }
.mono { font-family: 'Consolas', 'Monaco', monospace; }
.text-dim { color: #909399; font-size: 12px; }
.text-danger { color: #f56c6c; }
.anomaly-card { border-left: 3px solid #e6a23c; }
</style>