import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { auth } from '../auth'

const pages: RouteRecordRaw[] = [
  { path: '', name: 'dashboard', component: () => import('../pages/Dashboard.vue') },
  { path: 'agents', name: 'agents', component: () => import('../pages/Agents.vue') },
  { path: 'tasks', name: 'tasks', component: () => import('../pages/Tasks.vue') },
  { path: 'tasks/:id', name: 'task-detail', component: () => import('../pages/TaskDetail.vue'), props: true },
  { path: 'monitor', name: 'monitor', component: () => import('../pages/Monitor.vue') },
  { path: 'settings', name: 'settings', component: () => import('../pages/Settings.vue') },
  { path: 'chat', name: 'chat', component: () => import('../pages/Chat.vue') },
  { path: 'mcp', name: 'mcp', component: () => import('../pages/Mcp.vue') },
  { path: 'skills', name: 'skills', component: () => import('../pages/Skills.vue') },
  { path: 'prompts', name: 'prompts', component: () => import('../pages/Prompts.vue') },
  {
    path: 'evals',
    name: 'evals',
    component: () => import('../pages/Placeholder.vue'),
    props: {
      phase: 5,
      title: '自动化评测',
      items: ['数据集 (CSV/JSON 导入、版本)', '10 类评分器（确定性优先）', '多 Agent 横向 / 版本纵向对比', '评测可视化'],
    },
  },
  {
    path: 'reports',
    name: 'reports',
    component: () => import('../pages/Placeholder.vue'),
    props: {
      phase: 6,
      title: '评测报告',
      items: ['自动生成报告（历史快照）', 'PDF / Excel / JSON 导出', '报告版本、对比、检索与归档'],
    },
  },
  {
    path: 'versions',
    name: 'versions',
    component: () => import('../pages/Placeholder.vue'),
    props: {
      phase: 4,
      title: '版本管理',
      items: ['MCP / Skill / Agent / Prompt 统一版本体系', '版本快照、对比、发布、回滚、启停'],
    },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('../pages/Login.vue'), meta: { public: true } },
    {
      path: '/',
      component: () => import('../components/AppLayout.vue'),
      children: [...pages, { path: ':pathMatch(.*)*', redirect: '/' }],
    },
  ],
})

router.beforeEach(async (to) => {
  await auth.bootstrap()
  if (!to.meta.public && !auth.state.user) return { name: 'login', query: { redirect: to.fullPath } }
  if (to.name === 'login' && auth.state.user) return { name: 'dashboard' }
  return true
})

export default router
