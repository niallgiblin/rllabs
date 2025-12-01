// API service for backend communication
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8080'

// Token management
const TOKEN_KEY = 'rllabs_auth_token'

export const auth = {
  getToken() {
    return localStorage.getItem(TOKEN_KEY)
  },
  
  setToken(token) {
    if (token) {
      localStorage.setItem(TOKEN_KEY, token)
    } else {
      localStorage.removeItem(TOKEN_KEY)
    }
  },
  
  isAuthenticated() {
    return !!this.getToken()
  },
  
  logout() {
    this.setToken(null)
  }
}

// API client with error handling and fault tolerance
export async function apiRequest(endpoint, options = {}) {
  const url = `${API_BASE_URL}${endpoint}`
  const token = auth.getToken()
  
  const headers = {
    'Content-Type': 'application/json',
    ...options.headers
  }
  
  // Only add Authorization header if token exists
  // Gateway handles public endpoints correctly even with token present
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  
  // Retry configuration for transient failures
  const maxRetries = 3
  const retryDelay = 1000 // Start with 1 second
  
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      const response = await fetch(url, {
        ...options,
        headers
      })
      
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ 
          error: 'Unknown error',
          error_description: `HTTP ${response.status}: ${response.statusText}`,
          detail: `HTTP ${response.status}: ${response.statusText}`
        }))
        
        // Don't retry on client errors (4xx) except 429 (rate limit)
        if (response.status >= 400 && response.status < 500 && response.status !== 429) {
          if (response.status === 401) {
            // Unauthorized - clear token and redirect to login
            auth.logout()
            throw new Error('Authentication required. Please sign in again.')
          }
          // Extract error message - try detail first (FastAPI format), then error_description, then error
          const errorMessage = errorData.detail || errorData.error_description || errorData.error || `Request failed (${response.status})`
          throw new Error(errorMessage)
        }
        
        // Retry on rate limit (429) or server errors (5xx)
        if (response.status === 429 || response.status >= 500) {
          if (attempt < maxRetries - 1) {
            const retryAfter = response.headers.get('Retry-After')
            const delay = retryAfter ? parseInt(retryAfter) * 1000 : retryDelay * Math.pow(2, attempt)
            await new Promise(resolve => setTimeout(resolve, delay))
            continue
          }
        }
        
        throw new Error(errorData.error_description || errorData.error || 'Request failed')
      }
      
      // Handle 204 No Content
      if (response.status === 204) {
        return null
      }
      
      return await response.json()
    } catch (error) {
      // Network errors - retry with exponential backoff
      if (error instanceof TypeError && error.message.includes('fetch')) {
        if (attempt < maxRetries - 1) {
          const delay = retryDelay * Math.pow(2, attempt)
          await new Promise(resolve => setTimeout(resolve, delay))
          continue
        }
        throw new Error('Network error: Unable to connect to the server. Please check your connection.')
      }
      
      // Re-throw non-network errors (don't retry)
      throw error
    }
  }
  
  // Should never reach here, but TypeScript/ESLint might complain
  throw new Error('Request failed after retries')
}

// Models API
export const models = {
  async list() {
    return apiRequest('/api/models')
  },
  
  async get(id) {
    return apiRequest(`/api/models/${id}`)
  },
  
  async create(modelData) {
    return apiRequest('/api/models', {
      method: 'POST',
      body: JSON.stringify(modelData)
    })
  },
  
  async getLatestVersion(modelId) {
    return apiRequest(`/api/models/${modelId}/latest`)
  },
  
  async getVersions(modelId) {
    return apiRequest(`/api/models/${modelId}/versions`)
  },
  
  async delete(id) {
    return apiRequest(`/api/models/${id}`, {
      method: 'DELETE'
    })
  }
}

// Upload API
export const uploads = {
  async initiateUpload(uploadData) {
    return apiRequest('/api/uploads', {
      method: 'POST',
      body: JSON.stringify(uploadData)
    })
  },
  
  async completeUpload(uploadId, parts) {
    return apiRequest(`/api/uploads/${uploadId}/complete`, {
      method: 'POST',
      body: JSON.stringify({ parts })
    })
  },
  
  async abortUpload(uploadId) {
    return apiRequest(`/api/uploads/${uploadId}/abort`, {
      method: 'POST'
    })
  }
}

// Download API
export const downloads = {
  async getDownloadUrl(artifactId, expiresIn = 3600) {
    return apiRequest(`/api/downloads/${artifactId}?expires_in=${expiresIn}`)
  }
}

// Health check
export const health = {
  async check() {
    return apiRequest('/health')
  }
}

// Auth API
export const authApi = {
  async register(username, email, password, isAdmin = false) {
    return apiRequest('/api/auth/register', {
      method: 'POST',
      body: JSON.stringify({
        username,
        email,
        password,
        is_admin: isAdmin
      })
    })
  },
  
  async login(username, password) {
    return apiRequest('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({
        username,
        password
      })
    })
  },
  
  async getCurrentUser() {
    return apiRequest('/api/auth/me')
  }
}

// Export individual services for modular imports
export default {
  auth,
  authApi,
  models,
  uploads,
  downloads,
  health,
  apiRequest // Export for use in other service modules
}

