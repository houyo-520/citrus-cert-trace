import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from '@/router'

const request = axios.create({
  baseURL: '/api',
  timeout: 15000
})

/**
 * ★ 静默续期（单飞，B-1 修复）
 *
 * 背景：进入后台首页会**并发**触发多个请求（MainLayout 的 alert+notice、
 * Dashboard 的 Promise.allSettled([getDashboard, getAlerts])），access 过期后
 * 第一屏就是 4 个并发 401。若每个 401 各自去刷新，因为 ROTATE+BLACKLIST 会把
 * 旧 refresh 拉黑：A 刷新成功后，B/C/D 拿着**已被拉黑的旧 refresh** 再请求 →
 * 401 → 落到登出分支 → 用户仍被踢回登录页（只是从「没刷新」变成「刷新打架」）。
 *
 * 修法：模块级 refreshing 单飞 —— 同一时刻只发起一次刷新，其余 401 等待它。
 */
let refreshing = null
// ★ 登出闸门：并发 401 失败时避免弹多次「登录已过期」/重复跳转
let loggingOut = false

async function renewTokens() {
  const refresh = localStorage.getItem('refresh')
  if (!refresh) return false
  try {
    // ★ 带 timeout：原生 axios 无实例默认值，服务端挂住时不要让所有重放一起卡死
    const r = await axios.post('/api/auth/refresh/', { refresh }, { timeout: 10000 })
    if (!r.data?.access) return false
    localStorage.setItem('token', r.data.access)
    if (r.data.refresh) localStorage.setItem('refresh', r.data.refresh)
    return true
  } catch (e) {
    return false
  }
}

/**
 * ★ 统一登出（走 loggingOut 闸门）
 *
 * 主拦截器与 downloadFile 共用，避免「多处各写一份登出逻辑」导致有的走闸门、
 * 有的不走（那就会出现重复弹窗）。闸门用短暂时间窗解锁，允许后续真的再次过期时再提示。
 */
function forceLogout() {
  if (loggingOut) return
  loggingOut = true
  localStorage.removeItem('token')
  localStorage.removeItem('refresh')
  localStorage.removeItem('user')
  if (router.currentRoute.value.path !== '/login') {
    ElMessage.error('登录已过期，请重新登录')
    router.push('/login')
  }
  // 跳转后短暂解锁，允许后续真的再次过期时再提示
  setTimeout(() => { loggingOut = false }, 1500)
}

request.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = 'Bearer ' + token
  }
  return config
})

/**
 * ★ 响应形状归一化
 *
 * 后端存在两类返回体，前端页面历史上两种写法混用，导致
 * `Cannot read properties of undefined (reading 'results')` 一类报错：
 *   ① DRF 分页体  { count, next, previous, results }
 *   ② 自定义包装体 { code, message, data }
 *
 * 这里统一补齐别名，使 `res.results` 与 `res.data` 两种取值方式都能工作，
 * 页面无需再关心自己调用的是哪一类接口。
 */
function normalize(res) {
  if (!res || typeof res !== 'object' || Array.isArray(res)) return res

  if ('results' in res) {
    // ① 分页体：补 data 别名（指向列表本身）
    if (!('data' in res)) res.data = res.results
    if (res.count === undefined) res.count = res.results.length
  } else if ('code' in res && 'data' in res) {
    // ② 包装体：data 为列表时补 results / count 别名
    const d = res.data
    if (Array.isArray(d)) {
      if (!('results' in res)) res.results = d
      if (res.count === undefined) res.count = d.length
    }
  }
  return res
}

request.interceptors.response.use(
  (response) => {
    const res = response.data
    if (res && res.code !== undefined && res.code !== 200) {
      ElMessage.error(res.message || '请求失败')
      return Promise.reject(new Error(res.message || 'Error'))
    }
    return normalize(res)
  },
  async (error) => {
    const status = error.response?.status
    const original = error.config || {}

    // ★ B-1：401 静默续期走**单飞**，避免并发 401 各自刷新的踩踏。
    //   重放时不用手设 Authorization —— 请求拦截器会从 localStorage 取新 token。
    if (status === 401 && !original._retry && !original.url?.includes('/auth/refresh')) {
      original._retry = true
      if (!refreshing) {
        // finally 保证无论成败都释放锁，避免一次失败把后续刷新永久堵死
        refreshing = renewTokens().finally(() => { refreshing = null })
      }
      if (await refreshing) {
        return request(original)
      }
    }

    if (status === 401) {
      // ★ 统一走 forceLogout（内部有 loggingOut 闸门）：
      //   并发 4 个 401 同时失败时只提示/跳转一次。
      forceLogout()
    } else {
      ElMessage.error(error.response?.data?.message || error.message || '网络错误')
    }
    return Promise.reject(error)
  }
)

export default request

/**
 * ★ 鉴权下载（B2 修复）
 *
 * 背景：`/media/` 已改为需登录（ProtectedMediaView）。前端若还用
 * `<a href="/media/...">` / `el-link :href` 直接导航，浏览器**不会带上
 * Authorization 头**（token 只加在 axios 拦截器里），必然 401。
 *
 * 这里用 axios 以 blob 方式请求（经拦截器带上 token），拿到二进制后
 * 用 createObjectURL 触发浏览器下载。url 可是 `/media/...` 绝对路径，
 * 也可是后端给的相对地址。
 *
 * @param {string} url      文件地址（如 row.file_url）
 * @param {string} filename 另存为的文件名（可选）
 */
export async function downloadFile(url, filename = '') {
  if (!url) {
    ElMessage.warning('文件地址为空')
    return
  }
  // 后端返回的是 /media/... 相对路径；axios 实例 baseURL='/api'，故这里
  // 用原生 axios 发同源绝对路径请求，仅手动补 Authorization。
  const doGet = (tok) => axios.get(url, {
    responseType: 'blob',
    timeout: 60000,   // 附件可能较大，给足超时（原生 axios 无实例默认值）
    headers: tok ? { Authorization: 'Bearer ' + tok } : {}
  })
  let resp
  try {
    resp = await doGet(localStorage.getItem('token'))
  } catch (err) {
    // ★ R6/B-1：原生 axios 不经 request 拦截器，401 需在这里处理。
    //   与主拦截器一致：先尝试**单飞静默续期**，成功则重试一次，失败才登出。
    const st = err.response?.status
    if (st === 401) {
      if (!refreshing) {
        refreshing = renewTokens().finally(() => { refreshing = null })
      }
      const ok = await refreshing
      if (ok) {
        try {
          resp = await doGet(localStorage.getItem('token'))
        } catch (e2) {
          resp = null
        }
      }
      if (!resp) {
        forceLogout()
        throw err
      }
    } else if (st === 403) {
      ElMessage.error('无权访问该附件')
      throw err
    } else {
      ElMessage.error('附件下载失败')
      throw err
    }
  }
  const blob = new Blob([resp.data], {
    type: resp.headers['content-type'] || 'application/octet-stream'
  })
  const objectUrl = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  // 优先用调用方给的名字；否则从 Content-Disposition 或 url 末段推断
  let name = filename
  const cd = resp.headers['content-disposition'] || ''
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd)
  if (!name && m) name = decodeURIComponent(m[1])
  if (!name) name = url.split('/').pop() || 'download'
  a.download = name
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  // ★ R6：延后回收 —— 紧跟 a.click() 同步 revoke 在 Safari / 大文件下有
  //   被截断的先例，惯例放到下一帧再释放。
  setTimeout(() => window.URL.revokeObjectURL(objectUrl), 1000)
}