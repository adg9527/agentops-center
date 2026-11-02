import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { auth } from '../auth'

const pages: RouteRecordRaw[] = [
  { path: '', name: 'dashboard', component: () => import('../pages/Dashboard.vue') },
  { path: 'agents', name: 'agents', component: () => import('../pages/Agents.vue') },
  { path: 'tasks', name: 'tasks', component: () => import('../pages/Tasks.vue') },
  { path: 'tasks/:id', name: 'task-detail', component: () => import('../pages/TaskDetail.vue'), props: true },
  { path: 'sessions', name: 'sessions', component: () => import('../pages/LocalSessions.vue') },
  { path: 'monitor', name: 'monitor', component: () => import('../pages/Monitor.vue') },
  { path: 'settings', name: 'settings', component: () => import('../pages/Settings.vue') },
  { path: 'chat', name: 'chat', component: () => import('../pages/Chat.vue') },
  { path: 'mcp', name: 'mcp', component: () => import('../pages/Mcp.vue') },
  { path: 'skills', name: 'skills', component: () => import('../pages/Skills.vue') },
  { path: 'prompts', name: 'prompts', component: () => import('../pages/Prompts.vue') },
  { path: 'evals', name: 'evals', component: () => import('../pages/Evals.vue') },
  { path: 'artifacts', name: 'artifacts', component: () => import('../pages/Artifacts.vue') },
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
