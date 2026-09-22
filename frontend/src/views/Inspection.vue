<template>
  <div class="page">
    <!-- 顶部统计：执法闭环的全局态势 -->
    <el-row :gutter="12">
      <el-col :span="6">
        <el-card shadow="never" class="stat">
          <div class="stat-num">{{ stats.total }}</div>
          <div class="stat-label">检查总数</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" class="stat">
          <div class="stat-num warn">{{ stats.unqualified }}</div>
          <div class="stat-label">不合格</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" class="stat">
          <div class="stat-num">{{ stats.pending }}</div>
          <div class="stat-label">整改中/待整改</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="never" class="stat">
          <div class="stat-num danger">{{ stats.overdue }}</div>
          <div class="stat-label">已逾期</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never">
      <template #header>
        <div class="head">
          <span>监管现场监督检查</span>
          <div class="filters">
            <el-select v-model="query.check_result" placeholder="检查结果" clearable
                       size="small" style="width: 120px" @change="load">
              <el-option label="合格" value="qualified" />
              <el-option label="不合格" value="unqualified" />
              <el-option label="待复检" value="recheck" />
            </el-select>
            <el-select v-model="query.rectify_status" placeholder="整改状态" clearable
                       size="small" style="width: 130px" @change="load">
              <el-option label="无需整改" value="not_required" />
              <el-option label="待整改" value="pending" />
              <el-option label="整改中" value="ongoing" />
              <el-option label="已整改" value="done" />
              <el-option label="已逾期" value="overdue" />
              <el-option label="复查仍不合格" value="recheck_failed" />
            </el-select>
            <el-input v-model="query.keyword" placeholder="证号 / 检查人 / 地点" clearable
                      size="small" style="width: 190px" @keyup.enter="load" />
            <el-button size="small" @click="load">刷新</el-button>
            <el-button v-if="userStore.perms.can_inspect" size="small" type="primary"
                       @click="openDialog()">录入检查</el-button>
            <el-tag v-else size="small" type="info" effect="plain">
              只读：录入/复查需监管权限
            </el-tag>
          </div>
        </div>
      </template>

      <el-table :data="rows" v-loading="loading" border stripe size="small">
        <el-table-column prop="id" label="#" width="55" />
        <el-table-column label="合格证编号" width="185">
          <template #default="{ row }">
            <span class="mono">{{ row.cert_no || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="producer_name" label="生产主体" min-width="150"
                         show-overflow-tooltip />
        <el-table-column label="检查类型" width="100">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.check_type_display }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="检查时间" width="115">
          <template #default="{ row }">{{ (row.check_time || '').slice(0, 10) }}</template>
        </el-table-column>
        <el-table-column label="结果" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="resultType(row.check_result)">
              {{ row.check_result_display }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="评分" width="85">
          <template #default="{ row }">
            <span :class="scoreClass(row.check_score)">{{ row.check_score }}</span>
          </template>
        </el-table-column>
        <el-table-column label="整改状态" width="120">
          <template #default="{ row }">
            <el-tag size="small" :type="rectifyType(row.rectify_status)">
              {{ row.rectify_status_display }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="整改期限" width="110">
          <template #default="{ row }">
            <span :class="{ 'text-danger': row.is_overdue }">
              {{ row.rectify_deadline || '—' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="罚款(元)" width="100">
          <template #default="{ row }">
            <span v-if="Number(row.penalty_amount) > 0" class="text-danger">
              ¥{{ row.penalty_amount }}
            </span>
            <span v-else class="text-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="存证" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.is_anchored ? 'success' : 'info'">
              {{ row.is_anchored ? '已上链' : '未上链' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="showDetail(row)">
              详情
            </el-button>
            <el-button v-if="userStore.perms.can_rectify && canRectify(row)"
                       size="small" link type="success" @click="openRectify(row)">
              整改复查
            </el-button>
            <el-button v-if="userStore.perms.can_inspect" size="small" link type="danger"
                       @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        class="pager"
        background
        layout="total, prev, pager, next"
        :total="total"
        :page-size="pageSize"
        :current-page="page"
        @current-change="onPage"
      />
    </el-card>

    <!-- 录入检查 -->
    <el-dialog v-model="dialogVisible" title="录入监督检查记录" width="760px" top="5vh">
      <el-form :model="form" label-width="110px" size="small">
        <el-divider content-position="left">受检对象</el-divider>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="受检合格证" required>
              <el-select v-model="form.certificate" style="width:100%" filterable
                         placeholder="选择合格证（可搜索证号）">
                <el-option v-for="c in certs" :key="c.id"
                           :label="`${c.cert_no || '草稿#' + c.id} | ${c.producer_name_display}`"
                           :value="c.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="检查类型">
              <el-select v-model="form.check_type" style="width:100%">
                <el-option label="日常检查" value="routine" />
                <el-option label="专项检查" value="special" />
                <el-option label="投诉核查" value="complaint" />
                <el-option label="监督抽样" value="sampling" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="检查时间">
              <el-date-picker v-model="form.check_time" type="datetime"
                              value-format="YYYY-MM-DDTHH:mm:ss" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="检查地点">
              <el-input v-model="form.check_location" placeholder="如：XX 合作社分选车间" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="检查部门">
              <el-input v-model="form.department" placeholder="如：XX县农业农村局执法大队" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="执法证号">
              <el-input v-model="form.inspector_no" placeholder="如：ZJ5114020012" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider content-position="left">检查结论</el-divider>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="检查结果" required>
              <el-radio-group v-model="form.check_result" @change="onResultChange">
                <el-radio-button label="qualified">合格</el-radio-button>
                <el-radio-button label="unqualified">不合格</el-radio-button>
                <el-radio-button label="recheck">待复检</el-radio-button>
              </el-radio-group>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="检查评分">
              <el-input-number v-model="form.check_score" :min="0" :max="100"
                               :precision="2" style="width:100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="发现问题">
          <el-input v-model="form.problem_description" type="textarea" :rows="3"
                    placeholder="如实记录现场发现的问题；检查合格可写「未发现问题」" />
        </el-form-item>

        <el-alert v-if="form.check_result === 'unqualified'" type="warning"
                  :closable="false" show-icon class="tip">
          <template #title>
            判定不合格后，关联合格证将自动置为「暂停使用」（不是作废，复查合格可恢复），
            并生成一条监督检查存证上链 —— 事后任何一方都无法否认当时的判定。
          </template>
        </el-alert>

        <el-divider content-position="left">整改要求</el-divider>
        <el-form-item label="整改要求">
          <el-input v-model="form.rectify_requirement" type="textarea" :rows="3"
                    placeholder="如：7 日内补全农事记录台账并提交整改报告" />
        </el-form-item>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="整改期限">
              <el-date-picker v-model="form.rectify_deadline" type="date"
                              value-format="YYYY-MM-DD" style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="复查负责人">
              <el-input v-model="form.follow_up_person" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider content-position="left">行政处罚（可选）</el-divider>
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="罚款金额(元)">
              <el-input-number v-model="form.penalty_amount" :min="0" :precision="2"
                               style="width:100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="处罚事由">
              <el-input v-model="form.penalty_reason" placeholder="如：未按规定开具合格证" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="法律依据">
          <el-input v-model="form.legal_basis"
                    placeholder="如：《农产品质量安全法》第六十五条" />
        </el-form-item>
        <el-form-item label="现场证据材料">
          <el-input v-model="form.attachments"
                    placeholder="多个文件路径用英文逗号分隔，如 /media/insp/1.jpg,/media/insp/2.jpg" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="save">保存并上链存证</el-button>
      </template>
    </el-dialog>

    <!-- 整改复查 -->
    <el-dialog v-model="rectifyVisible" title="整改复查" width="620px">
      <el-descriptions :column="2" border size="small" class="mb">
        <el-descriptions-item label="合格证编号">
          <span class="mono">{{ current.cert_no || '—' }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="生产主体">{{ current.producer_name }}</el-descriptions-item>
        <el-descriptions-item label="检查结论">
          <el-tag size="small" :type="resultType(current.check_result)">
            {{ current.check_result_display }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="整改期限">{{ current.rectify_deadline || '—' }}</el-descriptions-item>
        <el-descriptions-item label="发现问题" :span="2">
          {{ current.problem_description || '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="整改要求" :span="2">
          {{ current.rectify_requirement || '—' }}
        </el-descriptions-item>
      </el-descriptions>

      <el-form :model="rectifyForm" label-width="100px" size="small">
        <el-form-item label="复查结论" required>
          <el-radio-group v-model="rectifyForm.result">
            <el-radio-button label="done">已整改（恢复合格证）</el-radio-button>
            <el-radio-button label="ongoing">整改中</el-radio-button>
            <el-radio-button label="recheck_failed">复查仍不合格</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="整改说明" required>
          <el-input v-model="rectifyForm.rectify_note" type="textarea" :rows="4"
                    placeholder="记录被检方的整改措施与现场复查情况" />
        </el-form-item>
        <el-form-item label="复查负责人">
          <el-input v-model="rectifyForm.follow_up_person" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="rectifyVisible = false">取消</el-button>
        <el-button type="primary" @click="saveRectify">提交复查结论</el-button>
      </template>
    </el-dialog>

    <!-- 详情 -->
    <el-dialog v-model="detailVisible" title="监督检查详情" width="700px">
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item label="合格证编号">
          <span class="mono">{{ current.cert_no || '—' }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="生产主体">{{ current.producer_name }}</el-descriptions-item>
        <el-descriptions-item label="检查类型">{{ current.check_type_display }}</el-descriptions-item>
        <el-descriptions-item label="检查时间">{{ (current.check_time || '').slice(0, 19).replace('T', ' ') }}</el-descriptions-item>
        <el-descriptions-item label="检查部门">{{ current.department || '—' }}</el-descriptions-item>
        <el-descriptions-item label="执法证号">{{ current.inspector_no || '—' }}</el-descriptions-item>
        <el-descriptions-item label="检查人">{{ current.inspector_name || '—' }}</el-descriptions-item>
        <el-descriptions-item label="检查地点">{{ current.check_location || '—' }}</el-descriptions-item>
        <el-descriptions-item label="检查结果">
          <el-tag size="small" :type="resultType(current.check_result)">
            {{ current.check_result_display }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="检查评分">
          <span :class="scoreClass(current.check_score)">{{ current.check_score }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="整改状态">
          <el-tag size="small" :type="rectifyType(current.rectify_status)">
            {{ current.rectify_status_display }}
          </el-tag>
          <el-tag v-if="current.is_overdue" size="small" type="danger" class="ml">已逾期</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="整改期限">{{ current.rectify_deadline || '—' }}</el-descriptions-item>
        <el-descriptions-item label="发现问题" :span="2">
          {{ current.problem_description || '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="整改要求" :span="2">
          {{ current.rectify_requirement || '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="整改说明" :span="2">
          {{ current.rectify_note || '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="整改完成时间">
          {{ (current.rectify_time || '').slice(0, 19).replace('T', ' ') || '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="复查负责人">{{ current.follow_up_person || '—' }}</el-descriptions-item>
        <el-descriptions-item label="罚款金额">
          <span v-if="Number(current.penalty_amount) > 0" class="text-danger">
            ¥{{ current.penalty_amount }}
          </span>
          <span v-else>—</span>
        </el-descriptions-item>
        <el-descriptions-item label="处罚事由">{{ current.penalty_reason || '—' }}</el-descriptions-item>
        <el-descriptions-item label="法律依据" :span="2">{{ current.legal_basis || '—' }}</el-descriptions-item>
        <el-descriptions-item label="结论存证哈希" :span="2">
          <span class="mono hash">{{ current.record_hash || '未存证' }}</span>
        </el-descriptions-item>
      </el-descriptions>
      <template #footer>
        <el-button v-if="userStore.perms.can_inspect && current.id" type="primary"
                   plain @click="reAnchor">重新存证</el-button>
        <el-button @click="detailVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listInspection, createInspection, deleteInspection, rectifyInspection,
  anchorInspection, listCertificate
} from '@/api'
import { useUserStore } from '@/store/user'

const userStore = useUserStore()
const loading = ref(false)
const rows = ref([])
const certs = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const query = reactive({ check_result: '', rectify_status: '', keyword: '' })

const dialogVisible = ref(false)
const rectifyVisible = ref(false)
const detailVisible = ref(false)
const current = ref({})
const form = reactive({})
const rectifyForm = reactive({})

// 顶部统计：从当前页数据聚合（真实场景应由 dashboard 接口下发，
// 这里保持页面自洽，避免额外请求）
const stats = computed(() => {
  const all = rows.value
  return {
    total: total.value,
    unqualified: all.filter((r) => r.check_result === 'unqualified').length,
    pending: all.filter((r) => ['pending', 'ongoing'].includes(r.rectify_status)).length,
    overdue: all.filter((r) => r.rectify_status === 'overdue').length
  }
})

const resultType = (v) => ({ qualified: 'success', unqualified: 'danger', recheck: 'warning' }[v] || 'info')
const rectifyType = (v) => ({
  not_required: 'info', pending: 'warning', ongoing: 'warning',
  done: 'success', overdue: 'danger', recheck_failed: 'danger'
}[v] || 'info')
const scoreClass = (s) => (Number(s) >= 85 ? 'text-success' : Number(s) >= 60 ? '' : 'text-danger')
const canRectify = (row) => ['pending', 'ongoing', 'overdue'].includes(row.rectify_status)

const load = async () => {
  loading.value = true
  try {
    const params = { page: page.value }
    if (query.check_result) params.check_result = query.check_result
    if (query.rectify_status) params.rectify_status = query.rectify_status
    if (query.keyword) params.keyword = query.keyword
    const res = await listInspection(params)
    const d = res.data
    rows.value = d.results || d
    total.value = d.count ?? rows.value.length
  } finally {
    loading.value = false
  }
}

const loadCerts = async () => {
  const res = await listCertificate({ page_size: 200 })
  certs.value = res.results || res.data || []
}

const onPage = (p) => {
  page.value = p
  load()
}

const openDialog = async () => {
  if (!certs.value.length) await loadCerts()
  Object.keys(form).forEach((k) => delete form[k])
  Object.assign(form, {
    check_type: 'routine',
    check_result: 'qualified',
    check_score: 95,
    check_time: new Date().toISOString().slice(0, 19),
    penalty_amount: 0,
    rectify_status: 'not_required'
  })
  dialogVisible.value = true
}

const onResultChange = (v) => {
  if (v === 'unqualified') {
    if (form.check_score > 80) form.check_score = 60
  } else if (v === 'qualified' && form.check_score < 80) {
    form.check_score = 95
  }
}

const save = async () => {
  if (!form.certificate) {
    ElMessage.warning('请选择受检合格证')
    return
  }
  const payload = { ...form }
  if (!payload.rectify_requirement && !payload.rectify_deadline) {
    payload.rectify_status = 'not_required'
  }
  await createInspection(payload)
  ElMessage.success('检查记录已保存并完成结论存证')
  dialogVisible.value = false
  load()
}

const openRectify = (row) => {
  current.value = row
  Object.keys(rectifyForm).forEach((k) => delete rectifyForm[k])
  Object.assign(rectifyForm, {
    result: 'done',
    rectify_note: '',
    follow_up_person: row.follow_up_person || ''
  })
  rectifyVisible.value = true
}

const saveRectify = async () => {
  if (!rectifyForm.rectify_note) {
    ElMessage.warning('请填写整改说明')
    return
  }
  await rectifyInspection(current.value.id, rectifyForm)
  ElMessage.success('复查结论已提交')
  rectifyVisible.value = false
  load()
}

const showDetail = (row) => {
  current.value = row
  detailVisible.value = true
}

const reAnchor = async () => {
  await anchorInspection(current.value.id)
  ElMessage.success('检查结论已重新存证')
  detailVisible.value = false
  load()
}

const remove = async (row) => {
  await ElMessageBox.confirm(`确认删除检查记录 #${row.id}？`, '提示', { type: 'warning' })
  await deleteInspection(row.id)
  ElMessage.success('已删除')
  load()
}

onMounted(() => { load(); loadCerts() })
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 12px; }
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
}
.filters { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.pager { margin-top: 12px; justify-content: flex-end; }
.stat { text-align: center; }
.stat-num { font-size: 26px; font-weight: 700; color: #303133; line-height: 1.2; }
.stat-num.warn { color: #e6a23c; }
.stat-num.danger { color: #f56c6c; }
.stat-label { font-size: 12px; color: #909399; margin-top: 4px; }
.mono { font-family: Consolas, Monaco, monospace; font-size: 12px; }
.hash { word-break: break-all; }
.text-danger { color: #f56c6c; font-weight: 600; }
.text-success { color: #67c23a; font-weight: 600; }
.text-muted { color: #c0c4cc; }
.tip { margin: 4px 0 12px; }
.mb { margin-bottom: 16px; }
.ml { margin-left: 6px; }
</style>
