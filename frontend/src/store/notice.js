/**
 * 站内消息全局状态。
 *
 * 为什么用全局单例而不是每个页面各拉一次：
 *   顶栏铃铛和消息页面显示的是同一个「未读数」。如果各自维护一份，
 *   用户在消息页读了 3 条，回到看板铃铛还显示旧的 5 条 —— 数字不一致
 *   会直接摧毁用户对系统的信任（"到底有几条没读？"）。
 *   因此统一放 store，任何一处读了消息都调 markRead 让两边同步。
 *
 * 节流策略：30 秒内不重复请求（和预警 store 一致），避免切页时打爆接口。
 */
import { defineStore } from 'pinia'
import {
  notificationUnread, readNotification, readAllNotification
} from '@/api'

const THROTTLE_MS = 30 * 1000

export const useNoticeStore = defineStore('notice', {
  state: () => ({
    unread: 0,
    danger: 0,
    byCategory: [],
    loaded: false,
    lastAt: 0
  }),
  getters: {
    hasUnread: (state) => state.unread > 0,
    hasDanger: (state) => state.danger > 0,
    // 铃铛角标上限 99+，避免四位数撑破布局
    badgeText: (state) => (state.unread > 99 ? '99+' : String(state.unread))
  },
  actions: {
    /**
     * 拉取未读汇总。
     * @param {boolean} force 忽略节流强制刷新（用户手动点刷新时用）
     */
    async fetch(force = false) {
      const now = Date.now()
      if (!force && this.lastAt && now - this.lastAt < THROTTLE_MS) return
      this.lastAt = now
      try {
        const res = await notificationUnread()
        const d = res.data || {}
        this.unread = d.unread || 0
        this.danger = d.danger || 0
        this.byCategory = d.by_category || []
        this.loaded = true
      } catch (e) {
        // 未读汇总失败不该弹错误提示打扰用户（顶栏是个静默组件）
      }
    },

    /** 标记单条已读，并就地扣减未读数（不重新请求，保证界面即时响应）。 */
    async markRead(id) {
      try {
        await readNotification(id)
        if (this.unread > 0) this.unread -= 1
      } catch (e) { /* 静默 */ }
    },

    /** 全部已读。 */
    async markAllRead() {
      try {
        await readAllNotification()
        this.unread = 0
        this.danger = 0
        this.byCategory = []
      } catch (e) { /* 静默 */ }
    },

    /** 退出登录时清空，防止下个账号登录闪出上个账号的角标。 */
    reset() {
      this.unread = 0
      this.danger = 0
      this.byCategory = []
      this.loaded = false
      this.lastAt = 0
    }
  }
})
