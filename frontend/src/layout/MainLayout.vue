<template>
  <el-container class="layout">
    <el-aside width="220px" class="aside">
      <div class="logo">
        <el-icon :size="22"><Orange /></el-icon>
        <span>柑橘合格证追溯</span>
      </div>
      <el-menu
        :default-active="activeMenu"
        router
        background-color="#1f2d3d"
        text-color="#c0c4cc"
        active-text-color="#ffd04b"
      >
        <!-- 菜单按角色渲染：menus 已按当前角色过滤 -->
        <el-menu-item v-for="m in menus" :key="m.path" :index="m.path">
          <el-icon><component :is="m.icon" /></el-icon>
          <span>{{ m.label }}</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <div class="header-title">{{ route.meta.title }}</div>
        <div class="header-right">
          <!-- ★ 风险预警红点：有待办才出现，点击展开清单并可直接跳转处理 -->
          <el-popover v-if="alertStore.hasAlert" placement="bottom-end" width="380"
                      trigger="click">
            <template #reference>
              <el-badge :value="alertStore.total"
                        :type="alertStore.hasDanger ? 'danger' : 'warning'"
                        class="alert-badge">
                <el-button size="small" :type="alertStore.hasDanger ? 'danger' : 'warning'"
                           plain>
                  <el-icon><WarningFilled /></el-icon>
                  预警
                </el-button>
              </el-badge>
            </template>
            <div class="alert-pop">
              <div class="alert-pop-head">
                <span>风险预警（{{ alertStore.total }} 项）</span>
                <el-button link type="primary" size="small" @click="alertStore.fetch(true)">
                  刷新
                </el-button>
              </div>
              <div class="alert-pop-list">
                <div v-for="a in alertStore.alerts" :key="a.key"
                     class="alert-pop-item" @click="gotoAlert(a.action_route)">
                  <el-tag :type="levelTag(a.level)" size="small" effect="dark">
                    {{ levelText(a.level) }}
                  </el-tag>
                  <div class="ap-text">
                    <div class="ap-title">{{ a.title }} × {{ a.count }}</div>
                    <div class="ap-desc">{{ a.desc }}</div>
                  </div>
                </div>
              </div>
            </div>
          </el-popover>

          <el-tag type="success" effect="plain" size="small">可信存证已启用</el-tag>
          <el-tag type="warning" effect="plain" size="small">{{ scopeLabel }}</el-tag>
          <el-dropdown @command="onCommand">
            <span class="user-info">
              <el-icon><User /></el-icon>
              {{ userStore.username }}
              <el-tag size="small" type="info">{{ roleLabel }}</el-tag>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="roles">查看权限说明</el-dropdown-item>
                <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { WarningFilled } from '@element-plus/icons-vue'
import { useUserStore } from '@/store/user'
import { useAlertStore } from '@/store/alerts'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const alertStore = useAlertStore()

const levelTag = (lv) => ({ danger: 'danger', warning: 'warning', info: 'info' }[lv] || 'info')
const levelText = (lv) => ({ danger: '紧急', warning: '关注', info: '提示' }[lv] || '提示')
const gotoAlert = (path) => { if (path) router.push(`/${path}`) }

// 进入后台即拉一次预警；之后每次切页顺带刷新（store 内部 30 秒节流）
onMounted(() => alertStore.fetch())
watch(() => route.path, () => alertStore.fetch())

const activeMenu = computed(() => route.path)
const roleMap = {
  admin: '系统管理员',
  regulator: '监管人员',
  producer: '种植主体',
  buyer: '收购商'
}
const roleLabel = computed(() => roleMap[userStore.role] || userStore.role)
const scopeLabel = computed(() => {
  if (userStore.isRegulator) return '全量数据可见'
  if (userStore.isProducer) return '仅我的数据'
  if (userStore.isBuyer) return '仅可交易数据'
  return ''
})

// 全部菜单定义，roles 为空表示所有角色可见
const ALL_MENUS = [
  { path: '/dashboard', label: '数据看板', icon: 'DataLine', roles: [] },
  { path: '/producer', label: '生产主体', icon: 'OfficeBuilding', roles: [] },
  { path: '/batch', label: '生产批次', icon: 'Box', roles: [] },
  { path: '/report', label: '检测报告', icon: 'Document', roles: [] },
  { path: '/certificate', label: '合格证管理', icon: 'Postcard', roles: [] },
  { path: '/inspection', label: '监督检查', icon: 'Stamp', roles: [] },
  { path: '/evidence', label: '存证与核验', icon: 'Lock', roles: [] },
  { path: '/supply', label: '产销对接', icon: 'ShoppingCart', roles: [] },
  { path: '/intent', label: '产销意向单', icon: 'Connection', roles: [] },
  { path: '/log', label: '操作日志', icon: 'Tickets', roles: [] },
  { path: '/roles', label: '权限说明', icon: 'Key', roles: [] }
]

// ★ 按角色过滤菜单 —— 角色差异化的直观体现
const menus = computed(() => {
  const role = userStore.role
  return ALL_MENUS.filter((m) => !m.roles.length || m.roles.includes(role))
})

const onCommand = async (cmd) => {
  if (cmd === 'roles') {
    router.push('/roles')
    return
  }
  if (cmd === 'logout') {
    await ElMessageBox.confirm('确认退出登录？', '提示', { type: 'warning' })
    // 退出时清掉预警缓存，避免下一个账号登录时闪出上个账号的红点
    alertStore.reset()
    userStore.logout()
    router.push('/login')
  }
}
</script>

<style scoped>
.layout { height: 100%; }
.aside {
  background: #1f2d3d;
  overflow-x: hidden;
}
.logo {
  height: 60px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 18px;
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  border-bottom: 1px solid rgba(255,255,255,0.08);
}
.aside :deep(.el-menu) { border-right: none; }
.header {
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #e4e7ed;
  height: 60px;
}
.header-title { font-size: 16px; font-weight: 600; }
.header-right { display: flex; align-items: center; gap: 12px; }
.user-info {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  font-size: 14px;
  color: #303133;
}
.main { background: #f5f7fa; padding: 16px; }

/* 预警红点与弹出清单 */
.alert-badge { margin-right: 4px; }
.alert-pop-head {
  display: flex; justify-content: space-between; align-items: center;
  font-weight: 600; font-size: 13.5px; padding-bottom: 8px;
  border-bottom: 1px solid #ebeef5;
}
.alert-pop-list {
  max-height: 340px; overflow-y: auto; padding-top: 6px;
}
.alert-pop-item {
  display: flex; gap: 8px; align-items: flex-start;
  padding: 8px; border-radius: 6px; cursor: pointer;
}
.alert-pop-item:hover { background: #f5f7fa; }
.ap-text { min-width: 0; }
.ap-title { font-size: 13px; font-weight: 600; color: #303133; }
.ap-desc { font-size: 11.5px; color: #909399; margin-top: 2px; line-height: 1.5; }
</style>
