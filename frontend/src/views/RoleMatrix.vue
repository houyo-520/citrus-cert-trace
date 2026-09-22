<template>
  <div class="page">
    <el-card shadow="never">
      <template #header>
        <div class="head">
          <span>角色 - 资源访问矩阵</span>
          <el-tag type="info" size="small">数据级权限隔离</el-tag>
        </div>
      </template>
      <el-table :data="resourceMatrix" v-loading="loading" border size="small">
        <el-table-column prop="resource" label="资源" width="140" />
        <el-table-column v-for="r in roles" :key="r.key" :label="r.label" min-width="150">
          <template #default="{ row }">
            <span :class="{ 'txt-hl': row[r.key] === '全部' }">{{ row[r.key] }}</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="head">
          <span>角色 - 操作权限矩阵</span>
          <el-tag type="warning" size="small">接口级权限拦截</el-tag>
        </div>
      </template>
      <el-table :data="actionMatrix" v-loading="loading" border size="small">
        <el-table-column prop="action" label="操作" width="180" />
        <el-table-column v-for="r in roles" :key="r.key" :label="r.label"
                         min-width="120" align="center">
          <template #default="{ row }">
            <el-icon v-if="row[r.key]" color="#67C23A"><CircleCheckFilled /></el-icon>
            <el-icon v-else color="#F56C6C"><CircleCloseFilled /></el-icon>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="head">
          <span>合格证编号规则</span>
          <el-tag type="success" size="small">
            {{ certNoRule.length }} 位 = 区域码 + 日期 + 流水
          </el-tag>
        </div>
      </template>
      <div class="rule-box">
        <div class="rule-example mono">{{ certNoRule.example }}</div>
        <div class="segments">
          <div v-for="s in certNoRule.segments" :key="s.name" class="seg">
            <div class="seg-val mono">{{ s.example }}</div>
            <div class="seg-lbl">{{ s.name }}（{{ s.length }} 位）</div>
            <div class="seg-desc">{{ s.desc }}</div>
          </div>
        </div>
        <el-alert type="info" :closable="false" show-icon class="mt">
          <template #title>{{ certNoRule.note }}</template>
        </el-alert>
      </div>

      <div class="flow-title">合格证状态流转</div>
      <div class="flow">
        <template v-for="(s, i) in statusFlow" :key="s.key">
          <div class="flow-node" :class="'flow-' + s.key">
            <div class="flow-name">{{ s.label }}</div>
            <div class="flow-desc">{{ s.desc }}</div>
          </div>
          <el-icon v-if="i < statusFlow.length - 1" class="flow-arrow">
            <Right />
          </el-icon>
        </template>
      </div>
    </el-card>

    <el-card shadow="never">
      <template #header><span>当前登录身份</span></template>
      <el-descriptions :column="3" border size="small">
        <el-descriptions-item label="账号">{{ userStore.username }}</el-descriptions-item>
        <el-descriptions-item label="角色">
          <el-tag size="small">{{ userStore.user?.role_display }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="数据范围">
          {{ userStore.perms.can_view_all ? '全量可见' : '受限（按角色隔离）' }}
        </el-descriptions-item>
        <el-descriptions-item label="主体审核">
          <el-tag size="small" :type="userStore.perms.can_audit ? 'success' : 'info'">
            {{ userStore.perms.can_audit ? '可操作' : '无权限' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="合格证签发">
          <el-tag size="small" :type="userStore.perms.can_issue ? 'success' : 'info'">
            {{ userStore.perms.can_issue ? '可操作' : '无权限' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="合格证作废">
          <el-tag size="small" :type="userStore.perms.can_revoke ? 'success' : 'info'">
            {{ userStore.perms.can_revoke ? '可操作' : '无权限' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="监督检查录入">
          <el-tag size="small" :type="userStore.perms.can_inspect ? 'success' : 'info'">
            {{ userStore.perms.can_inspect ? '可操作' : '无权限' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="整改复查">
          <el-tag size="small" :type="userStore.perms.can_rectify ? 'success' : 'info'">
            {{ userStore.perms.can_rectify ? '可操作' : '无权限' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="生产数据写入">
          <el-tag size="small" :type="userStore.perms.can_write_production ? 'success' : 'info'">
            {{ userStore.perms.can_write_production ? '可操作' : '无权限' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="执行存证">
          <el-tag size="small" :type="userStore.perms.can_anchor ? 'success' : 'info'">
            {{ userStore.perms.can_anchor ? '可操作' : '无权限' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="发布供需/对接">
          <el-tag size="small" :type="userStore.perms.can_publish_supply ? 'success' : 'info'">
            {{ userStore.perms.can_publish_supply ? '可操作' : '无权限' }}
          </el-tag>
        </el-descriptions-item>
      </el-descriptions>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { getRoleMatrix } from '@/api'
import { useUserStore } from '@/store/user'

const userStore = useUserStore()
const loading = ref(false)
const resourceMatrix = ref([])
const actionMatrix = ref([])
const roles = ref([])
const certNoRule = ref({ length: 19, segments: [], example: '', note: '' })
const statusFlow = ref([])

const load = async () => {
  loading.value = true
  try {
    const res = await getRoleMatrix()
    const d = res.data.data
    resourceMatrix.value = d.resource_matrix
    actionMatrix.value = d.action_matrix
    roles.value = d.roles
    if (d.cert_no_rule) certNoRule.value = d.cert_no_rule
    if (d.cert_status_flow) statusFlow.value = d.cert_status_flow
  } finally {
    loading.value = false
  }
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
.txt-hl { color: #409EFF; font-weight: 600; }
.mono { font-family: Consolas, Monaco, monospace; }
.rule-box { padding: 4px 0; }
.rule-example {
  font-size: 24px;
  font-weight: 700;
  letter-spacing: 3px;
  color: #409eff;
  text-align: center;
  margin-bottom: 16px;
}
.segments {
  display: flex;
  justify-content: center;
  gap: 12px;
  flex-wrap: wrap;
}
.seg {
  border: 1px solid #dcdfe6;
  border-radius: 6px;
  padding: 10px 18px;
  text-align: center;
  min-width: 130px;
}
.seg-val { font-size: 18px; font-weight: 700; color: #303133; }
.seg-lbl { font-size: 12px; color: #606266; margin-top: 4px; }
.seg-desc { font-size: 11px; color: #909399; margin-top: 4px; }
.flow-title { font-weight: 600; margin: 22px 0 12px; font-size: 14px; }
.flow { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.flow-node {
  border: 1px solid #dcdfe6;
  border-radius: 6px;
  padding: 8px 12px;
  min-width: 120px;
  flex: 1;
  background: #fafafa;
}
.flow-name { font-size: 13px; font-weight: 600; }
.flow-desc { font-size: 11px; color: #909399; margin-top: 4px; line-height: 1.4; }
.flow-draft { border-left: 3px solid #909399; }
.flow-pending { border-left: 3px solid #e6a23c; }
.flow-issued { border-left: 3px solid #67c23a; }
.flow-suspended { border-left: 3px solid #f56c6c; }
.flow-revoked { border-left: 3px solid #303133; }
.flow-arrow { color: #c0c4cc; flex: 0 0 auto; }
.mt { margin-top: 16px; }
</style>

