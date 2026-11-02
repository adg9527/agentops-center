<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { api } from '../api'
import { auth } from '../auth'
import { fmtTime } from '../labels'

interface TaskRow {
  id: number
  title: string
  status: string
  created_at: string
  file_count: number
  version_count: number
}

interface FileRow {
  path: string
  size: number
  modified_at: number | null
  versions: number
  latest_version: number
  latest_at: string | null
  deleted?: boolean
}

interface VersionRow {
  id: number
  task_id: number
  path: string
  version: number
  sha256: string
  size: number
  encoding: string
  note: string
  created_at: string
  content?: string
}

interface EvalRunRow {
  id: number
  suite_id: number
  suite_name: string
  round: number
  status: string
  passed: boolean | null
  metrics: Record<string, any>
  task_ids: number[]
  started_at: string
  finished_at: string | null
}

const tab = ref<'reports' | 'artifacts'>('reports')

const runs = ref<EvalRunRow[]>([])
const runsLoading = ref(false)
const runDetail = ref<any | null>(null)
const runDetailOpen = ref(false)

const tasks = ref<TaskRow[]>([])
const files = ref<FileRow[]>([])
const versions = ref<VersionRow[]>([])
const selectedTask = ref<TaskRow | null>(null)
const selectedFile = ref<FileRow | null>(null)
const selectedVersion = ref<VersionRow | null>(null)
const compareId = ref<number | null>(null)
const loadingTasks = ref(false)
const loadingFiles = ref(false)
const loadingVersions = ref(false)
const diffOpen = ref(false)
const diffText = ref('')

const canWrite = computed(() => auth.can('operator'))

async function loadRuns() {
  runsLoading.value = true
  try {
    runs.value = await api<EvalRunRow[]>('/api/evals/recent-runs?limit=50')
  } catch (e: any) {
    message.error(e.message)
  } finally {
    runsLoading.value = false
  }
}

async function openRunDetail(run: EvalRunRow) {
  try {
    runDetail.value = await api(`/api/evals/runs/${run.id}`)
    runDetailOpen.value = true
  } catch (e: any) {
    message.error(e.message)
  }
}

async function loadTasks() {
  loadingTasks.value = true
  try {
    tasks.value = await api<TaskRow[]>('/api/artifacts/tasks')
    if (!selectedTask.value && tasks.value.length) await selectTask(tasks.value[0])
  } catch (e: any) {
    message.error(e.message)
  } finally {
    loadingTasks.value = false
  }
}

async function selectTask(t: TaskRow) {
  selectedTask.value = t
  selectedFile.value = null
  selectedVersion.value = null
  versions.value = []
  loadingFiles.value = true
  try {
    const r = await api<{ files: FileRow[] }>(`/api/artifacts/tasks/${t.id}/files`)
    files.value = r.files
    if (files.value.length) await selectFile(files.value[0])
  } catch (e: any) {
    message.error(e.message)
  } finally {
    loadingFiles.value = false
  }
}

async function selectFile(f: FileRow) {
  selectedFile.value = f
  selectedVersion.value = null
  loadingVersions.value = true
  try {
    versions.value = await api<VersionRow[]>(
      `/api/artifacts/tasks/${selectedTask.value?.id}/versions?path=${encodeURIComponent(f.path)}`,
    )
    if (versions.value.length) await selectVersion(versions.value[0])
  } catch (e: any) {
    message.error(e.message)
  } finally {
    loadingVersions.value = false
  }
}

async function selectVersion(v: VersionRow) {
  compareId.value = null
  try {
    selectedVersion.value = await api<VersionRow>(`/api/artifacts/versions/${v.id}`)
  } catch (e: any) {
    message.error(e.message)
  }
}

async function snapshot() {
  if (!selectedTask.value) return
  try {
    const r = await api<{ created: number }>(`/api/artifacts/tasks/${selectedTask.value.id}/snapshot`, { body: { note: 'manual' } })
    message.success(r.created ? `记录 ${r.created} 个新版本` : '没有变化，无需记录')
    await selectTask(selectedTask.value)
  } catch (e: any) {
    message.error(e.message)
  }
}

async function showDiff() {
  if (!compareId.value || !selectedVersion.value) {
    message.info('先在右侧版本列表里选一个版本作为对比基准')
    return
  }
  try {
    const r = await api<{ diff: string }>(`/api/artifacts/diff?a=${compareId.value}&b=${selectedVersion.value.id}`)
    diffText.value = r.diff || '（没有差异）'
    diffOpen.value = true
  } catch (e: any) {
    message.error(e.message)
  }
}

function restore(v: VersionRow) {
  Modal.confirm({
    title: `恢复到 v${v.version}？`,
    content: '会把该版本内容写回任务工作目录，并记为最新版本（历史不丢）。',
    okType: 'danger',
    onOk: async () => {
      try {
        await api(`/api/artifacts/versions/${v.id}/restore`, { method: 'POST', body: {} })
        message.success('已恢复')
        if (selectedTask.value) await selectTask(selectedTask.value)
      } catch (e: any) {
        message.error(e.message)
      }
    },
  })
}

function sizeText(n: number): string {
  if (!n) return '0 B'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

watch(tab, (value) => {
  if (value === 'reports' && !runs.value.length) loadRuns()
  if (value === 'artifacts' && !tasks.value.length) loadTasks()
})

onMounted(async () => {
  await loadRuns()
  await loadTasks()
})
</script>

<template>
  <a-card title="评测报告 / 版本管理" :bordered="false">
    <template #extra>
      <a-space>
        <a-button v-if="tab === 'reports'" size="small" :loading="runsLoading" @click="loadRuns">刷新</a-button>
        <a-button v-else size="small" :loading="loadingTasks" @click="loadTasks">刷新</a-button>
        <a-button v-if="tab === 'artifacts' && canWrite" type="primary" size="small" @click="snapshot">记录快照</a-button>
      </a-space>
    </template>

    <a-tabs v-model:active-key="tab">
      <a-tab-pane key="reports" tab="评测报告">
        <a-table
          size="middle"
          :data-source="runs"
          :loading="runsLoading"
          row-key="id"
          :pagination="{ pageSize: 15 }"
          :columns="[
            { title: '套件', dataIndex: 'suite_name', width: 180 },
            { title: '轮次', dataIndex: 'round', width: 70 },
            { title: '结果', key: 'passed', width: 100 },
            { title: '完成率', key: 'completion', width: 90 },
            { title: '工具率', key: 'tool', width: 90 },
            { title: '关键词', key: 'keyword', width: 90 },
            { title: 'token', key: 'tokens', width: 90 },
            { title: '耗时', key: 'ms', width: 100 },
            { title: '时间', key: 'time', width: 160 },
          ]"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'passed'">
              <a-tag :color="record.passed ? 'success' : record.status === 'running' ? 'processing' : 'error'">
                {{ record.status === 'running' ? '运行中' : record.passed ? '达标' : '未达标' }}
              </a-tag>
            </template>
            <template v-else-if="column.key === 'completion'">{{ (record.metrics?.completion ?? 0).toFixed(2) }}</template>
            <template v-else-if="column.key === 'tool'">{{ (record.metrics?.tool_use ?? 0).toFixed(2) }}</template>
            <template v-else-if="column.key === 'keyword'">{{ record.metrics?.keyword ?? '—' }}</template>
            <template v-else-if="column.key === 'tokens'">{{ record.metrics?.avg_tokens ?? 0 }}</template>
            <template v-else-if="column.key === 'ms'">{{ record.metrics?.avg_ms ?? 0 }}</template>
            <template v-else-if="column.key === 'time'">
              <a @click="openRunDetail(record)">{{ fmtTime(record.finished_at || record.started_at) }}</a>
            </template>
          </template>
        </a-table>
        <a-empty v-if="!runsLoading && !runs.length" description="还没有评测记录，去「自动化评测」跑一个" />
      </a-tab-pane>

      <a-tab-pane key="artifacts" tab="产物版本">
        <a-row :gutter="12">
          <a-col :xs="24" :md="6">
            <a-card size="small" title="任务" :body-style="{ padding: '8px' }">
              <div class="list">
                <div
                  v-for="t in tasks"
                  :key="t.id"
                  class="row"
                  :class="{ active: selectedTask?.id === t.id }"
                  @click="selectTask(t)"
                >
                  <div class="name">#{{ t.id }} {{ t.title }}</div>
                  <div class="meta">{{ t.file_count }} 文件 · {{ t.version_count }} 版本</div>
                </div>
              </div>
              <a-empty v-if="!loadingTasks && !tasks.length" description="还没有产物" :image-style="{ height: '30px' }" />
            </a-card>
          </a-col>

          <a-col :xs="24" :md="7">
            <a-card size="small" :title="selectedTask ? `文件（${files.length}）` : '文件'" :body-style="{ padding: '8px' }">
              <div class="list">
                <div
                  v-for="f in files"
                  :key="f.path"
                  class="row"
                  :class="{ active: selectedFile?.path === f.path }"
                  @click="selectFile(f)"
                >
                  <div class="name mono">{{ f.path }}</div>
                  <div class="meta">
                    {{ sizeText(f.size) }} · {{ f.versions }} 版本
                    <a-tag v-if="f.deleted" color="red" style="font-size: 10px">已删除</a-tag>
                  </div>
                </div>
              </div>
              <a-empty v-if="selectedTask && !loadingFiles && !files.length" description="该任务没有产物文件" :image-style="{ height: '30px' }" />
            </a-card>
          </a-col>

          <a-col :xs="24" :md="11">
            <a-card size="small" :title="selectedFile ? `${selectedFile.path} 的版本` : '版本'" :body-style="{ padding: '8px' }">
              <a-spin :spinning="loadingVersions">
                <a-radio-group
                  v-if="versions.length"
                  :value="selectedVersion?.id"
                  style="display: flex; flex-direction: column; gap: 6px"
                  @change="(e: any) => selectVersion(versions.find((v) => v.id === e.target.value)!)"
                >
                  <a-radio v-for="v in versions" :key="v.id" :value="v.id" style="margin: 0">
                    <a-space wrap>
                      <b>v{{ v.version }}</b>
                      <a-tag v-if="v.note" style="font-size: 10px">{{ v.note }}</a-tag>
                      <span class="mono" style="font-size: 11px">{{ v.sha256 }}</span>
                      <span style="font-size: 11px; color: #999">{{ fmtTime(v.created_at) }}</span>
                      <a @click.stop="compareId = v.id; selectedVersion && (selectedVersion = selectedVersion)">
                        设为对比基准
                      </a>
                    </a-space>
                  </a-radio>
                </a-radio-group>
                <a-empty v-else description="选择一个文件查看版本" :image-style="{ height: '30px' }" />

                <template v-if="selectedVersion">
                  <a-divider style="margin: 8px 0" />
                  <a-space style="margin-bottom: 6px">
                    <a-button size="small" @click="showDiff">与基准对比</a-button>
                    <a-button v-if="canWrite" size="small" danger @click="restore(selectedVersion)">恢复到此版本</a-button>
                    <a-typography-text type="secondary" style="font-size: 11px">
                      基准：{{ compareId ? versions.find((v) => v.id === compareId)?.version + ' 版' : '未选' }}
                    </a-typography-text>
                  </a-space>
                  <pre class="content">{{ selectedVersion.content }}</pre>
                </template>
              </a-spin>
            </a-card>
          </a-col>
        </a-row>
      </a-tab-pane>
    </a-tabs>

    <a-modal v-model:open="diffOpen" title="版本对比" :width="900" :footer="null">
      <pre class="content">{{ diffText }}</pre>
    </a-modal>

    <a-modal v-model:open="runDetailOpen" title="评测明细" :width="960" :footer="null">
      <template v-if="runDetail">
        <a-list :data-source="runDetail.detail || []" size="small">
          <template #renderItem="{ item }">
            <a-list-item>
              <a-space direction="vertical" style="width: 100%">
                <div><b>用例：</b>{{ item.prompt }}</div>
                <a-table
                  size="small"
                  :pagination="false"
                  :data-source="item.agents"
                  :columns="[
                    { title: 'Agent', dataIndex: 'agent', width: 140 },
                    { title: '状态', dataIndex: 'status', width: 100 },
                    { title: '工具', dataIndex: 'tool_calls', width: 70 },
                    { title: '关键词', dataIndex: 'keyword_hit', width: 90 },
                    { title: 'token', dataIndex: 'tokens', width: 90 },
                    { title: '输出', dataIndex: 'output', ellipsis: true },
                  ]"
                />
              </a-space>
            </a-list-item>
          </template>
        </a-list>
      </template>
    </a-modal>
  </a-card>
</template>

<style scoped>
.list {
  max-height: calc(100vh - 320px);
  overflow: auto;
}

.row {
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  border: 1px solid transparent;
}

.row:hover {
  background: #f5f7fa;
}

.row.active {
  background: #e6f0ff;
  border-color: #91caff;
}

.name {
  font-weight: 500;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta {
  font-size: 11px;
  color: #999;
}

.content {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  background: #fafafa;
  border-radius: 6px;
  padding: 8px 10px;
  margin: 0;
  max-height: 420px;
  overflow: auto;
}
</style>
