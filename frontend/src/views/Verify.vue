<template>
  <div class="verify-page">
    <div class="verify-header">
      <h1><el-icon><Orange /></el-icon> 柑橘合格证追溯验真</h1>
      <p>输入或扫描合格证编号，查看农产品从生产到流通的全链路存证信息</p>
    </div>

    <div class="verify-search">
      <el-input v-model="certNo" placeholder="请输入合格证编号" size="large"
                clearable @keyup.enter="doVerify">
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-button type="primary" size="large" :loading="loading" @click="doVerify">
        查询验真
      </el-button>
    </div>

    <div class="quick-tips">
      <span class="text-muted">示例编号：</span>
      <el-tag v-for="n in samples" :key="n" class="sample" @click="quick(n)">{{ n }}</el-tag>
    </div>

    <div v-if="result" class="verify-result">
      <!-- ★ 动态防伪水印：铺在结果区底层，截图泄露可溯源 -->
      <div v-if="result.watermark" class="watermark-layer" aria-hidden="true">
        <span v-for="i in 6" :key="i" class="wm-text">{{ result.watermark }}</span>
      </div>

      <!-- ===== 验真结论：放最顶部，一眼可见 ===== -->
      <!--
        ★ 第四轮审计修复（FRONTEND-VERDICT-IGNORES-ANCHOR，中高）：
          原判据只看 result.trusted —— 但后端在「链降级/未上链」时也会返回
          trusted=true（代表「本地摘要一致」），于是页面顶部绿色大条写
          「验真通过 · 数据可信」，下面第二层却是红色「不通过」，自相矛盾。
          现在绿色（数据可信）需要 **trusted && anchored** 同时成立；
          只 trusted 未 anchored 时显示为「中性/待核验」状态。
      -->
      <div class="verdict" :class="verdictClass">
        <div class="verdict-icon">
          <el-icon :size="34">
            <component :is="verdictIcon" />
          </el-icon>
        </div>
        <div class="verdict-text">
          <div class="verdict-title">{{ verdictTitle }}</div>
          <div class="verdict-sub">{{ result.verify_message }}</div>
        </div>
      </div>

      <!-- ===== ★ 追溯链时间轴：一件柑橘的旅程 ===== -->
      <div v-if="timeline.length" class="tl-panel">
        <div class="tl-head">
          <div class="tl-head-left">
            <el-icon><Guide /></el-icon>
            <span>全链路追溯</span>
          </div>
          <div class="tl-head-right">
            <el-tag size="small" type="info" effect="plain">
              {{ timeline.length }} 个环节
            </el-tag>
            <el-tag v-if="timelineSummary.anchored_nodes" size="small"
                    type="success" effect="plain">
              {{ timelineSummary.anchored_nodes }} 项已上链
            </el-tag>
          </div>
        </div>

        <div class="timeline">
          <div v-for="(n, i) in timeline" :key="n.key + i"
               class="tl-item" :class="[`lv-${n.level}`, { done: n.done }]">
            <!-- 轴：圆点 + 连接线 -->
            <div class="tl-axis">
              <div class="tl-dot">
                <el-icon :size="15"><component :is="n.icon" /></el-icon>
              </div>
              <div v-if="i < timeline.length - 1" class="tl-line"></div>
            </div>
            <!-- 内容 -->
            <div class="tl-content">
              <div class="tl-row1">
                <span class="tl-title">{{ n.title }}</span>
                <span class="tl-time">{{ n.time }}</span>
              </div>
              <div class="tl-desc">{{ n.desc }}</div>
              <div v-if="n.detail && n.detail.length" class="tl-detail">
                <div v-for="(d, di) in n.detail" :key="di" class="tl-drow">
                  <span class="tl-dk">{{ d[0] }}</span>
                  <span class="tl-dv">{{ d[1] }}</span>
                </div>
              </div>
              <!-- 链上锚点：证明这个环节被固化在联盟链上 -->
              <div v-if="n.anchor" class="tl-anchor">
                <el-icon><Lock /></el-icon>
                <span v-if="n.anchor.anchored">
                  已上链 · 区块 #{{ n.anchor.block_height }} ·
                  <span class="hash-text">{{ (n.anchor.tx_hash || '').slice(0, 22) }}…</span>
                </span>
                <span v-else>已存证（模拟模式，未上链）</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- ===== 双层校验状态（答辩亮点） ===== -->
      <div class="dual-check">
        <div class="check-item" :class="result.trusted ? 'ok' : 'bad'">
          <div class="check-head">
            <el-icon><DocumentChecked /></el-icon>
            <span>第一层 · 本地哈希比对</span>
            <el-tag :type="result.trusted ? 'success' : 'danger'" size="small" effect="dark">
              {{ result.trusted ? '通过' : '不通过' }}
            </el-tag>
          </div>
          <div class="check-body">
            当前数据重算摘要 与 存证表摘要
            <b>{{ result.current_hash === result.stored_hash ? '一致' : '不一致' }}</b>
          </div>
        </div>

        <div class="check-item"
             :class="!result.anchored ? 'skip' : (result.chain_verified ? 'ok' : 'bad')">
          <div class="check-head">
            <el-icon><Link /></el-icon>
            <span>第二层 · 联盟链上核验</span>
            <!--
              ★ 第四轮审计修复：原判据 `!result.tx_hash` 判「未上链」不成立 ——
                mock 存证的假哈希本身就是 0x+64hex，会被当成"已上链"，
                于是这一格显示成红色「不通过」而不是中性「未上链」。
                正确的判据是后端下发的 result.anchored（= chain_status == success）。
            -->
            <el-tag v-if="!result.anchored" type="info" size="small" effect="dark">
              {{ result.anchor_label || '未上链' }}
            </el-tag>
            <el-tag v-else :type="result.chain_verified ? 'success' : 'danger'"
                    size="small" effect="dark">
              {{ result.chain_verified ? '通过' : '不通过' }}
            </el-tag>
          </div>
          <div class="check-body">
            {{ result.chain_detail || result.anchor_detail || '该合格证尚未上链存证' }}
          </div>
        </div>
      </div>

      <!-- ===== 证面基础信息 ===== -->
      <div class="info-panel">
        <div class="info-title"><el-icon><Postcard /></el-icon> 合格证信息</div>
        <el-descriptions :column="isMobile ? 1 : 2" border class="result-desc">
          <el-descriptions-item label="合格证编号">{{ result.cert_no }}</el-descriptions-item>
          <el-descriptions-item label="证书状态">
            <el-tag :type="statusTagType(result.status)" size="small">
              {{ result.status_display }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="生产主体">{{ result.producer_name }}</el-descriptions-item>
          <el-descriptions-item label="产品名称">
            {{ result.product_name }} <span class="text-muted">（{{ result.variety }}）</span>
          </el-descriptions-item>
          <el-descriptions-item label="产地">{{ result.origin }}</el-descriptions-item>
          <el-descriptions-item label="开具数量">{{ result.quantity_kg }} kg</el-descriptions-item>
          <el-descriptions-item label="生产批次">{{ result.batch_no }}</el-descriptions-item>
          <el-descriptions-item label="检测报告">
            {{ result.report_no }}
            <el-tag v-if="result.conclusion" type="success" size="small" style="margin-left:6px">
              {{ result.conclusion }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="签发日期">{{ result.issue_date }}</el-descriptions-item>
          <el-descriptions-item label="有效期至">{{ result.valid_until }}</el-descriptions-item>
        </el-descriptions>
      </div>

      <!-- 编号构成：让消费者看懂 19 位编号的含义 -->
      <div v-if="result.cert_no_parsed && result.cert_no_parsed.valid" class="no-parse">
        <div class="no-parse-title">编号构成（19 位：区域码 + 日期 + 流水）</div>
        <div class="segments">
          <div class="seg">
            <div class="seg-val">{{ result.cert_no_parsed.region_code }}</div>
            <div class="seg-lbl">行政区划码</div>
          </div>
          <div class="seg">
            <div class="seg-val">{{ result.cert_no_parsed.date }}</div>
            <div class="seg-lbl">签发日期</div>
          </div>
          <div class="seg">
            <div class="seg-val">{{ result.cert_no_parsed.seq }}</div>
            <div class="seg-lbl">当日流水号</div>
          </div>
        </div>
      </div>

      <el-alert v-if="result.status === 'suspended'" type="error" :closable="false"
                show-icon class="suspend-alert">
        <template #title>该合格证已被暂停使用</template>
        <div class="result-msg">{{ result.suspend_reason || '监督检查不合格，暂缓流通' }}</div>
      </el-alert>

      <div class="chain-panel">
        <div class="chain-title">
          <el-icon><Lock /></el-icon> 可信存证信息
        </div>
        <el-descriptions :column="1" border>
          <el-descriptions-item label="合格证哈希">
            <span class="hash-text">{{ result.cert_hash }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="存证摘要">
            <span class="hash-text">{{ result.stored_hash }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="实时重算哈希">
            <span class="hash-text"
                  :class="result.trusted ? 'trust-badge-ok' : 'trust-badge-bad'">
              {{ result.current_hash }}
            </span>
          </el-descriptions-item>
          <el-descriptions-item label="存证交易哈希">
            <span class="hash-text">{{ result.tx_hash || '—' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="区块高度">{{ result.block_height || '—' }}</el-descriptions-item>
        </el-descriptions>
        <p class="chain-note">
          说明：验真采用<b>双层校验</b>。<br />
          第一层——读取当前合格证数据重新计算 SHA-256 摘要，与存证表中的摘要比对，识别业务数据是否被改动；<br />
          第二层——再向联盟链（FISCO BCOS）查询该合格证的链上存证摘要并比对，识别"连数据库摘要一起篡改"的高级攻击。<br />
          两层均通过才判定为可信。
        </p>
      </div>
    </div>

    <el-empty v-else-if="searched" description="未查询到该合格证，请核对编号" />

    <div class="verify-footer">
      <el-button link @click="$router.push('/login')">后台管理登录 →</el-button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { publicVerify } from '@/api'

const route = useRoute()
const certNo = ref(route.params.certNo || '')
const result = ref(null)
const loading = ref(false)
const searched = ref(false)
const samples = ['CERT-2026-1001', 'CERT-2026-1002']

// 移动端：验真页是扫码后的落地页，手机上必须好用 —— 描述列表要单列
const MQ = '(max-width: 768px)'
const isMobile = ref(window.matchMedia(MQ).matches)
let mq = null
const onMq = (e) => { isMobile.value = e.matches }

const timeline = computed(() => result.value?.timeline || [])
const timelineSummary = computed(() => result.value?.timeline_summary || {})

// ★ 第四轮修复（FRONTEND-VERDICT-IGNORES-ANCHOR）：
//   结论条必须综合 trusted 与 anchored —— 「本地摘要一致」不等于「链上已验证」。
//   三态优先级：已作废 > 可信（本地一致且已上链且链上核验通过）> 部分可信 > 不可信
const verdictState = computed(() => {
  const r = result.value
  if (!r) return 'unknown'
  if (r.revoked) return 'revoked'
  if (r.trusted && r.anchored && r.chain_verified) return 'ok'
  // 本地一致但未上链（mock/降级/链关闭）→ 中性提示，不给绿色
  if (r.trusted && !r.anchored) return 'partial'
  if (r.trusted) return 'local'          // 已上链但本次未做链上核验
  return 'bad'
})
const verdictClass = computed(() => verdictState.value)
const verdictIcon = computed(() => (
  ['ok', 'local', 'partial'].includes(verdictState.value)
    ? 'CircleCheckFilled' : 'CircleCloseFilled'
))
const verdictTitle = computed(() => {
  const r = result.value || {}
  switch (verdictState.value) {
    case 'revoked': return '该合格证已作废 · 不可流通'
    case 'ok': return '验真通过 · 数据可信（已通过链上核验）'
    case 'local': return '本地摘要一致 · 链上已存证'
    case 'partial': return '本地摘要一致 · 尚未上链核验'
    case 'bad': return '验真失败 · 数据不可信'
    default: return ''
  }
})

const statusTagType = (s) => ({
  issued: 'success', draft: 'info', pending: 'warning',
  suspended: 'danger', revoked: 'danger'
}[s] || 'info')

const doVerify = async () => {
  if (!certNo.value.trim()) return
  loading.value = true
  searched.value = true
  result.value = null
  try {
    const res = await publicVerify(certNo.value.trim())
    result.value = res.data
  } catch (e) {
    result.value = null
  } finally {
    loading.value = false
  }
}

const quick = (n) => {
  certNo.value = n
  doVerify()
}

onMounted(() => {
  mq = window.matchMedia(MQ)
  mq.addEventListener('change', onMq)
  if (certNo.value) doVerify()
})
onUnmounted(() => { mq?.removeEventListener('change', onMq) })
</script>

<style scoped>
.verify-page {
  min-height: 100%;
  background: #f5f7fa;
  padding: 32px 16px 60px;
  max-width: 860px;
  margin: 0 auto;
}
.verify-header { text-align: center; margin-bottom: 24px; }
.verify-header h1 {
  font-size: 24px;
  color: #0f5c3f;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin: 0 0 8px;
}
.verify-header p { color: #909399; font-size: 13px; }
.verify-search { display: flex; gap: 12px; }
.quick-tips { margin-top: 12px; display: flex; align-items: center; gap: 8px; }
.sample { cursor: pointer; }
.verify-result { margin-top: 24px; position: relative; }

/* ===== 动态防伪水印层：半透明铺底，不干扰阅读，截图可溯源 ===== */
.watermark-layer {
  position: absolute; inset: 0; overflow: hidden;
  pointer-events: none; user-select: none; z-index: 0;
  display: flex; flex-direction: column; justify-content: space-around;
  padding: 20px 0;
}
.wm-text {
  font-size: 13px; color: #909399; opacity: 0.10;
  transform: rotate(-18deg); white-space: nowrap;
  text-align: center; letter-spacing: 2px; padding: 12px 0;
}
/* 让结果区内容浮在水印之上，保证可读 */
.verify-result > *:not(.watermark-layer) { position: relative; z-index: 1; }

/* ===== 验真结论（顶部大色块，一眼可见）===== */
.verdict {
  display: flex; align-items: center; gap: 16px;
  padding: 18px 20px; border-radius: 10px; margin-bottom: 16px;
  color: #fff;
}
.verdict.ok { background: linear-gradient(135deg, #3f9d5c, #2d7d46); }
.verdict.bad { background: linear-gradient(135deg, #e05b5b, #c33c3c); }
.verdict.revoked { background: linear-gradient(135deg, #8c7a3c, #6b5c28); }
/* ★ 第四轮新增：本地一致但未上链核验 / 已上链但本次未核验 —— 不给绿色误导 */
.verdict.partial { background: linear-gradient(135deg, #c98a2e, #a06f1f); }
.verdict.local { background: linear-gradient(135deg, #4a7fa5, #35617f); }
.verdict.unknown { background: linear-gradient(135deg, #7a7a7a, #5c5c5c); }
.verdict-icon { flex: 0 0 auto; display: flex; }
.verdict-title { font-size: 19px; font-weight: 700; letter-spacing: 0.5px; }
.verdict-sub { font-size: 13px; opacity: 0.92; margin-top: 4px; }

/* ============================================================
   ★ 追溯链时间轴
   ============================================================ */
.tl-panel {
  background: #fff; border: 1px solid #e4e7ed; border-radius: 10px;
  padding: 18px; margin-bottom: 20px;
}
.tl-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 10px; flex-wrap: wrap; margin-bottom: 18px;
  padding-bottom: 12px; border-bottom: 1px solid #ebeef5;
}
.tl-head-left {
  display: flex; align-items: center; gap: 8px;
  font-size: 16px; font-weight: 700; color: #0f5c3f;
}
.tl-head-right { display: flex; gap: 8px; }

.timeline { position: relative; }
.tl-item { display: flex; gap: 14px; }

/* 轴：圆点 + 竖线 */
.tl-axis {
  flex: 0 0 34px; display: flex; flex-direction: column;
  align-items: center; position: relative;
}
.tl-dot {
  width: 32px; height: 32px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  background: #f2f6fc; color: #909399;
  border: 2px solid #dcdfe6; flex: 0 0 32px; z-index: 1;
}
.tl-line {
  flex: 1; width: 2px; background: #e4e7ed; margin: 2px 0 0;
  min-height: 20px;
}

/* 节点状态配色：让"哪一环出问题"一眼可见 */
.lv-success .tl-dot { background: #f0f9eb; color: #67c23a; border-color: #b3e19d; }
.lv-info .tl-dot { background: #FDEEE3; color: #E8743B; border-color: #F3B08A; }
.lv-warning .tl-dot { background: #fdf6ec; color: #e6a23c; border-color: #f3d19e; }
.lv-danger .tl-dot { background: #fef0f0; color: #f56c6c; border-color: #fab6b6; }
.lv-success .tl-line { background: #b3e19d; }
.lv-danger .tl-line { background: #fab6b6; }

.tl-content { flex: 1; min-width: 0; padding-bottom: 20px; }
.tl-row1 {
  display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
}
.tl-title { font-size: 15px; font-weight: 600; color: #303133; }
.tl-time { font-size: 12px; color: #909399; }
.tl-desc {
  font-size: 13px; color: #606266; line-height: 1.7; margin-top: 4px;
}
.tl-detail {
  margin-top: 8px; background: #fafbfc; border-radius: 6px;
  padding: 8px 12px; border: 1px solid #f0f2f5;
}
.tl-drow { display: flex; gap: 10px; font-size: 12.5px; line-height: 1.9; }
.tl-dk { flex: 0 0 84px; color: #909399; }
.tl-dv { flex: 1; color: #303133; word-break: break-all; }
.tl-anchor {
  display: flex; align-items: center; gap: 6px;
  font-size: 11.5px; color: #67c23a; margin-top: 8px;
  background: #f0f9eb; border-radius: 4px; padding: 4px 8px;
}
.lv-danger .tl-anchor { color: #e6a23c; background: #fdf6ec; }

/* 最后一个节点不要留大段空白 */
.tl-item:last-child .tl-content { padding-bottom: 0; }

/* ===== 双层校验 ===== */
.dual-check {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-bottom: 20px;
}
.check-item {
  border: 1px solid #e4e7ed;
  border-left: 3px solid #c0c4cc;
  border-radius: 6px;
  padding: 12px 14px;
  background: #fff;
}
.check-item.ok { border-left-color: #67c23a; }
.check-item.bad { border-left-color: #f56c6c; background: #fef0f0; }
.check-item.skip { border-left-color: #c0c4cc; background: #fafafa; }
.check-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
}
.check-head span { flex: 1; }
.check-body { font-size: 12px; color: #606266; line-height: 1.6; }
.check-body b { color: #303133; }

/* ===== 信息面板 ===== */
.info-panel {
  background: #fff; border: 1px solid #e4e7ed; border-radius: 10px;
  padding: 16px; margin-bottom: 16px;
}
.info-title {
  font-size: 15px; font-weight: 600; margin-bottom: 12px;
  display: flex; align-items: center; gap: 6px; color: #0f5c3f;
}
.result-desc { margin-bottom: 0; }
.result-alert { margin-bottom: 16px; }
.result-title { font-size: 16px; font-weight: 600; }
.result-msg { font-size: 13px; margin-top: 4px; }

.chain-panel {
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 16px;
}
.chain-title {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
  color: #0f5c3f;
}
.chain-note {
  font-size: 12px;
  color: #909399;
  line-height: 1.7;
  margin: 12px 0 0;
}
.no-parse {
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 14px 16px;
  margin-bottom: 16px;
}
.no-parse-title { font-size: 13px; font-weight: 600; margin-bottom: 10px; }
.segments { display: flex; gap: 10px; flex-wrap: wrap; }
.seg {
  border: 1px solid #dcdfe6;
  border-radius: 6px;
  padding: 8px 16px;
  text-align: center;
  min-width: 110px;
  flex: 1;
}
.seg-val {
  font-family: Consolas, Monaco, monospace;
  font-size: 16px;
  font-weight: 700;
  color: #0f5c3f;
}
.seg-lbl { font-size: 11px; color: #909399; margin-top: 4px; }
.suspend-alert { margin-bottom: 16px; }
.verify-footer { text-align: center; margin-top: 30px; display: flex; flex-direction: column; align-items: center; gap: 4px; }

/* ============================================================
   移动端：验真页是「扫码落地页」，手机上必须比桌面更好用
   ============================================================ */
@media (max-width: 768px) {
  .verify-page { padding: 18px 12px 44px; }
  .verify-header { margin-bottom: 18px; }
  .verify-header h1 { font-size: 19px; }
  .verify-header p { font-size: 12px; line-height: 1.6; }

  /* 搜索区改竖排：横排在 375px 下输入框只剩一半宽 */
  .verify-search { flex-direction: column; gap: 10px; }
  .verify-search :deep(.el-button) { width: 100%; }

  .quick-tips { flex-wrap: wrap; }

  /* 结论块更紧凑 */
  .verdict { padding: 14px; gap: 12px; }
  .verdict-title { font-size: 16px; }
  .verdict-sub { font-size: 12px; }

  /* 时间轴：缩小轴宽与圆点，把宽度让给内容（手机上横轴很贵） */
  .tl-panel { padding: 14px 12px; }
  .tl-head { margin-bottom: 14px; }
  .tl-head-left { font-size: 15px; }
  .tl-item { gap: 10px; }
  .tl-axis { flex: 0 0 26px; }
  .tl-dot { width: 26px; height: 26px; flex: 0 0 26px; }
  .tl-dot :deep(.el-icon) { font-size: 13px !important; }
  .tl-title { font-size: 14px; }
  .tl-desc { font-size: 12.5px; }
  /* 键值对改上下排：84px 标签 + 内容在窄屏会被压成窄条 */
  .tl-drow { flex-direction: column; gap: 0; line-height: 1.6; padding: 3px 0; }
  .tl-dk { flex: none; font-size: 11.5px; }
  .tl-dv { font-size: 12.5px; }

  /* 双层校验改单列 */
  .dual-check { grid-template-columns: 1fr; }

  /* 编号构成：三格改两格，避免每格过窄 */
  .seg { min-width: 46%; padding: 8px 10px; }
  .seg-val { font-size: 14px; }

  .chain-panel, .info-panel, .no-parse { padding: 13px; }
}
</style>
