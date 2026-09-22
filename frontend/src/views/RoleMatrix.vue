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

const load = async () => {
  loading.value = true
  try {
    const res = await getRoleMatrix()
    const d = res.data.data
    resourceMatrix.value = d.resource_matrix
    actionMatrix.value = d.action_matrix
    roles.value = d.roles
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
</style>
