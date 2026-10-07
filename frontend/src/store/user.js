import { defineStore } from 'pinia'
import { login as loginApi, logout as logoutApi } from '@/api'

export const useUserStore = defineStore('user', {
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
      // ★ 先请求后端把 refresh 拉黑（失败不阻断本地清理）
      try {
        const rt = this.refresh || localStorage.getItem('refresh')
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
    }
  }
})