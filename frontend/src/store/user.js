import { defineStore } from 'pinia'
import { login as loginApi } from '@/api'

export const useUserStore = defineStore('user', {
  state: () => ({
    token: localStorage.getItem('token') || '',
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
    isProducer: (state) => (state.user?.role || '') === 'producer',
    isBuyer: (state) => (state.user?.role || '') === 'buyer'
  },
  actions: {
    async login(form) {
      const res = await loginApi(form)
      const data = res.data
      this.token = data.access
      this.user = data.user
      localStorage.setItem('token', data.access)
      localStorage.setItem('user', JSON.stringify(data.user))
      return data
    },
    setUser(user) {
      this.user = user
      localStorage.setItem('user', JSON.stringify(user))
    },
    logout() {
      this.token = ''
      this.user = null
      localStorage.removeItem('token')
      localStorage.removeItem('user')
    }
  }
})