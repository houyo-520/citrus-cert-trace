import { createRouter, createWebHashHistory } from 'vue-router'

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
    next()
  } else if (!token) {
    next('/login')
  } else {
    next()
  }
})

export default router