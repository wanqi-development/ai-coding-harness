<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { getAuthConfig, login, type AuthConfig, type AuthUser } from '../lib/api'

const emit = defineEmits<{ authenticated: [user: AuthUser] }>()

const config = ref<AuthConfig | null>(null)
const username = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')

onMounted(async () => {
  try {
    config.value = await getAuthConfig()
  } catch (exc: any) {
    error.value = exc?.message ?? '认证配置加载失败。'
  }
})

async function submit() {
  if (busy.value || !username.value.trim() || !password.value) return
  busy.value = true
  error.value = ''
  try {
    emit('authenticated', await login(username.value, password.value))
  } catch (exc: any) {
    error.value = exc?.message ?? '登录失败'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="auth-page">
    <section class="auth-card">
      <div class="auth-brand">ai-coding-harness</div>
      <h1>登录工作台</h1>

      <template v-if="config && !config.initialized">
        <p class="auth-note">系统尚未创建管理员账号。请先在服务器执行：</p>
        <code class="auth-command">
          python -m ai_coding_harness.manage create-admin --username admin --claim-existing
        </code>
      </template>

      <form v-else class="auth-form" @submit.prevent="submit">
        <label>
          <span>用户名</span>
          <input v-model="username" autocomplete="username" autofocus />
        </label>
        <label>
          <span>密码</span>
          <input v-model="password" type="password" autocomplete="current-password" />
        </label>
        <p v-if="error" class="auth-error">{{ error }}</p>
        <button class="primary" type="submit" :disabled="busy || !config">
          {{ busy ? '正在登录…' : '登录' }}
        </button>
      </form>

      <p v-if="config === null && !error" class="auth-note">正在读取认证配置…</p>
    </section>
  </main>
</template>
