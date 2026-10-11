<template>
  <div class="cp-wrap">
    <div class="cp-card">
      <div class="cp-head">
        <el-icon :size="30" class="cp-icon"><Lock /></el-icon>
        <div>
          <div class="cp-title">
            {{ forced ? '请先修改初始密码' : '修改密码' }}
          </div>
          <div class="cp-sub">
            {{ forced
              ? '您的账号由管理员初始化，出于安全考虑需先设置新密码后方可使用系统。'
              : '为保障账号安全，请定期更换密码。' }}
          </div>
        </div>
      </div>

      <el-form ref="formRef" :model="form" :rules="rules" label-position="top"
               class="cp-form" @submit.prevent>
        <el-form-item label="原密码" prop="old_password">
          <el-input v-model="form.old_password" type="password" show-password
                    placeholder="请输入当前密码" autocomplete="current-password" />
        </el-form-item>
        <el-form-item label="新密码" prop="new_password">
          <el-input v-model="form.new_password" type="password" show-password
                    placeholder="至少 8 位" autocomplete="new-password" />
        </el-form-item>
        <el-form-item label="确认新密码" prop="confirm">
          <el-input v-model="form.confirm" type="password" show-password
                    placeholder="再次输入新密码" autocomplete="new-password" />
        </el-form-item>

        <div class="cp-actions">
          <el-button type="primary" :loading="loading" class="cp-btn" @click="submit">
            确认修改
          </el-button>
          <el-button v-if="!forced" class="cp-btn" @click="router.back()">返回</el-button>
          <el-button v-else link type="info" @click="logout">退出登录</el-button>
        </div>
      </el-form>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { changePassword } from '@/api'

const router = useRouter()
const formRef = ref(null)
const loading = ref(false)

// ★ 由路由 query 或本地标记判断是否「被动改密」（服务端强制）
const forced = ref(
  new URLSearchParams(window.location.hash.split('?')[1] || '').get('forced') === '1'
  || localStorage.getItem('must_change_password') === '1'
)

const form = reactive({ old_password: '', new_password: '', confirm: '' })

const rules = {
  old_password: [{ required: true, message: '请输入原密码', trigger: 'blur' }],
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 8, message: '新密码至少 8 位', trigger: 'blur' }
  ],
  confirm: [
    { required: true, message: '请再次输入新密码', trigger: 'blur' },
    {
      validator: (rule, value, cb) => {
        if (value !== form.new_password) cb(new Error('两次输入的新密码不一致'))
        else cb()
      },
      trigger: 'blur'
    }
  ]
}

async function submit() {
  const ok = await formRef.value?.validate().catch(() => false)
  if (!ok) return
  loading.value = true
  try {
    await changePassword({
      old_password: form.old_password,
      new_password: form.new_password
    })
    // ★ 后端改密成功后会拉黑该用户所有旧 refresh —— 本地 token 已失效，
    //   必须清干净并重新登录，否则后续请求全是 401。
    localStorage.removeItem('token')
    localStorage.removeItem('refresh')
    localStorage.removeItem('user')
    localStorage.removeItem('must_change_password')
    ElMessage.success('密码已修改，请使用新密码重新登录')
    router.replace('/login')
  } catch (e) {
    // 错误消息已由 request 拦截器统一提示
  } finally {
    loading.value = false
  }
}

function logout() {
  localStorage.clear()
  router.replace('/login')
}
</script>

<style scoped>
.cp-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #0f5c3f, #1c7a54);
  padding: 20px;
}
.cp-card {
  width: 100%;
  max-width: 430px;
  background: #fff;
  border-radius: 14px;
  padding: 28px 26px 22px;
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.25);
}
.cp-head { display: flex; gap: 12px; align-items: flex-start; margin-bottom: 18px; }
.cp-icon { color: #0f5c3f; margin-top: 2px; flex: 0 0 auto; }
.cp-title { font-size: 19px; font-weight: 700; color: #1f2d27; }
.cp-sub { font-size: 13px; color: #6b7a72; margin-top: 5px; line-height: 1.5; }
.cp-form :deep(.el-form-item) { margin-bottom: 16px; }
.cp-actions { display: flex; align-items: center; gap: 10px; margin-top: 6px; }
.cp-btn { flex: 1; }
</style>
