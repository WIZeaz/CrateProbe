import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    redirect: '/dashboard'
  },
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('../views/Dashboard.vue')
  },
  {
    path: '/queue',
    name: 'JobQueue',
    component: () => import('../views/JobQueue.vue')
  },
  {
    path: '/jobs',
    name: 'JobList',
    component: () => import('../views/JobList.vue')
  },
  {
    path: '/jobs/new',
    name: 'JobNew',
    component: () => import('../views/JobNew.vue')
  },
  {
    path: '/jobs/batch',
    name: 'JobBatch',
    component: () => import('../views/JobBatch.vue')
  },
  {
    path: '/jobs/:id',
    name: 'JobDetail',
    component: () => import('../views/JobDetail.vue'),
    props: true
  },
  {
    path: '/tasks',
    name: 'TaskManage',
    component: () => import('../views/TaskManage.vue')
  },
  {
    path: '/runners',
    name: 'RunnerList',
    component: () => import('../views/RunnerList.vue')
  },
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('../views/Settings.vue')
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
