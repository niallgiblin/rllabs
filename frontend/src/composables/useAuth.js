import { ref, computed } from 'vue'
import { auth } from '../services/api'

// Global auth state
const isAuthenticated = ref(auth.isAuthenticated())
const currentUser = ref(null)

export function useAuth() {
  const login = (token) => {
    auth.setToken(token)
    isAuthenticated.value = true
    // In a real app, you might decode the JWT to get user info
    // For now, we'll just track that they're logged in
  }
  
  const logout = () => {
    auth.logout()
    isAuthenticated.value = false
    currentUser.value = null
  }
  
  return {
    isAuthenticated: computed(() => isAuthenticated.value),
    currentUser: computed(() => currentUser.value),
    login,
    logout
  }
}

