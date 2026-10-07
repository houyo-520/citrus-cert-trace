/**
 * 柑橘合格证追溯系统 —— 前端入口
 * 技术栈：Vue 3 + Vite + Pinia + Vue Router + Element Plus + ECharts
 */
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'

// Element Plus 组件库 + 中文语言包
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'

// 全量图标库
import * as Icons from '@element-plus/icons-vue'

// 全局样式（柑橘橙主题变量 + 布局 + 移动端收口）
import './styles/main.css'

const app = createApp(App)

// 注册图标：遍历图标导出项，逐个挂载为全局组件（业务侧 <el-icon><Xxx /></el-icon> 直接可用）
const iconEntries = Object.entries(Icons)
for (let i = 0; i < iconEntries.length; i++) {
  const [name, comp] = iconEntries[i]
  app.component(name, comp)
}

// 全局错误兜底：避免单个组件渲染异常导致整页白屏
app.config.errorHandler = (err, _instance, info) => {
  // 生产环境仅记录，开发环境下抛到控制台便于排查
  if (import.meta.env.DEV) {
    console.error('[vue-runtime-error]', info, err)
  }
}

app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })

app.mount('#app')