<script setup lang="ts">
/**
 * 设置页第 4 栏「提供商」：**在这里改目录**（2026-10-15 用户口径 b）。

 * 为什么要有这一栏：目录（表 `model_providers`）以前只能改库或改种子文件 + `reload`，
 * 而「清单路径」「鉴权头」这些格子只有真机试过才知道该怎么填（GLM 要多一层 `/v1`、
 * qwen 在另一个根下面）—— 让用户去手改库不对。目录本来就是**每条请求现读库**，
 * 所以这里改完刷新即生效，连重启都不用。

 * 列表**含停用**的家（停用只是不进下拉，改回来要能看见它）。快照里那份 `providers`
 * 仍然只含启用的，给模型弹窗的下拉用 —— 两份口径不要混。

 * 不做删除：`provider` 是模型行与 agent 行的外键目标，删一行会牵出「那些行怎么办」。
 * 要下架就停用。
 */
import { onMounted, onUnmounted, ref } from 'vue'
import { getProviders, putProvider, type ProviderOption, type ProviderUpsert } from '../../lib/api'

const providers = ref<ProviderOption[]>([])
const busy = ref(false)
const error = ref('')
const notice = ref('')
/** 正在编辑的那一行；`isNew` 时键可以填 */
const editing = ref<ProviderUpsert | null>(null)
const editingKey = ref('')
const isNew = ref(false)

/** 一句话把这一行的关键信息说清：地址 + 清单路径（两样都可能被改，界面上要看得见）。 */
function line(meta: ProviderOption): string {
  return [meta.base_url || '（没配地址）', meta.models_path || '/models'].join(' · ')
}

async function load() {
  error.value = ''
  try {
    providers.value = await getProviders()
  } catch (e: any) {
    error.value = e?.message ?? '读取失败'
  }
}

void load()

function blank(): ProviderUpsert {
  return {
    label: '',
    base_url: '',
    ui_group: '',
    sort_order: 0,
    models_path: '/models',
    auth_header: 'Authorization',
    auth_prefix: 'Bearer ',
    status: 1,
    is_dynamic_model_list: true,
  }
}

function openNew() {
  error.value = ''
  editing.value = blank()
  editingKey.value = ''
  isNew.value = true
}

function openEdit(meta: ProviderOption) {
  error.value = ''
  editing.value = {
    label: meta.label,
    base_url: meta.base_url,
    ui_group: meta.ui_group,
    sort_order: meta.sort_order,
    models_path: meta.models_path,
    auth_header: meta.auth_header,
    auth_prefix: meta.auth_prefix,
    status: meta.status,
    is_dynamic_model_list: meta.is_dynamic_model_list,
  }
  editingKey.value = meta.key
  isNew.value = false
}

function closeDialog() {
  editing.value = null
}

/**
 * 弹窗开着时按 `Esc` 只关弹窗。

 * 用**捕获阶段** + `stopImmediatePropagation`：设置页自己也监听 `Esc`（没有弹窗时它才是
 * 「返回聊天」），两处都挂在 window 上，谁先注册谁先跑 —— 捕获阶段一定早于冒泡阶段，
 * 所以这里能稳定地把它拦下来（与模型弹窗那次的坑同一个：别把整页连草稿一起关掉）。
 */
function onKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape' || !editing.value) return
  event.stopImmediatePropagation()
  closeDialog()
}

onMounted(() => window.addEventListener('keydown', onKeydown, true))
onUnmounted(() => window.removeEventListener('keydown', onKeydown, true))

async function save() {
  const draft = editing.value
  if (!draft) return
  const key = (isNew.value ? editingKey.value : editingKey.value).trim().toLowerCase()
  if (!key) {
    error.value = '提供商键不能为空（新增时由你填，只能用 a-z / 0-9 / _ / -）'
    return
  }
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    providers.value = await putProvider(key, draft)
    notice.value = isNew.value ? `已新增「${draft.label}」。` : `「${draft.label}」已保存，刷新即生效。`
    closeDialog()
  } catch (e: any) {
    error.value = e?.message ?? '保存失败'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="section">
    <h3>提供商目录</h3>
    <p class="field-note">
      这里改完**刷新界面即生效**（目录是每条请求现读库的）：默认地址、清单路径、鉴权头都由这一栏说了算。
      停用只是不进下拉，不删任何东西。
    </p>

    <p v-if="error" class="page-banner page-banner-error">{{ error }}</p>
    <p v-else-if="notice" class="page-banner">{{ notice }}</p>

    <div class="entry" v-for="meta in providers" :key="meta.key">
      <div class="entry-main">
        <b class="entry-name">{{ meta.label }}</b>
        <span class="entry-hint">{{ meta.key }}</span>
        <span v-if="meta.status !== 1" class="chip">已停用</span>
        <button class="btn entry-btn" type="button" @click="openEdit(meta)">编辑</button>
      </div>
      <p class="entry-detail">{{ line(meta) }}</p>
    </div>

    <div class="set-row">
      <span class="set-label">新增一家</span>
      <div class="set-control">
        <button class="btn" type="button" @click="openNew">新增提供商</button>
      </div>
    </div>
  </section>

  <!-- 点遮罩不关（与模型弹窗同一口径）：要关按 X 或 Esc -->
  <div v-if="editing" class="modal-mask">
    <section class="modal" role="dialog" aria-modal="true" :aria-label="isNew ? '新增提供商' : `编辑 ${editing.label}`">
      <header class="modal-head">
        <h2>{{ isNew ? '新增提供商' : editing.label }}</h2>
        <button class="icon-btn" type="button" aria-label="关闭" @click="closeDialog">
          <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
            <path
              d="M3.5 3.5l9 9M12.5 3.5l-9 9"
              fill="none"
              stroke="currentColor"
              stroke-width="1.4"
              stroke-linecap="round"
            />
          </svg>
        </button>
      </header>

      <div class="modal-body">
        <p v-if="error" class="modal-error">{{ error }}</p>

        <div class="group">
          <h3>这一家是谁</h3>
          <label class="field">
            <span class="field-label">键（不可改）</span>
            <input
              v-model="editingKey"
              class="input"
              :disabled="!isNew"
              placeholder="如 typesafe（小写字母 / 数字 / _ / -）"
            />
          </label>
          <label class="field">
            <span class="field-label">显示名</span>
            <input v-model="editing.label" class="input" placeholder="如 JEV / GLM（智谱）" />
          </label>
          <div class="field-inline">
            <label class="field">
              <span class="field-label">分组</span>
              <input v-model="editing.ui_group" class="input" placeholder="如 国内厂商（留空＝不分组）" />
            </label>
            <label class="field field-num">
              <span class="field-label">排序</span>
              <input v-model.number="editing.sort_order" class="input input-num" inputmode="numeric" />
            </label>
          </div>
        </div>

        <div class="group">
          <h3>去哪拉、怎么鉴权</h3>
          <label class="field">
            <span class="field-label">默认地址</span>
            <input v-model="editing.base_url" class="input" placeholder="https://…（选中这家时带出来）" />
          </label>
          <label class="field">
            <span class="field-label">清单路径</span>
            <input
              v-model="editing.models_path"
              class="input"
              placeholder="/models（也可以整段绝对地址）"
            />
          </label>
          <div class="field-inline">
            <label class="field">
              <span class="field-label">鉴权头</span>
              <input v-model="editing.auth_header" class="input" placeholder="Authorization" />
            </label>
            <label class="field">
              <span class="field-label">前缀</span>
              <input v-model="editing.auth_prefix" class="input" placeholder="Bearer （有的网关留空）" />
            </label>
          </div>
          <p class="field-note">
            清单地址 = 默认地址 + 清单路径（路径写成 http 开头就整段用它）。实测：GLM 要 /v1/models、
            qwen 要 https://dashscope.aliyuncs.com/api/v1/models。
          </p>
        </div>

        <div class="group">
          <h3>状态</h3>
          <div class="field-inline">
            <label class="field">
              <span class="field-label">状态</span>
              <select v-model.number="editing.status" class="input">
                <option :value="1">启用</option>
                <option :value="0">停用</option>
              </select>
            </label>
            <label class="field">
              <span class="field-label">官方有 /models？</span>
              <select v-model="editing.is_dynamic_model_list" class="input">
                <option :value="true">有</option>
                <option :value="false">没有（只是标注）</option>
              </select>
            </label>
          </div>
        </div>
      </div>

      <footer class="modal-foot">
        <button class="btn" type="button" @click="closeDialog">取消</button>
        <button class="btn btn-primary" type="button" :disabled="busy" @click="save">
          {{ busy ? '保存中…' : '保存' }}
        </button>
      </footer>
    </section>
  </div>
</template>
