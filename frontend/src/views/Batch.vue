<template>
  <el-card shadow="never">
    <div class="toolbar">
      <el-button type="primary" @click="load">刷新</el-button>
      <el-button type="success" @click="openDialog()">新增批次</el-button>
    </div>

    <el-table :data="list" v-loading="loading" border stripe>
      <el-table-column prop="batch_no" label="批次编号" width="160" />
      <el-table-column prop="producer_name" label="生产主体" min-width="160" />
      <el-table-column prop="product_name" label="产品" width="90" />
      <el-table-column prop="variety" label="品种" width="120" />
      <el-table-column prop="origin" label="产地" min-width="140" />
      <el-table-column prop="harvest_date" label="采收日期" width="120" />
      <el-table-column prop="weight_kg" label="重量(kg)" width="110" />
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button link type="warning" @click="anchor(row)">存证</el-button>
          <el-button link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" title="新增生产批次" width="560px">
      <el-form :model="form" label-width="110px">
        <el-form-item label="批次编号" required>
          <el-input v-model="form.batch_no" placeholder="如 BATCH-2026-1003" />
        </el-form-item>
        <el-form-item label="生产主体" required>
          <el-select v-model="form.producer" style="width:100%" placeholder="选择主体">
            <el-option v-for="p in producers" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="产品名称">
          <el-input v-model="form.product_name" />
        </el-form-item>
        <el-form-item label="品种">
          <el-input v-model="form.variety" />
        </el-form-item>
        <el-form-item label="产地">
          <el-input v-model="form.origin" />
        </el-form-item>
        <el-form-item label="采收日期">
          <el-date-picker v-model="form.harvest_date" type="date" value-format="YYYY-MM-DD"
                          style="width:100%" />
        </el-form-item>
        <el-form-item label="批次重量(kg)">
          <el-input-number v-model="form.weight_kg" :min="0" style="width:100%" />
        </el-form-item>
        <el-form-item label="种植面积(亩)">
          <el-input-number v-model="form.plant_area_mu" :min="0" style="width:100%" />
        </el-form-item>
        <el-form-item label="农事记录">
          <el-input v-model="form.farming_record" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { ref, onMounted, reactive } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listBatch, createBatch, deleteBatch, anchorBatch, listProducer } from '@/api'

const list = ref([])
const producers = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const form = reactive({ product_name: '柑橘', weight_kg: 0, plant_area_mu: 0 })

const load = async () => {
  loading.value = true
  try {
    const res = await listBatch({ page_size: 100 })
    list.value = res.results || res.data || []
  } finally {
    loading.value = false
  }
}

const loadProducers = async () => {
  const res = await listProducer({ page_size: 100 })
  producers.value = res.results || res.data || []
}

const openDialog = () => {
  Object.keys(form).forEach((k) => delete form[k])
  Object.assign(form, { product_name: '柑橘', weight_kg: 0, plant_area_mu: 0 })
  dialogVisible.value = true
}

const save = async () => {
  if (!form.batch_no || !form.producer) {
    ElMessage.warning('请填写批次编号并选择生产主体')
    return
  }
  await createBatch(form)
  ElMessage.success('批次创建成功')
  dialogVisible.value = false
  load()
}

const anchor = async (row) => {
  await anchorBatch(row.id)
  ElMessage.success('批次存证成功')
}

const remove = async (row) => {
  await ElMessageBox.confirm(`确认删除批次「${row.batch_no}」？`, '提示', { type: 'warning' })
  await deleteBatch(row.id)
  ElMessage.success('已删除')
  load()
}

onMounted(() => { load(); loadProducers() })
</script>

<style scoped>
.toolbar { display: flex; gap: 10px; margin-bottom: 14px; }
</style>