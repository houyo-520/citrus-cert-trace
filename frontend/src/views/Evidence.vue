<template>
  <div>
    <!-- 联盟链状态 -->
    <el-card shadow="never" class="chain-status-card">
      <div class="chain-status-row">
        <div class="chain-status-left">
          <el-icon class="chain-icon" :class="chain.connected ? 'on' : 'off'">
            <Link />
          </el-icon>
          <div>
            <div class="chain-status-title">
              联盟链节点
              <el-tag :type="chain.connected ? 'success' : 'danger'" size="small" effect="dark">
                {{ chain.connected ? '已连接' : '未连接' }}
              </el-tag>
            </div>
            <div class="chain-status-sub">{{ chain.rpc || '—' }}</div>
          </div>
        </div>
        <div v-if="chain.connected" class="chain-metrics">
          <div class="metric"><span>区块高度</span><b>{{ chain.block_number }}</b></div>
          <div class="metric"><span>链 ID</span><b>{{ chain.chain_id }}</b></div>
          <div class="metric"><span>群组</span><b>{{ chain.group_id }}</b></div>
          <div class="metric"><span>存证总数</span><b>{{ chain.total_evidence ?? '—' }}</b></div>
        </div>
        <el-button link @click="loadChain">
          <el-icon><Refresh /></el-icon> 刷新
        </el-button>
      </div>
      <div v-if="chain.contract" class="chain-contract">
        存证合约：<span class="hash-text">{{ chain.contract }}</span>
        <span v-if="chain.contract_version" class="text-muted">（{{ chain.contract_version }}）</span>
      </div>
    </el-card>

    <el-card shadow="never" style="margin-top:16px">
      <div class="card-title">存证记录（哈希留痕）</div>
      <el-table :data="list" v-loading="loading" border stripe>
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="biz_type_display" label="业务类型" width="110" />
        <el-table-column prop="biz_no" label="业务编号" width="160" />
        <el-table-column label="数据哈希(SHA-256)" min-width="320">
          <template #default="{ row }">
            <span class="hash-text">{{ row.data_hash }}</span>
          </template>
        </el-table-column>
        <el-table-column label="交易哈希" min-width="240">
          <template #default="{ row }">
            <span class="hash-text">{{ (row.tx_hash || '').slice(0, 24) }}...</span>
          </template>
        </el-table-column>
        <el-table-column prop="block_height" label="区块高度" width="110" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.chain_status === 'success' ? 'success' : 'info'" size="small">
              {{ row.chain_status === 'success' ? '已上链' : '模拟存证' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        style="margin-top:16px; justify-content:flex-end"
        layout="total, prev, pager, next"
        :total="total" :page-size="pageSize" :current-page="page"
        @current-change="onPage"
      />
    </el-card>

    <el-card shadow="never" style="margin-top:16px">
      <div class="card-title">哈希核验（本地比对 + 链上核验）</div>
      <div class="verify-row">
        <el-input v-model="certNo" placeholder="如 CERT-2026-1001" style="width:320px"
                  clearable @keyup.enter="doVerify" />
        <el-button type="primary" :loading="verifying" @click="doVerify">核验</el-button>
      </div>

      <div v-if="verifyResult" class="verify-out">
        <el-alert :type="verifyResult.trusted ? 'success' : 'error'" :closable="false" show-icon>
          <template #title>{{ verifyResult.message }}</template>
        </el-alert>
        <el-descriptions :column="1" border style="margin-top:12px">
          <el-descriptions-item label="当前重算哈希">
            <span class="hash-text">{{ verifyResult.current_hash }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="存证摘要">
            <span class="hash-text">{{ verifyResult.stored_hash }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="链上核验">
            <el-tag v-if="verifyResult.chain_verified" type="success" size="small" effect="dark">
              链上摘要一致
            </el-tag>
            <el-tag v-else type="danger" size="small" effect="dark">链上核验未通过</el-tag>
            <span class="text-muted" style="margin-left:8px">{{ verifyResult.chain_detail }}</span>
          </el-descriptions-item>
        </el-descriptions>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listEvidence, verifyEvidence, chainStatus } from '@/api'

const list = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 10
const loading = ref(false)
const certNo = ref('')
const verifying = ref(false)
const verifyResult = ref(null)
const chain = ref({ connected: false })

const load = async () => {
  loading.value = true
  try {
    const res = await listEvidence({ page: page.value })
    list.value = res.results || res.data || []
    total.value = res.count ?? list.value.length
  } finally {
    loading.value = false
  }
}

const loadChain = async () => {
  try {
    const res = await chainStatus()
    chain.value = res.data || { connected: false }
  } catch (e) {
    chain.value = { connected: false }
  }
}

const onPage = (p) => { page.value = p; load() }

const doVerify = async () => {
  if (!certNo.value.trim()) {
    ElMessage.warning('请输入合格证编号')
    return
  }
  verifying.value = true
  verifyResult.value = null
  try {
    const res = await verifyEvidence(certNo.value.trim(), true)
    verifyResult.value = res.data
  } catch (e) {
  } finally {
    verifying.value = false
  }
}

onMounted(() => { load(); loadChain() })
</script>

<style scoped>
.verify-row { display: flex; gap: 10px; }
.verify-out { margin-top: 14px; }
.chain-status-card { border-left: 3px solid #67c23a; }
.chain-status-row {
  display: flex;
  align-items: center;
  gap: 20px;
  flex-wrap: wrap;
}
.chain-status-left { display: flex; align-items: center; gap: 12px; min-width: 260px; }
.chain-icon { font-size: 26px; }
.chain-icon.on { color: #67c23a; }
.chain-icon.off { color: #f56c6c; }
.chain-status-title {
  font-size: 14px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 8px;
}
.chain-status-sub { font-size: 12px; color: #909399; margin-top: 2px; }
.chain-metrics { display: flex; gap: 24px; flex: 1; flex-wrap: wrap; }
.metric { display: flex; flex-direction: column; font-size: 12px; color: #909399; }
.metric b { font-size: 16px; color: #303133; margin-top: 2px; }
.chain-contract {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed #e4e7ed;
  font-size: 12px;
  color: #606266;
}
</style>