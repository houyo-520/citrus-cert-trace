import request from '@/utils/request'

// ---- 认证 ----
export const login = (data) => request.post('/auth/login/', data)
export const getProfile = () => request.get('/auth/profile/')

// ---- 生产主体 ----
export const listProducer = (params) => request.get('/producer/', { params })
export const createProducer = (data) => request.post('/producer/', data)
export const updateProducer = (id, data) => request.put(`/producer/${id}/`, data)
export const deleteProducer = (id) => request.delete(`/producer/${id}/`)
export const auditProducer = (id, audit_status) =>
  request.post(`/producer/${id}/audit/`, { audit_status })

// ---- 生产批次 ----
export const listBatch = (params) => request.get('/batch/', { params })
export const createBatch = (data) => request.post('/batch/', data)
export const updateBatch = (id, data) => request.put(`/batch/${id}/`, data)
export const deleteBatch = (id) => request.delete(`/batch/${id}/`)
export const anchorBatch = (id) => request.post(`/batch/${id}/anchor/`)

// ---- 检测报告 ----
export const listReport = (params) => request.get('/report/', { params })
export const createReport = (data) => request.post('/report/', data)
export const deleteReport = (id) => request.delete(`/report/${id}/`)
// 检测报告附件：必须用 multipart/form-data 提交（axios 会自动带 boundary）
export const uploadReportFile = (id, file) => {
  const fd = new FormData()
  fd.append('file', file)
  return request.patch(`/report/${id}/`, fd)
}

// ---- 合格证 ----
export const listCertificate = (params) => request.get('/certificate/', { params })
export const createCertificate = (data) => request.post('/certificate/', data)
export const updateCertificate = (id, data) => request.put(`/certificate/${id}/`, data)
export const issueCertificate = (id) => request.post(`/certificate/${id}/issue/`)
export const submitCertificate = (id) => request.post(`/certificate/${id}/submit/`)
export const rejectCertificate = (id, reason) =>
  request.post(`/certificate/${id}/reject/`, { reason })
export const revokeCertificate = (id, reason) =>
  request.post(`/certificate/${id}/revoke/`, { reason })
export const deleteCertificate = (id) => request.delete(`/certificate/${id}/`)
// ★ 预看下一个可用编号（不占用）：让监管直观看到 19 位编号规则
export const peekCertNo = (producerId) =>
  request.get('/certificate/peek-cert-no/', { params: { producer: producerId } })
// ★ 某张合格证的监督检查汇总
export const certInspectionSummary = (id) =>
  request.get(`/certificate/${id}/inspection-summary/`)

// ---- 监督检查（监管执法闭环）----
export const listInspection = (params) => request.get('/inspection/', { params })
export const createInspection = (data) => request.post('/inspection/', data)
export const updateInspection = (id, data) => request.put(`/inspection/${id}/`, data)
export const deleteInspection = (id) => request.delete(`/inspection/${id}/`)
// ★ 整改复查：提交整改说明 + 复查结论
export const rectifyInspection = (id, data) =>
  request.post(`/inspection/${id}/rectify/`, data)
// ★ 检查结论重新存证
export const anchorInspection = (id) => request.post(`/inspection/${id}/anchor/`)

// ---- 存证 ----
export const listEvidence = (params) => request.get('/evidence/', { params })
export const verifyEvidence = (certNo, withChain = false) =>
  request.get(`/evidence/verify/${certNo}/`, { params: withChain ? { chain: 1 } : {} })
export const chainStatus = () => request.get('/evidence/chain-status/')

// ---- 产销供需 ----
export const listSupply = (params) => request.get('/supply/', { params })
export const createSupply = (data) => request.post('/supply/', data)
export const deleteSupply = (id) => request.delete(`/supply/${id}/`)
export const matchSupply = (id) => request.post(`/supply/${id}/match/`)
// ★ 智能匹配推荐（返回按匹配度降序的候选，含各因子得分明细）
export const recommendSupply = (id, limit = 5) =>
  request.get(`/supply/${id}/recommend/`, { params: { limit } })
// ★ 发起产销意向单
export const createIntent = (id, data) => request.post(`/supply/${id}/intent/`, data)

// ---- 产销意向单 ----
export const listIntent = (params) => request.get('/intent/', { params })
export const respondIntent = (id, status) =>
  request.post(`/intent/${id}/respond/`, { status })
export const deleteIntent = (id) => request.delete(`/intent/${id}/`)

// ---- 操作日志 ----
export const listLog = (params) => request.get('/log/', { params })

// ---- 权限矩阵 ----
export const getRoleMatrix = () => request.get('/role-matrix/')

// ---- 公众验真（免登录） ----
export const publicVerify = (certNo) => request.get(`/public/verify/${certNo}/`)

// ---- 看板 ----
export const getDashboard = () => request.get('/dashboard/')
