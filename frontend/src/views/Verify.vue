<template>
  <div class="verify-page">
    <div class="verify-header">
      <h1><el-icon><Orange /></el-icon> 柑橘合格证追溯验真</h1>
      <p>输入或扫描合格证编号，查询农产品承诺达标合格证及存证信息</p>
    </div>

    <div class="verify-search">
      <el-input v-model="certNo" placeholder="请输入合格证编号，如 CERT-2026-1001"
                size="large" clearable @keyup.enter="doVerify">
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
      <el-alert
        :type="result.trusted ? 'success' : 'error'"
        :closable="false"
        show-icon
        class="result-alert"
      >
        <template #title>
          <span class="result-title">
            {{ result.trusted ? '✓ 验真通过：数据可信' : '✗ 验真失败：数据不可信' }}
          </span>
        </template>
        <div class="result-msg">{{ result.verify_message }}</div>
      </el-alert>

      <!-- 双层校验状态（答辩亮点） -->
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
             :class="!result.tx_hash ? 'skip' : (result.chain_verified ? 'ok' : 'bad')">
          <div class="check-head">
            <el-icon><Link /></el-icon>
            <span>第二层 · 联盟链上核验</span>
            <el-tag v-if="!result.tx_hash" type="info" size="small" effect="dark">未上链</el-tag>
            <el-tag v-else :type="result.chain_verified ? 'success' : 'danger'"
                    size="small" effect="dark">
              {{ result.chain_verified ? '通过' : '不通过' }}
            </el-tag>
          </div>
          <div class="check-body">
            {{ result.chain_detail || '该合格证尚未上链存证' }}
          </div>
        </div>
      </div>

      <el-descriptions :column="2" border class="result-desc">
        <el-descriptions-item label="合格证编号">{{ result.cert_no }}</el-descriptions-item>
        <el-descriptions-item label="证书状态">
          <el-tag :type="result.status === 'issued' ? 'success' : 'info'" size="small">
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
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { publicVerify } from '@/api'

const route = useRoute()
const certNo = ref(route.params.certNo || '')
const result = ref(null)
const loading = ref(false)
const searched = ref(false)
const samples = ['CERT-2026-1001', 'CERT-2026-1002']

const doVerify = async () => {
  if (!certNo.value.trim()) {
    ElMessage.warning('请输入合格证编号')
    return
  }
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

if (certNo.value) doVerify()
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
.verify-result { margin-top: 24px; }
.result-alert { margin-bottom: 16px; }
.result-title { font-size: 16px; font-weight: 600; }
.result-msg { font-size: 13px; margin-top: 4px; }
.result-desc { margin-bottom: 20px; }
.dual-check {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-bottom: 20px;
}
@media (max-width: 640px) {
  .dual-check { grid-template-columns: 1fr; }
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
.verify-footer { text-align: center; margin-top: 30px; }
</style>