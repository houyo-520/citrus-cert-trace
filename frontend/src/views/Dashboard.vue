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
          <div class="card-title">产销供需分布</div>
          <div ref="pieRef" style="height:300px"></div>
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

const renderBar = () => {
  const chart = echarts.init(barRef.value)
  chart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 20, top: 30, bottom: 40 },
    xAxis: {
      type: 'category',
      data: ['生产主体', '批次', '检测报告', '合格证', '存证', '供需'],
      axisLabel: { fontSize: 12 }
    },
    yAxis: { type: 'value' },
    series: [{
      type: 'bar',
      data: [
        stats.value.producer_count,
        stats.value.batch_count,
        stats.value.batch_count,
        stats.value.certificate_count,
        stats.value.evidence_count,
        stats.value.supply_count + stats.value.demand_count
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
        { value: stats.value.supply_count, name: '供应信息', itemStyle: { color: '#67c23a' } },
        { value: stats.value.demand_count, name: '求购信息', itemStyle: { color: '#e6a23c' } }
      ],
      label: { formatter: '{b}: {c}' }
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
  window.addEventListener('resize', () => {
    echarts.getInstanceByDom(barRef.value)?.resize()
    echarts.getInstanceByDom(pieRef.value)?.resize()
  })
})
</script>

<style scoped>
.stat-card { text-align: center; }
.stat-label { font-size: 13px; color: #909399; }
.stat-value { font-size: 28px; font-weight: 600; margin-top: 6px; }
</style>
