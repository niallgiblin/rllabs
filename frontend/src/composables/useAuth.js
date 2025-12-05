import { ref, computed } from 'vue'
import { auth, authApi } from '../services/api'

// Global auth state
const isAuthenticated = ref(auth.isAuthenticated())
const currentUser = ref(null)

export function useAuth() {
  const login = async (token) => {
    auth.setToken(token)
    isAuthenticated.value = true
    await fetchCurrentUser()
  }
  
  const logout = () => {
    auth.logout()
    isAuthenticated.value = false
    currentUser.value = null
  }

  const fetchCurrentUser = async () => {
    if (auth.isAuthenticated()) {
      try {
        const user = await authApi.getCurrentUser()
        currentUser.value = user
      } catch (error) {
        console.error("Failed to fetch current user:", error)
        logout() // Log out if token is invalid
      }
    }
  }
  
  return {
    isAuthenticated: computed(() => isAuthenticated.value),
    currentUser: computed(() => currentUser.value),
    login,
    logout,
    fetchCurrentUser
  }
}

// Initial fetch of current user if a token exists
const { fetchCurrentUser } = useAuth()
fetchCurrentUser()

