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