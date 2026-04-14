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
    name: 'TaskQueue',
    component: () => import('../views/TaskQueue.vue')
  },
  {
    path: '/tasks',
    name: 'TaskList',
    component: () => import('../views/TaskList.vue')
  },
  {
    path: '/tasks/new',
    name: 'TaskNew',
    component: () => import('../views/TaskNew.vue')
  },
  {
    path: '/tasks/batch',
    name: 'TaskBatch',
    component: () => import('../views/TaskBatch.vue')
  },
  {
    path: '/tasks/:id',
    name: 'TaskDetail',
    component: () => import('../views/TaskDetail.vue'),
    props: true
  },
  {
    path: '/runners',
    name: 'RunnerList',
    component: () => import('../views/RunnerList.vue')
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
