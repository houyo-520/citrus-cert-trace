<template>
  <div>
    <!-- 角色视角提示：不同角色看到不同统计口径 -->
    <el-alert :title="`当前视角：${stats.scope_label || '—'}`"
              :type="userStore.perms.can_view_all ? 'success' : 'warning'"
              :closable="false" show-icon style="margin-bottom: 16px" />

    <!-- ★ 风险预警条：有待办才出现，点开看明细并可直接跳转处理 -->
    <el-card v-if="alertData.total" shadow="never" class="alert-card">
      <div class="alert-head">
        <div class="alert-title">
          <el-icon class="alert-icon"><WarningFilled /></el-icon>
          风险预警
          <el-tag v-if="alertData.danger" type="danger" size="small" effect="dark">
            紧急 {{ alertData.danger }} 项
          </el-tag>
          <el-tag type="warning" size="small" effect="plain">
            共 {{ alertData.total }} 项待处理
          </el-tag>
        </div>
        <el-button link type="primary" @click="alertVisible = !alertVisible">
          {{ alertVisible ? '收起' : '展开明细' }}
        </el-button>
      </div>

      <div v-show="alertVisible" class="alert-body">
        <div v-for="a in alertData.alerts" :key="a.key" class="alert-item"
             :class="a.level">
          <div class="ai-left">
            <el-tag :type="levelTag(a.level)" size="small" effect="dark">
              {{ levelText(a.level) }}
            </el-tag>
            <div class="ai-text">
              <div class="ai-title">
                {{ a.title }}
                <span class="ai-count">× {{ a.count }}</span>
              </div>
              <div class="ai-desc">{{ a.desc }}</div>
            </div>
          </div>
          <el-button size="small" :type="levelTag(a.level)" plain
                     @click="goto(a.action_route)">
            {{ a.action_label }}
          </el-button>
        </div>
      </div>
    </el-card>

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

    <!-- ★ 看板增强：签发趋势 + 检查类型分布 -->
    <el-row :gutter="16" style="margin-top:16px">
      <el-col :span="12">
        <el-card shadow="never">
          <div class="card-title">
            近 6 个月签发趋势
            <el-tag size="small" type="info" effect="plain" style="margin-left:8px">
              共 {{ trendTotal }} 张
            </el-tag>
          </div>
          <div ref="trendRef" style="height:280px"></div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never">
          <div class="card-title">监督检查类型分布</div>
          <div ref="typeRef" style="height:280px"></div>
        </el-card>
      </el-col>
    </el-row>

    <!-- ★ 签发量 TOP 主体 + 数据质量指标 -->
    <el-row :gutter="16" style="margin-top:16px">
      <el-col :span="14">
        <el-card shadow="never">
          <div class="card-title">签发量 TOP 生产主体</div>
          <div ref="topRef" style="height:280px"></div>
        </el-card>
      </el-col>
      <el-col :span="10">
        <el-card shadow="never">
          <div class="card-title">数据质量指标</div>
          <div class="metric-grid">
            <div class="metric" v-for="m in metrics" :key="m.label">
              <div class="metric-val" :style="{ color: m.color }">{{ m.value }}</div>
              <div class="metric-lbl">{{ m.label }}</div>
              <div class="metric-tip">{{ m.tip }}</div>
            </div>
          </div>
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
          角色权限隔离 + 操作审计日志 + 产销智能匹配对接 + 风险预警聚合
        </el-descriptions-item>
      </el-descriptions>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, nextTick, computed } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { WarningFilled } from '@element-plus/icons-vue'
import { getDashboard, getAlerts } from '@/api'
import { useUserStore } from '@/store/user'

const router = useRouter()
const userStore = useUserStore()
const barRef = ref()
const pieRef = ref()
const inspRef = ref()
const trendRef = ref()
const typeRef = ref()
const topRef = ref()
const alertVisible = ref(false)
const alertData = ref({ total: 0, danger: 0, alerts: [] })

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

const trendTotal = computed(() =>
  (stats.value.issue_trend || []).reduce((a, b) => a + b.count, 0))

/**
 * 数据质量指标 —— 把「存证覆盖率」「验真活跃度」这类抽象概念量化。
 * 答辩时这几项比单纯的计数更有说服力：它们说明系统不只是"存了数据"，
 * 而是"数据真的被存证、真的被公众查询验证过"。
 */
const metrics = computed(() => {
  const s = stats.value
  const certTotal = s.certificate_count || 0
  const anchoredRate = certTotal
    ? Math.round((s.issued_count || 0) / certTotal * 100) : 0
  return [
    { label: '检测报告', value: s.report_count ?? 0, color: '#409eff',
      tip: `其中不合格 ${s.report_unqualified_count ?? 0} 份` },
    { label: '批次已存证', value: s.batch_anchored_count ?? 0, color: '#0f5c3f',
      tip: '批次信息已上链锚定' },
    { label: '公众验真次数', value: s.public_query_count ?? 0, color: '#e6a23c',
      tip: '免登录扫码查询累计' },
    { label: '产销已对接', value: s.matched_supply_count ?? 0, color: '#67c23a',
      tip: `平均匹配度 ${s.avg_match_score ?? 0} 分` },
    { label: '有效证占比', value: `${anchoredRate}%`, color: '#409eff',
      tip: '已签发 / 全部证' },
    { label: '已整改闭环', value: s.rectify_done_count ?? 0, color: '#67c23a',
      tip: '复查合格并恢复' }
  ]
})

const levelTag = (lv) => ({ danger: 'danger', warning: 'warning', info: 'info' }[lv] || 'info')
const levelText = (lv) => ({ danger: '紧急', warning: '关注', info: '提示' }[lv] || '提示')
const goto = (route) => { if (route) router.push(`/${route}`) }

/**
 * ★ 安全初始化图表
 * echarts.init(null) 会抛 "Cannot read properties of null (reading 'getAttribute')"，
 * 常见于 ① 元素在 v-if 分支里未渲染 ② 弹窗/切页后 DOM 已卸载 ③ onMounted 时布局未就绪。
 * 这里统一做「元素存在 + 未销毁 + 已有实例则复用」三重守卫。
 */
const safeInit = (el) => {
  if (!el) return null
  const existed = echarts.getInstanceByDom(el)
  if (existed && !existed.isDisposed()) return existed
  return echarts.init(el)
}

const renderBar = () => {
  const chart = safeInit(barRef.value)
  if (!chart) return
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
  const chart = safeInit(pieRef.value)
  if (!chart) return
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
          itemStyle: { color: '#f56c6c' } },
        { value: stats.value.revoked_count ?? 0, name: '已作废',
          itemStyle: { color: '#c45656' } }
      ].filter((d) => d.value > 0),
      label: { formatter: '{b}: {c}' }
    }]
  })
}

/** 近 6 个月签发趋势（折线 + 面积，直观看出业务节奏） */
const renderTrend = () => {
  const chart = safeInit(trendRef.value)
  if (!chart) return
  const trend = stats.value.issue_trend || []
  chart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 44, right: 20, top: 26, bottom: 34 },
    xAxis: {
      type: 'category',
      data: trend.map((t) => t.month),
      boundaryGap: false,
      axisLabel: { fontSize: 11 }
    },
    yAxis: { type: 'value', minInterval: 1 },
    series: [{
      type: 'line',
      smooth: true,
      symbolSize: 7,
      data: trend.map((t) => t.count),
      itemStyle: { color: '#409eff' },
      lineStyle: { width: 3 },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(64,158,255,0.45)' },
          { offset: 1, color: 'rgba(64,158,255,0.02)' }
        ])
      },
      label: { show: true, position: 'top', fontSize: 11 }
    }]
  })
}

/** 检查类型分布（玫瑰饼图） */
const renderType = () => {
  const chart = safeInit(typeRef.value)
  if (!chart) return
  const dist = stats.value.inspection_type_dist || []
  const palette = ['#409eff', '#e6a23c', '#f56c6c', '#0f5c3f']
  chart.setOption({
    tooltip: { trigger: 'item' },
    legend: { bottom: 0 },
    series: [{
      type: 'pie',
      radius: ['34%', '66%'],
      center: ['50%', '46%'],
      data: dist.length
        ? dist.map((d, i) => ({ ...d, itemStyle: { color: palette[i % palette.length] } }))
        : [{ value: 0, name: '暂无检查记录', itemStyle: { color: '#dcdfe6' } }],
      label: { formatter: '{b}: {c}' }
    }]
  })
}

/** 签发量 TOP 主体（横向柱图，名称可能较长故留足左边距） */
const renderTop = () => {
  const chart = safeInit(topRef.value)
  if (!chart) return
  const top = (stats.value.top_producers || []).slice().reverse()
  chart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 130, right: 34, top: 20, bottom: 26 },
    xAxis: { type: 'value', minInterval: 1 },
    yAxis: {
      type: 'category',
      data: top.length ? top.map((t) => t.name) : ['暂无数据'],
      axisLabel: { fontSize: 11, width: 116, overflow: 'truncate' }
    },
    series: [{
      type: 'bar',
      data: top.length ? top.map((t) => t.count) : [0],
      itemStyle: { color: '#0f5c3f' },
      barWidth: '54%',
      label: { show: true, position: 'right', fontSize: 11 }
    }]
  })
}

const renderInsp = () => {
  const chart = safeInit(inspRef.value)
  if (!chart) return
  const s = stats.value
  chart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 20, top: 30, bottom: 40 },
    xAxis: {
      type: 'category',
      data: ['检查总数', '不合格', '待/整改中', '已逾期', '已整改'],
      axisLabel: { fontSize: 12 }
    },
    yAxis: { type: 'value', minInterval: 1 },
    series: [{
      type: 'bar',
      data: [
        { value: s.inspection_count ?? 0, itemStyle: { color: '#409eff' } },
        { value: s.inspection_unqualified_count ?? 0, itemStyle: { color: '#f56c6c' } },
        { value: s.rectify_pending_count ?? 0, itemStyle: { color: '#e6a23c' } },
        { value: s.rectify_overdue_count ?? 0, itemStyle: { color: '#c45656' } },
        { value: s.rectify_done_count ?? 0, itemStyle: { color: '#67c23a' } }
      ],
      barWidth: '46%'
    }]
  })
}

const renderAll = () => {
  renderBar()
  renderPie()
  renderTrend()
  renderType()
  renderTop()
  renderInsp()
}

const chartEls = () => [barRef, pieRef, inspRef, trendRef, typeRef, topRef]

const onResize = () => {
  chartEls()
    .map((r) => (r.value ? echarts.getInstanceByDom(r.value) : null))
    .filter((c) => c && !c.isDisposed())
    .forEach((c) => c.resize())
}

onMounted(async () => {
  // 统计与预警并行拉取：两者互不依赖，串行会白等一个 RTT
  const [statsRes, alertRes] = await Promise.allSettled([getDashboard(), getAlerts()])
  if (statsRes.status === 'fulfilled') {
    stats.value = statsRes.value.data || {}   // 包装体 {code,message,data}
  }
  if (alertRes.status === 'fulfilled') {
    alertData.value = alertRes.value.data || { total: 0, danger: 0, alerts: [] }
    // 有紧急预警时默认展开，别让用户还要多点一次才能看到问题
    alertVisible.value = alertData.value.danger > 0
  }
  // 等 DOM 完成布局再初始化，规避高度为 0 / 元素未挂载
  await nextTick()
  renderAll()
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  // 主动销毁实例，防止切页后残留导致下次 init 拿到已 dispose 的容器
  chartEls().forEach((r) => {
    if (!r.value) return
    const c = echarts.getInstanceByDom(r.value)
    if (c && !c.isDisposed()) c.dispose()
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

/* 风险预警 */
.alert-card { margin-bottom: 16px; border-left: 4px solid #e6a23c; }
.alert-head {
  display: flex; justify-content: space-between; align-items: center;
}
.alert-title {
  display: flex; align-items: center; gap: 8px;
  font-size: 15px; font-weight: 600;
}
.alert-icon { color: #e6a23c; font-size: 18px; }
.alert-body { margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.alert-item {
  display: flex; justify-content: space-between; align-items: center;
  gap: 12px; padding: 10px 12px; border-radius: 6px; background: #fafafa;
  border-left: 3px solid #dcdfe6;
}
.alert-item.danger { border-left-color: #f56c6c; background: #fef0f0; }
.alert-item.warning { border-left-color: #e6a23c; background: #fdf6ec; }
.alert-item.info { border-left-color: #409eff; background: #ecf5ff; }
.ai-left { display: flex; align-items: center; gap: 10px; min-width: 0; }
.ai-text { min-width: 0; }
.ai-title { font-size: 13.5px; font-weight: 600; color: #303133; }
.ai-count { color: #f56c6c; margin-left: 4px; }
.ai-desc { font-size: 12px; color: #909399; margin-top: 2px; }

/* 数据质量指标网格 */
.metric-grid {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px;
}
.metric {
  text-align: center; padding: 12px 6px; background: #fafafa; border-radius: 6px;
}
.metric-val { font-size: 22px; font-weight: 700; }
.metric-lbl { font-size: 12px; color: #606266; margin-top: 4px; }
.metric-tip { font-size: 11px; color: #a8abb2; margin-top: 2px; }
</style>