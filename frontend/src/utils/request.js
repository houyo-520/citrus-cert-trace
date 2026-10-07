import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from '@/router'

const request = axios.create({
  baseURL: '/api',
  timeout: 15000
})

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
  (error) => {
    const status = error.response?.status
    if (status === 401) {
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      if (router.currentRoute.value.path !== '/login') {
        ElMessage.error('登录已过期，请重新登录')
        router.push('/login')
      }
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
  const token = localStorage.getItem('token')
  const resp = await axios.get(url, {
    responseType: 'blob',
    headers: token ? { Authorization: 'Bearer ' + token } : {}
  })
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
  window.URL.revokeObjectURL(objectUrl)
}