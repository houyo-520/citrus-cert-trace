import { createRouter, createWebHashHistory } from 'vue-router'
import { ElMessage } from 'element-plus'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { title: '登录', public: true }
  },
  {
    path: '/verify/:certNo?',
    name: 'Verify',
    component: () => import('@/views/Verify.vue'),
    meta: { title: '合格证验真', public: true }
  },
  {
    path: '/',
    component: () => import('@/layout/MainLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/Dashboard.vue'),
        meta: { title: '数据看板' }
      },
      {
        path: 'producer',
        name: 'Producer',
        component: () => import('@/views/Producer.vue'),
        meta: { title: '生产主体' }
      },
      {
        path: 'batch',
        name: 'Batch',
        component: () => import('@/views/Batch.vue'),
        meta: { title: '生产批次' }
      },
      {
        path: 'report',
        name: 'Report',
        component: () => import('@/views/Report.vue'),
        meta: { title: '检测报告' }
      },
      {
        path: 'certificate',
        name: 'Certificate',
        component: () => import('@/views/Certificate.vue'),
        meta: { title: '合格证管理' }
      },
      {
        path: 'evidence',
        name: 'Evidence',
        component: () => import('@/views/Evidence.vue'),
        meta: { title: '存证与核验' }
      },
      {
        path: 'supply',
        name: 'Supply',
        component: () => import('@/views/Supply.vue'),
        meta: { title: '产销对接' }
      },
      {
        path: 'intent',
        name: 'Intent',
        component: () => import('@/views/Intent.vue'),
        meta: { title: '产销意向单' }
      },
      {
        path: 'log',
        name: 'OperationLog',
        component: () => import('@/views/OperationLog.vue'),
        meta: { title: '操作日志' }
      },
      {
        path: 'roles',
        name: 'RoleMatrix',
        component: () => import('@/views/RoleMatrix.vue'),
        meta: { title: '权限说明' }
      }
    ]
  }
]

const router = createRouter({
  history: createWebHashHistory(),
  routes
})

router.beforeEach((to, from, next) => {
  document.title = (to.meta.title ? to.meta.title + ' - ' : '') + '柑橘合格证追溯系统'
  const token = localStorage.getItem('token')
  if (to.meta.public) {
    return next()
  }
  if (!token) {
    return next('/login')
  }
  // 角色级路由守卫：菜单已隐藏，这里防手输 URL 绕过
  const need = to.meta.roles
  if (need && need.length) {
    let role = ''
    try {
      role = JSON.parse(localStorage.getItem('user') || '{}').role || ''
    } catch (e) {
      role = ''
    }
    if (!need.includes(role)) {
      ElMessage.warning('当前角色无权访问该页面')
      return next('/dashboard')
    }
  }
  next()
})

export default router
