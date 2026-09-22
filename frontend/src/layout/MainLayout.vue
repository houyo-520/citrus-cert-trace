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
        <el-menu-item index="/dashboard">
          <el-icon><DataLine /></el-icon><span>数据看板</span>
        </el-menu-item>
        <el-menu-item index="/producer">
          <el-icon><OfficeBuilding /></el-icon><span>生产主体</span>
        </el-menu-item>
        <el-menu-item index="/batch">
          <el-icon><Box /></el-icon><span>生产批次</span>
        </el-menu-item>
        <el-menu-item index="/report">
          <el-icon><Document /></el-icon><span>检测报告</span>
        </el-menu-item>
        <el-menu-item index="/certificate">
          <el-icon><Postcard /></el-icon><span>合格证管理</span>
        </el-menu-item>
        <el-menu-item index="/evidence">
          <el-icon><Lock /></el-icon><span>存证与核验</span>
        </el-menu-item>
        <el-menu-item index="/supply">
          <el-icon><ShoppingCart /></el-icon><span>产销对接</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <div class="header-title">{{ route.meta.title }}</div>
        <div class="header-right">
          <el-tag type="success" effect="plain" size="small">可信存证已启用</el-tag>
          <el-dropdown @command="onCommand">
            <span class="user-info">
              <el-icon><User /></el-icon>
              {{ userStore.username }}
              <el-tag size="small" type="info">{{ roleLabel }}</el-tag>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="logout">退出登录</el-dropdown-item>
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
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { useUserStore } from '@/store/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const activeMenu = computed(() => route.path)
const roleMap = {
  admin: '系统管理员',
  regulator: '监管人员',
  producer: '种植主体',
  buyer: '收购商'
}
const roleLabel = computed(() => roleMap[userStore.role] || userStore.role)

const onCommand = async (cmd) => {
  if (cmd === 'logout') {
    await ElMessageBox.confirm('确认退出登录？', '提示', { type: 'warning' })
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
.header-right { display: flex; align-items: center; gap: 16px; }
.user-info {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  font-size: 14px;
  color: #303133;
}
.main { background: #f5f7fa; padding: 16px; }
</style>