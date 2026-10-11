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
    // ★ 第四轮审计修复（FORCED-PASSWORD-CHANGE-NO-CLIENT-FLOW，中高）：
    //   后端 ForcePasswordChangeMiddleware 会把待改密账号除白名单外的所有
    //   业务接口 403 掉。若前端没有改密入口，账号一旦被置 must_change_password=1
    //   就是死锁：每个页面都报错，且无处自救。
    //   这条独立路由（不进 MainLayout，避免侧边栏请求触发 403）提供自救入口。
    path: '/change-password',
    name: 'ChangePassword',
    component: () => import('@/views/ChangePassword.vue'),
    meta: { title: '修改密码' }     // 注意：不加 public —— 仍需登录态
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
        path: 'notice',
        name: 'Notification',
        component: () => import('@/views/Notification.vue'),
        meta: { title: '消息中心' }
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
        path: 'inspection',
        name: 'Inspection',
        component: () => import('@/views/Inspection.vue'),
        meta: { title: '监督检查' }
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
        path: 'login-monitor',
        name: 'LoginMonitor',
        component: () => import('@/views/LoginMonitor.vue'),
        meta: { title: '登录监控', roles: ['admin'] }
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

/**
 * ★ 第五轮（STALE-FLAG-LOCKOUT）：向服务端复核「待改密」标记是否仍然有效。
 * 返回 true 表示服务端也认为需要改密；false 表示标记已过期（应清除并放行）。
 * 网络异常时保守返回 true（宁可多拦一次，也不要放过真正待改密的账号）。
 */
let _rechecking = null
function recheckMustChange () {
  if (_rechecking) return _rechecking
  _rechecking = (async () => {
    try {
      // ★ 用动态 import 打破循环依赖：
      //   utils/request.js 静态 import 了本模块（用于 forceLogout 跳转登录），
      //   若这里再静态 import request 就会成环（Vite 下表现为初始化顺序不确定）。
      const { default: request } = await import('@/utils/request')
      const res = await request.get('/auth/profile/')
      const u = res?.data ?? res
      const still = !!u?.must_change_password
      if (!still) localStorage.removeItem('must_change_password')
      return still
    } catch (e) {
      // 401/网络错误：交给既有拦截器处理；此处保守维持拦截
      return true
    } finally {
      _rechecking = null
    }
  })()
  return _rechecking
}

router.beforeEach((to, from, next) => {
  document.title = (to.meta.title ? to.meta.title + ' - ' : '') + '柑橘合格证追溯系统'
  const token = localStorage.getItem('token')
  if (to.meta.public) {
    return next()
  }
  if (!token) {
    return next('/login')
  }
  // ★ 第四轮审计修复（强制改密客户端流程）：
  //   待改密账号只能停在改密页 —— 否则进任何业务页都会收到 403，
  //   用户看到一堆报错却不知道要干什么（体验上是「系统坏了」）。
  const mustChange = localStorage.getItem('must_change_password') === '1'
  if (mustChange) {
    // ★ 第五轮审计修复（STALE-FLAG-LOCKOUT，低）：
    //   本地标记可能是**残留**的（改密成功但响应丢失；或管理员在后台清了标记）。
    //   只信本地会把人反复踹回改密页。这里先向服务端复核一次 ——
    //   /api/auth/profile/ 在白名单内，待改密时也不会被 403，
    //   所以它返回的 must_change_password 才是权威值。
    if (to.name !== 'ChangePassword') {
      recheckMustChange().then((stillForced) => {
        if (stillForced) {
          next({ name: 'ChangePassword', query: { forced: '1' } })
        } else {
          next()          // 标记已过期 → 放行
        }
      })
      return
    }
  }
  if (!mustChange && to.name === 'ChangePassword' && to.query.forced === '1') {
    // 改完密后不要再被 forced 参数拉回改密页
    return next('/dashboard')
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
