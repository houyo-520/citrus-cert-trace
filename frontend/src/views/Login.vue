<template>
  <div class="login-page">
    <div class="login-box">
      <div class="login-left">
        <div class="login-left-mask"></div>
        <div class="login-left-content">
          <div class="brand">
            <el-icon :size="40"><Orange /></el-icon>
            <h1>柑橘合格证追溯系统</h1>
            <p>基于可信存证与二维码追溯的农产品合格证管理平台</p>
          </div>
          <ul class="feature-list">
            <li>SHA-256 哈希存证，防篡改可验真</li>
            <li>合格证二维码追溯，扫码即查</li>
            <li>产销供需对接，打通产证销闭环</li>
          </ul>
        </div>
      </div>

      <div class="login-right">
        <h2>用户登录</h2>
        <el-form :model="form" :rules="rules" ref="formRef" @keyup.enter="onSubmit">
          <el-form-item prop="username">
            <el-input v-model="form.username" placeholder="请输入用户名" size="large">
              <template #prefix><el-icon><User /></el-icon></template>
            </el-input>
          </el-form-item>
          <el-form-item prop="password">
            <el-input v-model="form.password" type="password" placeholder="请输入密码"
                      size="large" show-password>
              <template #prefix><el-icon><Lock /></el-icon></template>
            </el-input>
          </el-form-item>
          <el-button type="primary" size="large" :loading="loading"
                     style="width:100%" @click="onSubmit">登 录</el-button>
        </el-form>

        <div class="verify-entry">
          <el-button link type="primary" @click="$router.push('/verify')">
            公众验真入口（免登录）→
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useUserStore } from '@/store/user'

const router = useRouter()
const userStore = useUserStore()
const formRef = ref()
const loading = ref(false)
const form = reactive({ username: '', password: '' })
const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }]
}

const onSubmit = async () => {
  await formRef.value.validate()
  loading.value = true
  try {
    await userStore.login(form)
    ElMessage.success('登录成功')
    router.push('/dashboard')
  } catch (e) {
    // 拦截器已提示
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  position: relative;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #0f5c3f url('@/assets/login-citrus.png') center / cover no-repeat;
}
.login-page::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(180deg, rgba(8, 40, 28, 0.78) 0%, rgba(10, 55, 40, 0.55) 50%, rgba(8, 40, 28, 0.82) 100%);
}
.login-box {
  position: relative;
  z-index: 1;
  display: flex;
  width: 860px;
  min-height: 460px;
  background: #fff;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 12px 40px rgba(0,0,0,0.28);
}
.login-left {
  flex: 1;
  position: relative;
  background: #0f5c3f url('@/assets/login-citrus.png') center / cover no-repeat;
  color: #fff;
  padding: 44px 36px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  overflow: hidden;
}
.login-left-mask {
  position: absolute;
  inset: 0;
  background: linear-gradient(180deg, rgba(10, 60, 40, 0.72) 0%, rgba(10, 60, 40, 0.45) 45%, rgba(10, 60, 40, 0.82) 100%);
}
.login-left-content {
  position: relative;
  z-index: 1;
}
.brand h1 { font-size: 22px; margin: 14px 0 8px; }
.brand p { font-size: 13px; color: #b7e4cf; line-height: 1.7; }
.feature-list { margin-top: 26px; padding-left: 18px; line-height: 2.1; font-size: 13px; color: #d4f0e3; }
.login-right { flex: 1; padding: 44px 40px; display: flex; flex-direction: column; justify-content: center; }
.login-right h2 { font-size: 18px; margin: 0 0 22px; text-align: center; }
.verify-entry { margin-top: 16px; text-align: center; }
</style>