<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ApiOutlined,
  AppstoreOutlined,
  BarChartOutlined,
  BranchesOutlined,
  DashboardOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  LogoutOutlined,
  MessageOutlined,
  MonitorOutlined,
  ProfileOutlined,
  RobotOutlined,
  SettingOutlined,
  ThunderboltOutlined,
  UserOutlined,
} from '@ant-design/icons-vue'
import { auth } from '../auth'
import { useLiveStatus } from '../live'

const router = useRouter()
const route = useRoute()
const live = useLiveStatus()
const collapsed = ref(false)

const ITEMS = [
  { key: '/', icon: DashboardOutlined, label: '工作台' },
  { key: '/chat', icon: MessageOutlined, label: 'Agent 群聊' },
  { key: '/agents', icon: RobotOutlined, label: 'Agent 管理' },
  { key: '/mcp', icon: ApiOutlined, label: 'MCP 管理', phase: 4 },
  { key: '/skills', icon: AppstoreOutlined, label: 'Skill 管理', phase: 4 },
  { key: '/prompts', icon: ProfileOutlined, label: 'Prompt 管理', phase: 4 },
  { key: '/tasks', icon: ThunderboltOutlined, label: '任务中心' },
  { key: '/monitor', icon: MonitorOutlined, label: '执行监控' },
  { key: '/evals', icon: ExperimentOutlined, label: '自动化评测', phase: 5 },
  { key: '/reports', icon: FileTextOutlined, label: '评测报告', phase: 6 },
  { key: '/versions', icon: BranchesOutlined, label: '版本管理', phase: 4 },
  { key: '/settings', icon: SettingOutlined, label: '系统设置' },
]

const selected = computed(() => {
  const path = route.path
  const hit = ITEMS.map((i) => i.key)
    .filter((k) => (k === '/' ? path === '/' : path.startsWith(k)))
    .sort((a, b) => b.length - a.length)
  return hit.length ? [hit[0]] : ['/']
})

function onMenu({ key }: { key: string | number }) {
  router.push(String(key))
}

function logout() {
  auth.logout()
  router.push('/login')
}
</script>

<template>
  <a-layout style="min-height: 100vh">
    <a-layout-sider
      v-model:collapsed="collapsed"
      collapsible
      :width="208"
      breakpoint="lg"
      style="border-right: 1px solid #1c2433"
    >
      <div class="logo">
        <BarChartOutlined style="font-size: 20px; color: #4f7cff" />
        <b v-if="!collapsed">AgentOps Center</b>
      </div>
      <a-menu theme="dark" mode="inline" :selected-keys="selected" @click="onMenu">
        <a-menu-item v-for="item in ITEMS" :key="item.key">
          <template #icon><component :is="item.icon" /></template>
          <span>{{ item.label }}</span>
          <a-tag v-if="item.phase" style="margin-inline-start: 6px; font-size: 10px; line-height: 16px; padding: 0 4px">
            P{{ item.phase }}
          </a-tag>
        </a-menu-item>
      </a-menu>
    </a-layout-sider>

    <a-layout>
      <a-layout-header class="header">
        <a-space size="large">
          <a-badge :status="live ? 'success' : 'error'">
            <a-typography-text type="secondary">{{ live ? '实时连接' : '实时连接断开' }}</a-typography-text>
          </a-badge>
          <a-dropdown>
            <a-button type="text">
              <UserOutlined />
              {{ auth.state.user?.username }}
              <a-tag style="margin-left: 6px">{{ auth.state.user?.role }}</a-tag>
            </a-button>
            <template #overlay>
              <a-menu>
                <a-menu-item key="logout" @click="logout">
                  <LogoutOutlined />
                  退出登录
                </a-menu-item>
              </a-menu>
            </template>
          </a-dropdown>
        </a-space>
      </a-layout-header>

      <a-layout-content style="padding: 20px; min-width: 0">
        <router-view />
      </a-layout-content>
    </a-layout>
  </a-layout>
</template>

<style scoped>
.logo {
  height: 56px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 20px;
  color: #e6ebf5;
  letter-spacing: 0.5px;
  overflow: hidden;
  white-space: nowrap;
}

.header {
  height: 56px;
  line-height: 56px;
  padding: 0 20px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  border-bottom: 1px solid #1c2433;
  background: #fff;
}
</style>
