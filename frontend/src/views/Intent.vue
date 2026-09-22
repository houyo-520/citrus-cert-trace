<template>
  <div class="page">
    <el-card shadow="never">
      <template #header>
        <div class="head">
          <span>产销意向单</span>
          <div class="filters">
            <el-select v-model="query.status" placeholder="意向状态" clearable
                       size="small" style="width: 130px" @change="load">
              <el-option label="待确认" value="pending" />
              <el-option label="已接受" value="accepted" />
              <el-option label="已拒绝" value="rejected" />
              <el-option label="已完成" value="done" />
            </el-select>
            <el-button size="small" @click="load">刷新</el-button>
          </div>
        </div>
      </template>

      <el-table :data="rows" v-loading="loading" stripe size="small">
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column prop="supply_title" label="关联供需单" min-width="180"
                         show-overflow-tooltip />
        <el-table-column label="类型" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.supply_kind === 'supply' ? 'success' : 'warning'">
              {{ row.supply_kind === 'supply' ? '供应' : '求购' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="initiator_name" label="发起人" width="110" />
        <el-table-column label="意向数量" width="110">
          <template #default="{ row }">{{ row.quantity_kg }} kg</template>
        </el-table-column>
        <el-table-column label="意向单价" width="110">
          <template #default="{ row }">¥{{ row.price }}</template>
        </el-table-column>
        <el-table-column label="匹配度" width="150">
          <template #default="{ row }">
            <div class="score-cell">
              <el-progress :percentage="row.match_score" :stroke-width="10"
                           :color="scoreColor(row.match_score)" :show-text="false"
                           style="width: 80px" />
              <span class="score-num">{{ row.match_score }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)">
              {{ row.status_display }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="message" label="留言" min-width="140" show-overflow-tooltip />
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="showDetail(row)">
              详情
            </el-button>
            <template v-if="row.status === 'pending'">
              <el-button size="small" link type="success"
                         @click="respond(row, 'accepted')">接受</el-button>
              <el-button size="small" link type="danger"
                         @click="respond(row, 'rejected')">拒绝</el-button>
            </template>
            <el-button v-if="row.status === 'accepted'" size="small" link type="primary"
                       @click="respond(row, 'done')">完成</el-button>
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

    <!-- 匹配度评分明细 -->
    <el-dialog v-model="detailVisible" title="意向单详情与匹配度评分" width="640px">
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item label="关联供需单">{{ current.supply_title }}</el-descriptions-item>
        <el-descriptions-item label="发起人">{{ current.initiator_name }}</el-descriptions-item>
        <el-descriptions-item label="意向数量">{{ current.quantity_kg }} kg</el-descriptions-item>
        <el-descriptions-item label="意向单价">¥{{ current.price }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ current.status_display }}</el-descriptions-item>
        <el-descriptions-item label="综合匹配度">
          <el-tag :type="current.match_score >= 80 ? 'success'
            : current.match_score >= 60 ? 'warning' : 'danger'">
            {{ current.match_score }} / 100
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="留言" :span="2">{{ current.message || '—' }}</el-descriptions-item>
        <el-descriptions-item label="评分明细" :span="2">
          <span class="mono">{{ current.score_detail || '—' }}</span>
        </el-descriptions-item>
      </el-descriptions>

      <div class="factors" v-if="factors.length">
        <div class="factors-title">各因子得分</div>
        <div v-for="f in factors" :key="f.name" class="factor-row">
          <span class="factor-name">{{ f.name }}</span>
          <el-progress :percentage="Math.round(f.score / f.weight * 100)"
                       :stroke-width="12" :color="scoreColor(f.score / f.weight * 100)"
                       style="flex: 1" />
          <span class="factor-score">{{ f.score }}/{{ f.weight }}</span>
          <span class="factor-note">{{ f.note }}</span>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { listIntent, respondIntent } from '@/api'

const loading = ref(false)
const rows = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const query = reactive({ status: '' })

const detailVisible = ref(false)
const current = ref({})

const factors = computed(() => {
  // score_detail 是摘要字符串；详情页展示各因子需要后端返回结构
  // 这里用摘要字符串兜底，结构化的 factors 在推荐接口里返回
  return []
})

const scoreColor = (s) => (s >= 80 ? '#67C23A' : s >= 60 ? '#E6A23C' : '#F56C6C')
const statusType = (s) => ({
  pending: 'warning', accepted: 'success', rejected: 'danger', done: 'primary'
}[s] || 'info')

const load = async () => {
  loading.value = true
  try {
    const params = { page: page.value }
    if (query.status) params.status = query.status
    const res = await listIntent(params)
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

const respond = async (row, status) => {
  await respondIntent(row.id, status)
  ElMessage.success('已回应')
  load()
}

const showDetail = (row) => {
  current.value = row
  detailVisible.value = true
}

onMounted(load)
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 12px; }
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
}
.filters { display: flex; gap: 8px; }
.pager { margin-top: 12px; justify-content: flex-end; }
.score-cell { display: flex; align-items: center; gap: 8px; }
.score-num { font-weight: 600; font-size: 13px; }
.mono { font-family: Consolas, Monaco, monospace; font-size: 12px; }
.factors { margin-top: 16px; }
.factors-title { font-weight: 600; margin-bottom: 10px; }
.factor-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
  font-size: 12px;
}
.factor-name { width: 70px; color: #606266; }
.factor-score { width: 52px; text-align: right; font-weight: 600; }
.factor-note { width: 180px; color: #909399; }
</style>
