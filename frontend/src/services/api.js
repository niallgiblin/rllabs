// API service for backend communication
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8080'

const DEV_MODE_GRACEFUL_FAIL = import.meta.env.VITE_DEV_MODE_GRACEFUL_FAIL !== 'false' 

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

export async function apiRequest(endpoint, options = {}) {
  const url = `${API_BASE_URL}${endpoint}`
  const token = auth.getToken()
  
  const headers = {
    'Content-Type': 'application/json',
    ...options.headers
  }
  
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  
  const maxRetries = 3
  const retryDelay = 1000 
  
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
        
        if (response.status >= 400 && response.status < 500 && response.status !== 429) {
          if (response.status === 401) {
            auth.logout()
            throw new Error('Authentication required. Please sign in again.')
          }
          const errorMessage = errorData.detail || errorData.error_description || errorData.error || `Request failed (${response.status})`
          throw new Error(errorMessage)
        }
        
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
      
      if (response.status === 204) {
        return null
      }
      
      return await response.json()
    } catch (error) {
      if (error instanceof TypeError && error.message.includes('fetch')) {
        if (attempt < maxRetries - 1) {
          const delay = retryDelay * Math.pow(2, attempt)
          await new Promise(resolve => setTimeout(resolve, delay))
          continue
        }
        if (DEV_MODE_GRACEFUL_FAIL) {
          console.warn(`[DEV MODE] API unavailable: ${endpoint} - returning null`)
          return null
        }
        throw new Error('Network error: Unable to connect to the server. Please check your connection.')
      }
      
      throw error
    }
  }
  
  if (DEV_MODE_GRACEFUL_FAIL) {
    console.warn(`[DEV MODE] API request failed after retries: ${endpoint} - returning null`)
    return null
  }
  throw new Error('Request failed after retries')
}

// Models API
export const models = {
  async list(page = 1, pageSize = 50) {
    const response = await apiRequest(`/api/models?page=${page}&page_size=${pageSize}`)
    if (Array.isArray(response)) {
      return {
        items: response,
        total: response.length,
        page: 1,
        page_size: response.length,
        total_pages: 1
      }
    }
    return response
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
    const result = await apiRequest(`/api/models/${modelId}/versions`)
    return result || [] // Return empty array if API unavailable
  },
  
  async delete(id) {
    return apiRequest(`/api/models/${id}`, {
      method: 'DELETE'
    })
  },
  
  async checkOwnership(modelId) {
    return apiRequest(`/api/models/${modelId}/ownership`)
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
  },
  
  async getUploadStatus(uploadId) {
    return apiRequest(`/api/uploads/${uploadId}`)
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

export default {
  auth,
  authApi,
  models,
  uploads,
  downloads,
  health,
  apiRequest 
}
