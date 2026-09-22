<template>
  <el-card shadow="never">
    <div class="toolbar">
      <el-radio-group v-model="filterKind" @change="load">
        <el-radio-button value="">全部</el-radio-button>
        <el-radio-button value="supply">供应</el-radio-button>
        <el-radio-button value="demand">求购</el-radio-button>
      </el-radio-group>
      <div style="flex:1"></div>
      <el-button type="success" @click="openDialog()">发布信息</el-button>
    </div>

    <el-row :gutter="16">
      <el-col :span="8" v-for="item in list" :key="item.id">
        <el-card shadow="hover" class="supply-card">
          <div class="supply-head">
            <el-tag :type="item.kind === 'supply' ? 'success' : 'warning'" size="small">
              {{ item.kind_display }}
            </el-tag>
            <el-tag :type="dealType(item.deal_status)" size="small" effect="plain">
              {{ item.deal_status_display }}
            </el-tag>
          </div>
          <div class="supply-title">{{ item.title }}</div>
          <div class="supply-meta">
            <span>品种：{{ item.variety || '不限' }}</span>
            <span>数量：{{ item.quantity_kg }} kg</span>
            <span>单价：¥{{ item.price }}/kg</span>
          </div>
          <div class="supply-meta">
            <span><el-icon><Location /></el-icon> {{ item.region || '未填' }}</span>
            <span><el-icon><Phone /></el-icon> {{ item.contact }} {{ item.phone }}</span>
          </div>
          <div v-if="item.cert_no" class="supply-cert">
            <el-icon><Postcard /></el-icon>
            关联合格证：<el-button link type="primary" size="small"
              @click="goVerify(item.cert_no)">{{ item.cert_no }}</el-button>
            <el-tag type="success" size="small">可验真</el-tag>
          </div>
          <div class="supply-desc">{{ item.description }}</div>
          <div class="supply-actions">
            <el-button type="primary" size="small" v-if="item.deal_status === 'open'"
                       @click="match(item)">标记对接</el-button>
            <el-button type="danger" size="small" @click="remove(item)">删除</el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>
    <el-empty v-if="!list.length" description="暂无供需信息" />

    <el-dialog v-model="dialogVisible" title="发布供需信息" width="560px">
      <el-form :model="form" label-width="110px">
        <el-form-item label="类型">
          <el-radio-group v-model="form.kind">
            <el-radio value="supply">供应</el-radio>
            <el-radio value="demand">求购</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="标题" required>
          <el-input v-model="form.title" />
        </el-form-item>
        <el-form-item label="产品名称">
          <el-input v-model="form.product_name" />
        </el-form-item>
        <el-form-item label="品种">
          <el-input v-model="form.variety" />
        </el-form-item>
        <el-form-item label="数量(kg)">
          <el-input-number v-model="form.quantity_kg" :min="0" style="width:100%" />
        </el-form-item>
        <el-form-item label="单价(元/kg)">
          <el-input-number v-model="form.price" :min="0" :precision="2" style="width:100%" />
        </el-form-item>
        <el-form-item label="地区">
          <el-input v-model="form.region" />
        </el-form-item>
        <el-form-item label="联系人">
          <el-input v-model="form.contact" />
        </el-form-item>
        <el-form-item label="联系电话">
          <el-input v-model="form.phone" />
        </el-form-item>
        <el-form-item label="关联合格证">
          <el-select v-model="form.certificate" style="width:100%" clearable
                     placeholder="选择合格证（可选，用于先验真再对接）">
            <el-option v-for="c in certs" :key="c.id"
                       :label="`${c.cert_no} - ${c.producer_name}`" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="详细说明">
          <el-input v-model="form.description" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="save">发布</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listSupply, createSupply, deleteSupply, matchSupply, listCertificate } from '@/api'

const router = useRouter()
const list = ref([])
const certs = ref([])
const filterKind = ref('')
const dialogVisible = ref(false)
const form = reactive({ kind: 'supply', product_name: '柑橘', quantity_kg: 0, price: 0 })

const dealType = (s) => ({ open: 'info', matched: 'success', closed: 'danger' }[s] || 'info')

const load = async () => {
  const params = { page_size: 100 }
  if (filterKind.value) params.kind = filterKind.value
  const res = await listSupply(params)
  list.value = res.results || res.data || []
}

const loadCerts = async () => {
  const res = await listCertificate({ page_size: 100 })
  certs.value = res.results || res.data || []
}

const openDialog = () => {
  Object.keys(form).forEach((k) => delete form[k])
  Object.assign(form, { kind: 'supply', product_name: '柑橘', quantity_kg: 0, price: 0 })
  dialogVisible.value = true
}

const save = async () => {
  if (!form.title) {
    ElMessage.warning('请填写标题')
    return
  }
  await createSupply(form)
  ElMessage.success('发布成功')
  dialogVisible.value = false
  load()
}

const match = async (item) => {
  await matchSupply(item.id)
  ElMessage.success('已标记为对接成功')
  load()
}

const remove = async (item) => {
  await ElMessageBox.confirm(`确认删除「${item.title}」？`, '提示', { type: 'warning' })
  await deleteSupply(item.id)
  ElMessage.success('已删除')
  load()
}

const goVerify = (certNo) => {
  const url = router.resolve(`/verify/${certNo}`).href
  window.open(url, '_blank')
}

onMounted(() => { load(); loadCerts() })
</script>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
.supply-card { margin-bottom: 16px; }
.supply-head { display: flex; justify-content: space-between; margin-bottom: 8px; }
.supply-title { font-size: 15px; font-weight: 600; margin-bottom: 10px; line-height: 1.5; }
.supply-meta {
  display: flex; flex-wrap: wrap; gap: 10px;
  font-size: 12px; color: #606266; margin-bottom: 6px;
}
.supply-cert {
  font-size: 12px; color: #0f5c3f; background: #f0f9f5;
  padding: 6px 8px; border-radius: 4px; margin: 8px 0;
  display: flex; align-items: center; gap: 4px; flex-wrap: wrap;
}
.supply-desc { font-size: 12px; color: #909399; line-height: 1.6; margin: 8px 0; }
.supply-actions { display: flex; gap: 8px; justify-content: flex-end; }
</style>