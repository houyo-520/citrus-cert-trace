<template>
  <div>
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
          SHA-256 哈希存证，后端生成，扫码重算比对验真
        </el-descriptions-item>
        <el-descriptions-item label="追溯方式">合格证二维码 → 公众验真页</el-descriptions-item>
        <el-descriptions-item label="技术栈">
          前端 Vue3 + Element Plus ｜ 后端 Django + DRF ｜ 数据库 MySQL/SQLite
        </el-descriptions-item>
        <el-descriptions-item label="特色功能">产销供需对接，先验真再对接</el-descriptions-item>
      </el-descriptions>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import * as echarts from 'echarts'
import { getDashboard } from '@/api'

const barRef = ref()
const pieRef = ref()
const stats = ref({
  producer_count: 0, approved_producer_count: 0, batch_count: 0,
  certificate_count: 0, issued_count: 0, evidence_count: 0,
  supply_count: 0, demand_count: 0, total_weight: '0'
})

const cards = computed(() => [
  { label: '生产主体', value: stats.value.producer_count, color: '#409eff' },
  { label: '生产批次', value: stats.value.batch_count, color: '#67c23a' },
  { label: '已签发合格证', value: stats.value.issued_count, color: '#e6a23c' },
  { label: '存证记录', value: stats.value.evidence_count, color: '#0f5c3f' }
])

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
    stats.value = res.data
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