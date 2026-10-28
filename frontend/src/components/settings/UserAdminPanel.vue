<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import {
  createUser,
  listUsers,
  resetUserPassword,
  revokeUserSessions,
  updateUser,
  type ManagedUser,
} from '../../lib/api'

const props = defineProps<{ currentUserId: number }>()

const users = ref<ManagedUser[]>([])
const loading = ref(false)
const busyId = ref<number | null>(null)
const error = ref('')
const notice = ref('')
const form = reactive({
  username: '',
  display_name: '',
  role: 'member' as 'admin' | 'member',
  temporary_password: '',
})
const editingNameId = ref<number | null>(null)
const editingName = ref('')
const resetTarget = ref<ManagedUser | null>(null)
const resetPassword = ref('')

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    users.value = await listUsers()
  } catch (exc: any) {
    error.value = exc?.message ?? '用户列表加载失败'
  } finally {
    loading.value = false
  }
}

async function create(): Promise<void> {
  error.value = ''
  notice.value = ''
  if (!form.username.trim() || !form.temporary_password) {
    error.value = '用户名和初始密码不能为空。'
    return
  }
  loading.value = true
  try {
    const user = await createUser({
      username: form.username,
      display_name: form.display_name,
      role: form.role,
      temporary_password: form.temporary_password,
    })
    users.value.push(user)
    form.username = ''
    form.display_name = ''
    form.role = 'member'
    form.temporary_password = ''
    notice.value = `用户 ${user.username} 已创建，首次登录必须修改密码。`
  } catch (exc: any) {
    error.value = exc?.message ?? '创建用户失败'
  } finally {
    loading.value = false
  }
}

function replace(updated: ManagedUser): void {
  users.value = users.value.map((user) => (user.id === updated.id ? updated : user))
}

async function patch(user: ManagedUser, payload: Parameters<typeof updateUser>[1]): Promise<void> {
  busyId.value = user.id
  error.value = ''
  notice.value = ''
  try {
    replace(await updateUser(user.id, payload))
  } catch (exc: any) {
    error.value = exc?.message ?? '更新用户失败'
    await load()
  } finally {
    busyId.value = null
  }
}

async function toggleActive(user: ManagedUser): Promise<void> {
  const next = !user.is_active
  if (!next && !window.confirm(`停用用户 ${user.username}？该用户现有登录会话会立即失效。`)) {
    return
  }
  await patch(user, { is_active: next })
  if (!error.value) notice.value = next ? '用户已启用。' : '用户已停用。'
}

async function changeRole(user: ManagedUser, event: Event): Promise<void> {
  const role = (event.target as HTMLSelectElement).value as 'admin' | 'member'
  await patch(user, { role })
  if (!error.value) notice.value = '用户角色已更新。'
}

function beginRename(user: ManagedUser): void {
  editingNameId.value = user.id
  editingName.value = user.display_name
}

async function saveName(user: ManagedUser): Promise<void> {
  await patch(user, { display_name: editingName.value })
  if (!error.value) {
    editingNameId.value = null
    notice.value = '显示名已更新。'
  }
}

async function revoke(user: ManagedUser): Promise<void> {
  if (!window.confirm(`撤销 ${user.username} 的全部登录会话？`)) return
  busyId.value = user.id
  error.value = ''
  try {
    replace(await revokeUserSessions(user.id))
    notice.value = '该用户的所有登录会话已撤销。'
  } catch (exc: any) {
    error.value = exc?.message ?? '撤销登录会话失败'
  } finally {
    busyId.value = null
  }
}

function openReset(user: ManagedUser): void {
  resetTarget.value = user
  resetPassword.value = ''
  error.value = ''
}

async function submitReset(): Promise<void> {
  const target = resetTarget.value
  if (!target || !resetPassword.value) return
  busyId.value = target.id
  error.value = ''
  try {
    replace(await resetUserPassword(target.id, resetPassword.value))
    resetTarget.value = null
    resetPassword.value = ''
    notice.value = '临时密码已设置，该用户现有登录会话已撤销，下次登录必须改密。'
  } catch (exc: any) {
    error.value = exc?.message ?? '重置密码失败'
  } finally {
    busyId.value = null
  }
}

function dateLabel(value: string | null): string {
  if (!value) return '从未登录'
  return new Date(value).toLocaleString()
}

onMounted(load)
</script>

<template>
  <section class="section">
    <div class="users-title-row">
      <div>
        <h3>用户管理</h3>
        <p class="field-note">管理员创建账号并分发临时密码；系统不开放公开注册。</p>
      </div>
      <button class="btn" type="button" :disabled="loading" @click="load">
        {{ loading ? '刷新中…' : '刷新' }}
      </button>
    </div>

    <p v-if="error" class="page-banner page-banner-error">{{ error }}</p>
    <p v-else-if="notice" class="page-banner">{{ notice }}</p>

    <form class="users-create" @submit.prevent="create">
      <div class="users-create-grid">
        <label>
          <span>用户名</span>
          <input v-model="form.username" class="input" autocomplete="off" placeholder="例如 zhangsan" />
        </label>
        <label>
          <span>显示名</span>
          <input v-model="form.display_name" class="input" autocomplete="off" placeholder="例如 张三" />
        </label>
        <label>
          <span>角色</span>
          <select v-model="form.role" class="input">
            <option value="member">成员</option>
            <option value="admin">管理员</option>
          </select>
        </label>
        <label>
          <span>初始密码</span>
          <input
            v-model="form.temporary_password"
            class="input"
            type="password"
            autocomplete="new-password"
            placeholder="至少 12 位"
          />
        </label>
      </div>
      <div class="users-create-actions">
        <span class="field-note">用户首次登录会被强制要求修改密码。</span>
        <button class="btn btn-primary" type="submit" :disabled="loading">创建用户</button>
      </div>
    </form>
  </section>

  <section class="section">
    <h3>账号列表 <span class="set-tag">{{ users.length }}</span></h3>
    <p v-if="!users.length && loading" class="field-note">正在加载用户…</p>
    <div v-else class="users-list">
      <article v-for="user in users" :key="user.id" class="user-card" :class="{ inactive: !user.is_active }">
        <div class="user-main">
          <div class="user-identity">
            <div class="user-name-line">
              <template v-if="editingNameId === user.id">
                <input v-model="editingName" class="input user-name-input" maxlength="128" />
                <button class="btn btn-small" type="button" @click="saveName(user)">保存</button>
                <button class="btn btn-small" type="button" @click="editingNameId = null">取消</button>
              </template>
              <template v-else>
                <strong>{{ user.display_name }}</strong>
                <button class="link-button" type="button" @click="beginRename(user)">改名</button>
              </template>
            </div>
            <span class="user-username">@{{ user.username }}</span>
          </div>
          <div class="user-badges">
            <span class="user-badge" :class="user.role">{{ user.role === 'admin' ? '管理员' : '成员' }}</span>
            <span class="user-badge" :class="user.is_active ? 'active' : 'inactive'">
              {{ user.is_active ? '启用' : '停用' }}
            </span>
            <span v-if="user.must_change_password" class="user-badge warning">待改密</span>
          </div>
        </div>

        <div class="user-meta">
          <span>{{ user.active_sessions }} 个登录会话</span>
          <span>{{ dateLabel(user.last_active_at) }}</span>
          <span>创建于 {{ dateLabel(user.created_at) }}</span>
        </div>

        <div class="user-actions">
          <label class="user-role">
            <span>角色</span>
            <select
              class="input"
              :value="user.role"
              :disabled="busyId === user.id"
              @change="changeRole(user, $event)"
            >
              <option value="member">成员</option>
              <option value="admin">管理员</option>
            </select>
          </label>
          <button class="btn btn-small" type="button" :disabled="busyId === user.id" @click="toggleActive(user)">
            {{ user.is_active ? '停用' : '启用' }}
          </button>
          <button
            class="btn btn-small"
            type="button"
            :disabled="busyId === user.id || user.id === props.currentUserId"
            :title="user.id === props.currentUserId ? '请使用“修改密码”修改自己的密码' : ''"
            @click="openReset(user)"
          >
            重置密码
          </button>
          <button class="btn btn-small" type="button" :disabled="busyId === user.id" @click="revoke(user)">
            撤销会话
          </button>
        </div>
      </article>
    </div>
  </section>

  <div v-if="resetTarget" class="modal-mask" @click.self="resetTarget = null">
    <section class="modal" role="dialog" aria-modal="true" aria-label="重置用户密码">
      <header class="modal-head">
        <h2>重置 {{ resetTarget.display_name }} 的密码</h2>
      </header>
      <div class="modal-body">
        <p class="field-note">
          设置临时密码后，该用户现有登录会全部失效，下次登录必须修改密码。
        </p>
        <label>
          <span>临时密码</span>
          <input
            v-model="resetPassword"
            class="input"
            type="password"
            autocomplete="new-password"
            placeholder="至少 12 位"
            autofocus
          />
        </label>
      </div>
      <footer class="modal-foot">
        <button class="btn" type="button" @click="resetTarget = null">取消</button>
        <button
          class="btn btn-primary"
          type="button"
          :disabled="busyId === resetTarget.id || !resetPassword"
          @click="submitReset"
        >
          {{ busyId === resetTarget.id ? '重置中…' : '确认重置' }}
        </button>
      </footer>
    </section>
  </div>
</template>
