<template>
  <div class="notice-page">
    <el-card shadow="never">
      <div class="toolbar">
        <div class="tb-left">
          <el-button type="primary" @click="load">刷新</el-button>
          <el-button :disabled="!noticeStore.hasUnread" @click="markAll">
            全部已读
          </el-button>
          <el-button @click="clearRead">清理已读</el-button>
        </div>
        <div class="tb-right">
          <el-radio-group v-model="filterUnread" size="small" @change="load">
            <el-radio-button :value="false">全部</el-radio-button>
            <el-radio-button :value="true">
              未读<template v-if="noticeStore.hasUnread">
                （{{ noticeStore.unread }}）
              </template>
            </el-radio-button>
          </el-radio-group>
          <el-select v-model="filterCategory" placeholder="全部类型" clearable
                     size="small" style="width:150px" @change="load">
            <el-option v-for="c in categories" :key="c.key"
                       :label="c.label" :value="c.key" />
          </el-select>
        </div>
      </div>

      <el-empty v-if="!loading && !list.length"
                :description="filterUnread ? '没有未读消息' : '暂无消息'" />

      <div v-else class="notice-list" v-loading="loading">
        <div v-for="n in list" :key="n.id" class="notice-item"
             :class="[`lv-${n.level}`, { unread: !n.is_read }]">
          <div class="ni-icon">
            <el-icon :size="20"><component :is="iconOf(n.category)" /></el-icon>
          </div>
          <div class="ni-body">
            <div class="ni-head">
              <span class="ni-title">{{ n.title }}</span>
              <el-tag size="small" :type="levelTag(n.level)" effect="plain">
                {{ n.category_display }}
              </el-tag>
              <span v-if="!n.is_read" class="ni-dot">未读</span>
            </div>
            <div v-if="n.content" class="ni-content">{{ n.content }}</div>
            <div class="ni-foot">
              <span class="ni-time">{{ n.created_at }}</span>
              <span v-if="n.actor_name" class="ni-actor">来自 {{ n.actor_name }}</span>
              <span v-if="n.biz_no" class="ni-biz">{{ n.biz_no }}</span>
            </div>
          </div>
          <div class="ni-actions">
            <el-button v-if="n.link_route" link type="primary" size="small"
                       @click="goto(n)">
              {{ n.link_label || '查看' }} →
            </el-button>
            <el-button v-if="!n.is_read" link type="info" size="small"
                       @click="markOne(n)">标为已读</el-button>
            <el-button link type="danger" size="small" @click="remove(n)">删除</el-button>
          </div>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listNotification, deleteNotification, notificationCategories
} from '@/api'
import { useNoticeStore } from '@/store/notice'

const router = useRouter()
const noticeStore = useNoticeStore()

const list = ref([])
const categories = ref([])
const loading = ref(false)
const filterUnread = ref(false)
const filterCategory = ref('')

// 消息类型 → 图标：让用户扫一眼就知道是什么事（比读文字快）
const ICONS = {
  cert_submit: 'Postcard', cert_issue: 'Postcard', cert_reject: 'Postcard',
  cert_revoke: 'Postcard', cert_suspend: 'WarningFilled',
  inspection: 'Stamp', rectify: 'Refresh', match: 'Connection',
  audit: 'OfficeBuilding', system: 'Bell'
}
const iconOf = (c) => ICONS[c] || 'Bell'
const levelTag = (lv) => ({
  info: 'info', success: 'success', warning: 'warning', danger: 'danger'
}[lv] || 'info')

const load = async () => {
  loading.value = true
  try {
    const params = { page_size: 100 }
    if (filterUnread.value) params.unread = 1
    if (filterCategory.value) params.category = filterCategory.value
    const res = await listNotification(params)
    list.value = res.results || res.data || []
  } finally {
    loading.value = false
  }
}

const loadCategories = async () => {
  const res = await notificationCategories()
  categories.value = res.data?.categories || []
}

const markOne = async (n) => {
  await noticeStore.markRead(n.id)
  n.is_read = true
  n.read_at = new Date().toISOString()
}

const markAll = async () => {
  await noticeStore.markAllRead()
  ElMessage.success('已全部标为已读')
  load()
}

const clearRead = async () => {
  await ElMessageBox.confirm('确认清理所有已读消息？未读消息不受影响。',
                             '提示', { type: 'warning' })
  const { clearReadNotification } = await import('@/api')
  const res = await clearReadNotification()
  ElMessage.success(res.message || '已清理')
  load()
}

const remove = async (n) => {
  await deleteNotification(n.id)
  ElMessage.success('已删除')
  // 删的是未读消息时，未读数也要跟着减，否则铃铛数字永远不归零
  if (!n.is_read) await noticeStore.fetch(true)
  load()
}

const goto = async (n) => {
  if (!n.is_read) await markOne(n)
  if (n.link_route) router.push(`/${n.link_route}`)
}

onMounted(() => { load(); loadCategories(); noticeStore.fetch(true) })
</script>

<style scoped>
.toolbar {
  display: flex; justify-content: space-between; align-items: center;
  gap: 12px; margin-bottom: 14px; flex-wrap: wrap;
}
.tb-left, .tb-right { display: flex; gap: 10px; align-items: center; }

.notice-list { display: flex; flex-direction: column; gap: 8px; }

.notice-item {
  display: flex; gap: 12px; align-items: flex-start;
  padding: 12px 14px;
  border: 1px solid #ebeef5;
  border-left: 3px solid #dcdfe6;
  border-radius: 8px;
  background: #fff;
  transition: background 0.15s;
}
.notice-item:hover { background: #fafbfc; }
/* 未读用浅底 + 左侧色条区分：比只改文字颜色更容易一眼扫到 */
.notice-item.unread { background: #f7fbf9; }
.notice-item.lv-success { border-left-color: #67c23a; }
.notice-item.lv-info { border-left-color: #409eff; }
.notice-item.lv-warning { border-left-color: #e6a23c; }
.notice-item.lv-danger { border-left-color: #f56c6c; }

.ni-icon {
  width: 36px; height: 36px; flex: 0 0 36px;
  display: flex; align-items: center; justify-content: center;
  border-radius: 50%; background: #f2f6fc; color: #606266;
}
.lv-success .ni-icon { background: #f0f9eb; color: #67c23a; }
.lv-info .ni-icon { background: #ecf5ff; color: #409eff; }
.lv-warning .ni-icon { background: #fdf6ec; color: #e6a23c; }
.lv-danger .ni-icon { background: #fef0f0; color: #f56c6c; }

.ni-body { flex: 1; min-width: 0; }
.ni-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ni-title { font-size: 14px; font-weight: 600; color: #303133; }
.ni-dot {
  font-size: 11px; color: #fff; background: #f56c6c;
  padding: 1px 6px; border-radius: 8px;
}
.ni-content {
  font-size: 13px; color: #606266; line-height: 1.7;
  margin-top: 6px; white-space: pre-wrap; word-break: break-word;
}
.ni-foot {
  display: flex; gap: 14px; flex-wrap: wrap;
  font-size: 12px; color: #909399; margin-top: 8px;
}
.ni-actions {
  display: flex; flex-direction: column; align-items: flex-end;
  gap: 2px; flex: 0 0 auto;
}

/* ---- 移动端：操作按钮横排到底部，避免右侧挤压正文 ---- */
@media (max-width: 640px) {
  .toolbar { flex-direction: column; align-items: stretch; }
  .tb-left, .tb-right { flex-wrap: wrap; }
  .notice-item { flex-wrap: wrap; }
  .ni-actions {
    flex-direction: row; width: 100%;
    justify-content: flex-end; border-top: 1px dashed #ebeef5;
    padding-top: 6px; margin-top: 2px;
  }
}
</style>
