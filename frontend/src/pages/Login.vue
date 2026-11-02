<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { LockOutlined, UserOutlined } from '@ant-design/icons-vue'
import { api, type User } from '../api'
import { auth } from '../auth'

const router = useRouter()
const needsSetup = ref<boolean | null>(null)
const busy = ref(false)
const form = ref({ username: '', password: '' })

onMounted(async () => {
  try {
    const r = await api<{ needs_setup: boolean }>('/api/auth/setup-status')
    needsSetup.value = r.needs_setup
  } catch {
    needsSetup.value = false
  }
})

async function submit() {
  busy.value = true
  try {
    const r = await api<{ token: string; user: User }>(
      needsSetup.value ? '/api/auth/setup' : '/api/auth/login',
      { body: form.value },
    )
    auth.login(r.token, r.user)
    router.push('/')
  } catch (e: any) {
    message.error(e.message ?? '登录失败')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="wrap">
    <a-card style="width: 100%; max-width: 380px">
      <a-typography-title :level="4" style="margin-top: 0">AgentOps Center</a-typography-title>
      <a-typography-paragraph type="secondary">
        AI Agent 统一管理、协作与自动化评测平台
      </a-typography-paragraph>

      <a-alert
        v-if="needsSetup"
        style="margin-bottom: 16px"
        type="info"
        show-icon
        message="首次启动：请创建管理员账号"
        description="系统没有默认口令，此账号拥有全部权限。"
      />

      <a-form layout="vertical" :model="form" :disabled="needsSetup === null" @finish="submit">
        <a-form-item name="username" label="用户名" :rules="[{ required: true, min: 2, message: '至少 2 位' }]">
          <a-input v-model:value="form.username" autocomplete="username">
            <template #prefix><UserOutlined /></template>
          </a-input>
        </a-form-item>
        <a-form-item name="password" label="密码" :rules="[{ required: true, min: 8, message: '至少 8 位' }]">
          <a-input-password
            v-model:value="form.password"
            :autocomplete="needsSetup ? 'new-password' : 'current-password'"
          >
            <template #prefix><LockOutlined /></template>
          </a-input-password>
        </a-form-item>
        <a-button type="primary" html-type="submit" block :loading="busy">
          {{ needsSetup ? '创建管理员并登录' : '登录' }}
        </a-button>
      </a-form>
    </a-card>
  </div>
</template>

<style scoped>
.wrap {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 16px;
}
</style>
