import { createRouter, createWebHistory } from 'vue-router'
import LandingPage from '../views/LandingPage.vue'
import ModelCollaboration from '../views/ModelCollaboration.vue'

const routes = [
  {
    path: '/',
    name: 'Home',
    component: LandingPage
  },
  {
    path: '/models/:id',
    name: 'ModelCollaboration',
    component: ModelCollaboration
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
