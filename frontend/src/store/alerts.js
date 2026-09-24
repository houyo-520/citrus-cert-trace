import { defineStore } from 'pinia'
import { getAlerts } from '@/api'

/**
 * 风险预警全局状态。
 *
 * 为什么要放 store 而不是各自组件里各请求一次：
 *   预警数据同时被「顶栏红点」和「看板预警条」两处使用，
 *   如果各拉一次就是 2 个请求、还可能显示不一致的数字（一个刷新了一个没刷）。
 *   统一收在这里，谁先加载谁触发，另一处直接读。
 *
 * 刷新时机：登录后首次进入、路由切换（节流 30 秒）、手动点刷新。
 * 不做轮询 —— 这是毕设演示系统，30 秒节流已足够，轮询只会让控制台刷满请求。
 */
export const useAlertStore = defineStore('alerts', {
  state: () => ({
    total: 0,
    danger: 0,
    alerts: [],
    loaded: false,
    lastAt: 0
  }),
  getters: {
    hasAlert: (s) => s.total > 0,
    hasDanger: (s) => s.danger > 0
  },
  actions: {
    /** 拉取预警；force=true 时忽略节流（用于手动刷新） */
    async fetch(force = false) {
      const now = Date.now()
      if (!force && this.loaded && now - this.lastAt < 30000) return
      try {
        const res = await getAlerts()
        const d = res.data || {}
        this.total = d.total || 0
        this.danger = d.danger || 0
        this.alerts = d.alerts || []
        this.loaded = true
        this.lastAt = now
      } catch (e) {
        // 预警是辅助信息，接口异常不应影响主流程渲染
        this.total = 0
        this.danger = 0
        this.alerts = []
      }
    },
    reset() {
      this.total = 0
      this.danger = 0
      this.alerts = []
      this.loaded = false
      this.lastAt = 0
    }
  }
})