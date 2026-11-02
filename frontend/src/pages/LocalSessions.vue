<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { api } from '../api'
import { fmtTime } from '../labels'

interface LocalSession {
  source: string
  id: string
  project: string
  project_path: string
  title: string
  summary: string
  started_at: string | null
  last_at: string
  size_kb: number
}

interface Entry {
  role: 'user' | 'assistant' | 'tool'
  kind: 'text' | 'tool_use' | 'tool_result'
  text: string
  name?: string
  at?: string
  model?: string
  error?: boolean
}

interface SessionDetail extends LocalSession {
  counts: Record<string, number>
  entries: Entry[]
}

const sessions = ref<LocalSession[]>([])
const loading = ref(false)
const query = ref('')
const selected = ref<LocalSession | null>(null)
const detail = ref<SessionDetail | null>(null)
const detailLoading = ref(false)

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return sessions.value
  return sessions.value.filter(
    (s) => s.title.toLowerCase().includes(q) || (s.project_path || '').toLowerCase().includes(q),
  )
})

function workspaceName(s: LocalSession): string {
  const path = s.project_path || ''
  const parts = path.split(/[\\/]/).filter(Boolean)
  return parts.slice(-2).join('/') || s.project
}

async function load() {
  loading.value = true
  try {
    const r = await api<{ items: LocalSession[] }>('/api/local-sessions?limit=80')
    sessions.value = r.items ?? []
    if (!selected.value && sessions.value.length) await open(sessions.value[0])
  } catch (e: any) {
    message.error(e.message)
  } finally {
    loading.value = false
  }
}

async function open(s: LocalSession) {
  selected.value = s
  detailLoading.value = true
  try {
    detail.value = await api<SessionDetail>(`/api/local-sessions/${s.source}/${s.project}/${s.id}`)
  } catch (e: any) {
    message.error(e.message)
    detail.value = null
  } finally {
    detailLoading.value = false
  }
}

const toolCount = computed(() => detail.value?.entries.filter((e) => e.kind === 'tool_use').length ?? 0)

function short(text: string, n = 400): string {
  return text.length > n ? text.slice(0, n) + ' …' : text
}

onMounted(load)
</script>

<template>
  <a-row :gutter="16">
    <a-col :xs="24" :md="8" :xl="7">
      <a-card size="small" title="本地 Agent 会话" :bordered="false" :body-style="{ padding: '8px' }">
        <template #extra>
          <a-button size="small" :loading="loading" @click="load">刷新</a-button>
        </template>
        <a-input v-model:value="query" placeholder="搜索标题或工作目录" allow-clear size="small" style="margin-bottom: 8px" />
        <a-spin :spinning="loading">
          <div class="session-list">
            <div
              v-for="s in filtered"
              :key="s.project + s.id"
              class="session-item"
              :class="{ active: selected?.id === s.id && selected?.project === s.project }"
              @click="open(s)"
            >
              <div class="row1">
                <span class="title">{{ s.title }}</span>
                <a-tag v-if="s.summary" color="blue" style="font-size: 10px">摘要</a-tag>
              </div>
              <div class="row2">
                <span class="ws">{{ workspaceName(s) }}</span>
                <span class="time">{{ fmtTime(s.last_at) }}</span>
                <span class="size">{{ s.size_kb }} KB</span>
              </div>
            </div>
          </div>
          <a-empty v-if="!loading && !filtered.length" description="没有找到本地会话" :image-style="{ height: '40px' }" />
        </a-spin>
      </a-card>
    </a-col>

    <a-col :xs="24" :md="16" :xl="17">
      <a-card size="small" :bordered="false">
        <template #title>
          <a-space>
            <span>{{ detail?.title || '选择一个会话' }}</span>
            <a-tag v-if="detail">Claude Code</a-tag>
          </a-space>
        </template>
        <template #extra>
          <a-space v-if="detail" wrap>
            <a-tag>{{ detail.counts?.user ?? 0 }} 条提问</a-tag>
            <a-tag>{{ detail.counts?.assistant ?? 0 }} 条回复</a-tag>
            <a-tag>{{ toolCount }} 次工具调用</a-tag>
            <a-typography-text type="secondary" style="font-size: 12px">{{ detail.project_path }}</a-typography-text>
          </a-space>
        </template>

        <a-spin :spinning="detailLoading">
          <a-empty v-if="!detail" description="左侧选择一个会话查看执行过程" />
          <div v-else class="transcript">
            <div v-for="(e, i) in detail.entries" :key="i" class="entry" :class="e.role">
              <div class="entry-head">
                <a-tag :color="e.role === 'user' ? 'green' : e.role === 'assistant' ? 'blue' : 'default'" style="font-size: 11px">
                  {{ e.role === 'user' ? '用户' : e.role === 'assistant' ? 'Claude' : '工具' }}
                </a-tag>
                <span v-if="e.kind === 'tool_use'" class="tool-name">{{ e.name }}</span>
                <a-typography-text type="secondary" style="font-size: 11px">{{ fmtTime(e.at) }}</a-typography-text>
                <a-tag v-if="e.error" color="error" style="font-size: 10px">失败</a-tag>
              </div>
              <pre v-if="e.kind !== 'text'" class="tool-body">{{ short(e.text, 1200) }}</pre>
              <div v-else class="body">{{ short(e.text, 4000) }}</div>
            </div>
          </div>
        </a-spin>
      </a-card>
    </a-col>
  </a-row>
</template>

<style scoped>
.session-list {
  max-height: calc(100vh - 220px);
  overflow: auto;
}

.session-item {
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  border: 1px solid transparent;
}

.session-item:hover {
  background: #f5f7fa;
}

.session-item.active {
  background: #e6f0ff;
  border-color: #91caff;
}

.row1 {
  display: flex;
  align-items: center;
  gap: 6px;
}

.title {
  font-weight: 500;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row2 {
  display: flex;
  gap: 8px;
  font-size: 11px;
  color: #999;
  margin-top: 2px;
}

.ws {
  max-width: 60%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.transcript {
  max-height: calc(100vh - 240px);
  overflow: auto;
}

.entry {
  padding: 8px 10px;
  margin-bottom: 8px;
  border-radius: 8px;
  background: #fafafa;
}

.entry.assistant {
  background: #f0f5ff;
}

.entry.user {
  background: #f6ffed;
}

.entry.tool {
  background: #f5f5f5;
}

.entry-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.tool-name {
  font-family: Consolas, monospace;
  font-size: 12px;
  color: #722ed1;
}

.body {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
  color: #222;
}

.tool-body {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 11px;
  color: #555;
  background: #fff;
  border-radius: 6px;
  padding: 6px 8px;
  margin: 0;
  max-height: 220px;
  overflow: auto;
}
</style>
