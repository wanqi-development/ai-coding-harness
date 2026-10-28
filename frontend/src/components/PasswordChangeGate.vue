<script setup lang="ts">
import { ref } from 'vue'
import { changePassword, type AuthUser } from '../lib/api'

const props = defineProps<{ user: AuthUser }>()
const emit = defineEmits<{ changed: [user: AuthUser]; logout: [] }>()

const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const busy = ref(false)
const error = ref('')

async function submit(): Promise<void> {
  error.value = ''
  if (!currentPassword.value || !newPassword.value) {
    error.value = '请填写当前密码和新密码。'
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    error.value = '两次输入的新密码不一致。'
    return
  }
  busy.value = true
  try {
    emit('changed', await changePassword(currentPassword.value, newPassword.value))
  } catch (exc: any) {
    error.value = exc?.message ?? '修改密码失败'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="auth-page">
    <section class="auth-card">
      <div class="auth-brand">ai-coding-harness</div>
      <h1>请先修改密码</h1>
      <p class="auth-note">
        账号 <strong>{{ props.user.display_name }}</strong>（@{{ props.user.username }}）
        使用的是初始或管理员重置的临时密码。
      </p>
      <form class="auth-form" @submit.prevent="submit">
        <label>
          <span>当前临时密码</span>
          <input v-model="currentPassword" type="password" autocomplete="current-password" autofocus />
        </label>
        <label>
          <span>新密码</span>
          <input v-model="newPassword" type="password" autocomplete="new-password" />
        </label>
        <label>
          <span>确认新密码</span>
          <input v-model="confirmPassword" type="password" autocomplete="new-password" />
        </label>
        <p v-if="error" class="auth-error">{{ error }}</p>
        <div class="password-gate-actions">
          <button class="btn" type="button" :disabled="busy" @click="emit('logout')">退出登录</button>
          <button class="btn btn-primary" type="submit" :disabled="busy">
            {{ busy ? '修改中…' : '修改并进入工作台' }}
          </button>
        </div>
      </form>
    </section>
  </main>
</template>
