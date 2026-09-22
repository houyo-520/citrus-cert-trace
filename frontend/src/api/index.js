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

// ---- 合格证 ----
export const listCertificate = (params) => request.get('/certificate/', { params })
export const createCertificate = (data) => request.post('/certificate/', data)
export const issueCertificate = (id) => request.post(`/certificate/${id}/issue/`)
export const revokeCertificate = (id) => request.post(`/certificate/${id}/revoke/`)
export const deleteCertificate = (id) => request.delete(`/certificate/${id}/`)

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

// ---- 公众验真（免登录） ----
export const publicVerify = (certNo) => request.get(`/public/verify/${certNo}/`)

// ---- 看板 ----
export const getDashboard = () => request.get('/dashboard/')