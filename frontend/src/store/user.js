import { defineStore } from 'pinia'
import { login as loginApi, logout as logoutApi } from '@/api'

export const useUserStore = defineStore('user', {
  // ★ token 说明：store.token/refresh 只在**登录**时写入，用作「已登录」标志与
  //   初始渲染；静默续期（request.js 的 renewTokens）只更新 localStorage，
  //   **不会**回填 store。因此发给后端的 token 事实源是 **localStorage**，
  //   发请求一律经 request 拦截器读它（切勿用 store.token 拼 Authorization）。
  state: () => ({
    token: localStorage.getItem('token') || '',
    refresh: localStorage.getItem('refresh') || '',
    user: JSON.parse(localStorage.getItem('user') || 'null')
  }),
  getters: {
    isLogin: (state) => !!state.token,
    role: (state) => state.user?.role || '',
    username: (state) => state.user?.username || '',
    // 权限清单由后端下发（/api/auth/profile/ 的 permissions 字段）
    perms: (state) => state.user?.permissions || {},
    isRegulator: (state) =>
      ['admin', 'regulator'].includes(state.user?.role || ''),
    isAdmin: (state) => (state.user?.role || '') === 'admin',
    isProducer: (state) => (state.user?.role || '') === 'producer',
    isBuyer: (state) => (state.user?.role || '') === 'buyer'
  },
  actions: {
    async login(form) {
      const res = await loginApi(form)
      const data = res.data
      this.token = data.access
      this.refresh = data.refresh || ''
      this.user = data.user
      localStorage.setItem('token', data.access)
      // ★ 保存 refresh：注销时交给后端拉黑（JWT 加固）
      if (data.refresh) localStorage.setItem('refresh', data.refresh)
      localStorage.setItem('user', JSON.stringify(data.user))
      return data
    },
    setUser(user) {
      this.user = user
      localStorage.setItem('user', JSON.stringify(user))
    },
    async logout() {
      // ★ B-2 修复：优先 localStorage —— 静默续期（request.js 的 renewTokens）
      //   只写 localStorage、从不回填 store，所以 this.refresh 很可能是**已被
      //   拉黑的旧值**。若拿它去注销，后端会把「已拉黑的行」再拉黑一次（无害但
      //   假注销），而真正活着的新 refresh 没人拉黑 → 注销后仍能换新 access。
      try {
        const rt = localStorage.getItem('refresh') || this.refresh
        if (rt) await logoutApi(rt)
      } catch (e) {
        // 网络/已过期等都不影响本地登出
      }
      this.token = ''
      this.refresh = ''
      this.user = null
      localStorage.removeItem('token')
      localStorage.removeItem('refresh')
      localStorage.removeItem('user')
      // ★ 第四轮：登出时一并清掉强制改密标记，避免换账号登录被上一次的标记带偏
      localStorage.removeItem('must_change_password')
    }
  }
})