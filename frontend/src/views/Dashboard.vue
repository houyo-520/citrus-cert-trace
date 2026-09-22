<template>
  <div>
    <!-- 角色视角提示：不同角色看到不同统计口径 -->
    <el-alert :title="`当前视角：${stats.scope_label || '—'}`"
              :type="userStore.perms.can_view_all ? 'success' : 'warning'"
              :closable="false" show-icon style="margin-bottom: 16px" />

    <el-row :gutter="16">
      <el-col :span="6" v-for="card in cards" :key="card.label">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">{{ card.label }}</div>
          <div class="stat-value" :style="{ color: card.color }">{{ card.value }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top:16px">
      <el-col :span="12">
        <el-card shadow="never">
          <div class="card-title">各模块数据统计</div>
          <div ref="barRef" style="height:300px"></div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never">
          <div class="card-title">合格证状态分布</div>
          <div ref="pieRef" style="height:300px"></div>
        </el-card>
      </el-col>
    </el-row>

    <!-- ★ 监管执法闭环态势（仅监管/管理员可见） -->
    <el-row :gutter="16" style="margin-top:16px" v-if="userStore.perms.can_view_all">
      <el-col :span="24">
        <el-card shadow="never">
          <div class="card-title">
            监督检查与执法闭环
            <el-tag size="small" type="warning" effect="plain" style="margin-left:8px">
              逾期 {{ stats.rectify_overdue_count ?? 0 }} 项待处理
            </el-tag>
          </div>
          <el-row :gutter="16">
            <el-col :span="6" v-for="c in inspCards" :key="c.label">
              <div class="mini-stat">
                <div class="mini-num" :style="{ color: c.color }">{{ c.value }}</div>
                <div class="mini-lbl">{{ c.label }}</div>
              </div>
            </el-col>
          </el-row>
          <div ref="inspRef" style="height:260px; margin-top:12px"></div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" style="margin-top:16px">
      <div class="card-title">系统说明</div>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="存证机制">
          双层校验：本地 SHA-256 重算比对 + 联盟链 FISCO BCOS 链上核验
        </el-descriptions-item>
        <el-descriptions-item label="追溯方式">合格证二维码 → 公众验真页</el-descriptions-item>
        <el-descriptions-item label="编号规则">
          19 位：区域码(6) + 日期(8) + 流水(5)，签发时分配（草稿不占号）
        </el-descriptions-item>
        <el-descriptions-item label="监管闭环">
          现场检查 → 结论上链 → 整改期限 → 复查 → 合格证暂停/恢复
        </el-descriptions-item>
        <el-descriptions-item label="技术栈">
          前端 Vue3 + Element Plus ｜ 后端 Django + DRF ｜ 联盟链 FISCO BCOS
        </el-descriptions-item>
        <el-descriptions-item label="特色功能">
          角色权限隔离 + 操作审计日志 + 产销智能匹配对接
        </el-descriptions-item>
      </el-descriptions>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import * as echarts from 'echarts'
import { getDashboard } from '@/api'
import { useUserStore } from '@/store/user'

const userStore = useUserStore()
const barRef = ref()
const pieRef = ref()
const inspRef = ref()
const stats = ref({
  producer_count: 0, approved_producer_count: 0, batch_count: 0,
  certificate_count: 0, issued_count: 0, evidence_count: 0,
  supply_count: 0, demand_count: 0, total_weight: '0', scope_label: ''
})

const cards = computed(() => {
  const s = stats.value
  const base = [
    { label: '生产主体', value: s.producer_count, color: '#409eff' },
    { label: '生产批次', value: s.batch_count, color: '#67c23a' },
    { label: '已签发合格证', value: s.issued_count, color: '#e6a23c' }
  ]
  // 监管视角额外展示待审核数量；其他角色展示自己的对接情况
  if (userStore.perms.can_view_all) {
    base.push({ label: '待审核主体', value: s.pending_audit_count ?? 0, color: '#f56c6c' })
  } else {
    base.push({ label: '产销意向单', value: s.intent_count ?? 0, color: '#0f5c3f' })
  }
  return base
})

const inspCards = computed(() => {
  const s = stats.value
  return [
    { label: '检查总数', value: s.inspection_count ?? 0, color: '#409eff' },
    { label: '检查不合格', value: s.inspection_unqualified_count ?? 0, color: '#f56c6c' },
    { label: '整改中/待整改', value: s.rectify_pending_count ?? 0, color: '#e6a23c' },
    { label: '罚款合计(元)', value: s.penalty_total ?? '0.00', color: '#303133' }
  ]
})

const renderBar = () => {
  const chart = echarts.init(barRef.value)
  chart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 20, top: 30, bottom: 40 },
    xAxis: {
      type: 'category',
      data: ['生产主体', '批次', '合格证', '存证', '供需', '检查'],
      axisLabel: { fontSize: 12 }
    },
    yAxis: { type: 'value' },
    series: [{
      type: 'bar',
      data: [
        stats.value.producer_count,
        stats.value.batch_count,
        stats.value.certificate_count,
        stats.value.evidence_count,
        stats.value.supply_count + stats.value.demand_count,
        stats.value.inspection_count ?? 0
      ],
      itemStyle: { color: '#0f5c3f' },
      barWidth: '46%'
    }]
  })
}

const renderPie = () => {
  const chart = echarts.init(pieRef.value)
  chart.setOption({
    tooltip: { trigger: 'item' },
    legend: { bottom: 0 },
    series: [{
      type: 'pie',
      radius: ['42%', '68%'],
      data: [
        { value: stats.value.issued_count, name: '已签发',
          itemStyle: { color: '#67c23a' } },
        { value: stats.value.draft_cert_count ?? 0, name: '草稿',
          itemStyle: { color: '#909399' } },
        { value: stats.value.pending_cert_count ?? 0, name: '待审核',
          itemStyle: { color: '#e6a23c' } },
        { value: stats.value.suspended_count ?? 0, name: '暂停使用',
          itemStyle: { color: '#f56c6c' } }
      ].filter((d) => d.value > 0),
      label: { formatter: '{b}: {c}' }
    }]
  })
}

const renderInsp = () => {
  if (!inspRef.value) return
  const chart = echarts.init(inspRef.value)
  const s = stats.value
  chart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 20, top: 30, bottom: 40 },
    xAxis: {
      type: 'category',
      data: ['检查总数', '不合格', '待/整改中', '已逾期', '已整改'],
      axisLabel: { fontSize: 12 }
    },
    yAxis: { type: 'value' },
    series: [{
      type: 'bar',
      data: [
        { value: s.inspection_count ?? 0, itemStyle: { color: '#409eff' } },
        { value: s.inspection_unqualified_count ?? 0, itemStyle: { color: '#f56c6c' } },
        { value: s.rectify_pending_count ?? 0, itemStyle: { color: '#e6a23c' } },
        { value: s.rectify_overdue_count ?? 0, itemStyle: { color: '#c45656' } },
        { value: (s.inspection_count ?? 0) - (s.inspection_unqualified_count ?? 0),
          itemStyle: { color: '#67c23a' } }
      ],
      barWidth: '46%'
    }]
  })
}

onMounted(async () => {
  try {
    const res = await getDashboard()
    stats.value = res.data.data || res.data
  } catch (e) {}
  renderBar()
  renderPie()
  renderInsp()
  window.addEventListener('resize', () => {
    echarts.getInstanceByDom(barRef.value)?.resize()
    echarts.getInstanceByDom(pieRef.value)?.resize()
    if (inspRef.value) echarts.getInstanceByDom(inspRef.value)?.resize()
  })
})
</script>

<style scoped>
.stat-card { text-align: center; }
.stat-label { font-size: 13px; color: #909399; }
.stat-value { font-size: 28px; font-weight: 600; margin-top: 6px; }
.card-title { font-size: 15px; font-weight: 600; margin-bottom: 12px; }
.mini-stat {
  text-align: center;
  padding: 10px 0;
  background: #fafafa;
  border-radius: 6px;
}
.mini-num { font-size: 22px; font-weight: 700; }
.mini-lbl { font-size: 12px; color: #909399; margin-top: 4px; }
</style>
