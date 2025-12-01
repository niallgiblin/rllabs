<template>
  <div class="min-h-screen text-foreground relative overflow-hidden selection:bg-primary selection:text-primary-foreground">
    <!-- Background Gradients -->
    <div class="fixed inset-0 -z-10 overflow-hidden pointer-events-none">
      <div class="absolute -top-[20%] -left-[10%] w-[70vw] h-[70vw] rounded-full bg-[#9333ea]/30 blur-[120px] animate-float"></div>
      <div class="absolute top-[20%] -right-[10%] w-[60vw] h-[60vw] rounded-full bg-[#4f46e5]/30 blur-[120px] animate-float-delayed"></div>
      <div class="absolute -bottom-[20%] left-[20%] w-[50vw] h-[50vw] rounded-full bg-[#2563eb]/30 blur-[120px] animate-float-slow"></div>
    </div>

    <!-- Navigation -->
    <nav class="sticky top-0 z-50 border-b border-white/10 bg-background/60 backdrop-blur-xl">
      <div class="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
        <div class="flex items-center gap-2">
          <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-pink-500 to-purple-600 flex items-center justify-center text-white font-bold">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-5 h-5"><polygon points="12 2 2 7 12 12 22 7 12 2"></polygon><polyline points="2 17 12 22 22 17"></polyline><polyline points="2 12 12 17 22 12"></polyline></svg>
          </div>
          <span class="text-xl font-bold tracking-tight">RLLabs</span>
        </div>

        <div class="flex items-center gap-4">
          <button v-if="isAuthenticated" @click="showUpload = true" class="bg-gradient-to-r from-orange-500 to-pink-600 hover:from-orange-400 hover:to-pink-500 text-white px-4 py-2 rounded-lg font-medium transition-all shadow-lg shadow-orange-500/20 flex items-center gap-2 text-sm">
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" x2="12" y1="3" y2="15"/></svg>
            Upload Model
          </button>
          <div v-if="isAuthenticated" class="h-6 w-px bg-white/10"></div>
          <button v-if="isAuthenticated" @click="logout" class="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors">Sign Out</button>
          <button v-if="!isAuthenticated" @click="showAuth = true" class="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors">Sign In</button>
          <button v-if="!isAuthenticated" @click="showAuth = true" class="text-sm font-medium border border-white/10 hover:bg-white/5 px-4 py-2 rounded-lg transition-colors">Sign Up</button>
        </div>
      </div>
    </nav>

    <!-- Hero Section -->
    <main class="max-w-7xl mx-auto px-6 pt-20 pb-32">
      <div class="text-center max-w-3xl mx-auto mb-20">
        <h1 class="text-5xl md:text-7xl font-bold tracking-tight mb-6 bg-clip-text text-transparent bg-gradient-to-r from-white via-white to-white/70">
          Train. Share. Accelerate.
        </h1>
        <p class="text-xl text-muted-foreground mb-10 leading-relaxed">
          The premier platform for reinforcement learning model development. 
          Collaborate, version, and deploy your agents with ease.
        </p>

        <!-- Search Bar -->
        <div class="relative max-w-2xl mx-auto group">
          <div class="absolute -inset-0.5 bg-gradient-to-r from-pink-500/20 to-blue-500/20 rounded-xl blur opacity-75 group-hover:opacity-100 transition duration-1000 group-hover:duration-200"></div>
          <div class="relative flex items-center bg-white/5 backdrop-blur-xl border border-white/10 rounded-xl overflow-hidden transition-colors focus-within:bg-white/10 focus-within:border-white/20">
            <div class="pl-4 text-muted-foreground">
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
            </div>
            <input 
              type="text" 
              v-model="searchQuery"
              placeholder="Search models..." 
              class="w-full bg-transparent border-none p-4 focus:outline-none text-foreground placeholder:text-muted-foreground/50"
            />
          </div>
        </div>
      </div>

      <!-- Filters -->
      <div class="flex flex-wrap items-center justify-between gap-4 mb-8">
        <div class="flex items-center gap-2">
          <button 
            @click="selectedFilter = 'all'"
            :class="selectedFilter === 'all' ? 'bg-gradient-to-r from-orange-500 to-pink-600 text-white shadow-lg shadow-orange-500/20' : 'border border-white/10 hover:bg-white/5 hover:border-white/20 text-muted-foreground hover:text-foreground'"
            class="px-4 py-1.5 rounded-full text-sm font-medium transition-all"
          >
            All
          </button>
          <button 
            v-for="filter in ['value-based', 'policy-gradient', 'actor-critic']" 
            :key="filter" 
            @click="selectedFilter = filter"
            :class="selectedFilter === filter ? 'bg-gradient-to-r from-orange-500 to-pink-600 text-white shadow-lg shadow-orange-500/20' : 'border border-white/10 hover:bg-white/5 hover:border-white/20 text-muted-foreground hover:text-foreground'"
            class="px-4 py-1.5 rounded-full text-sm font-medium transition-all"
          >
            {{ filter }}
          </button>
        </div>
        
        <div class="relative">
          <select v-model="sortBy" class="appearance-none bg-white/5 border border-white/10 rounded-lg px-4 py-2 pr-10 text-sm font-medium text-muted-foreground hover:text-foreground hover:border-white/20 focus:outline-none focus:ring-2 focus:ring-primary/20 transition-all cursor-pointer">
            <option value="downloads">Most Downloads</option>
            <option value="newest">Newest</option>
          </select>
          <div class="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none text-muted-foreground">
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>
          </div>
        </div>
      </div>

      <!-- Loading State -->
      <div v-if="loading" class="text-center py-20">
        <div class="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-white"></div>
        <p class="mt-4 text-muted-foreground">Loading models...</p>
      </div>

      <!-- Error State -->
      <div v-else-if="error" class="text-center py-20">
        <p class="text-destructive mb-4">{{ error }}</p>
        <button @click="fetchModels" class="px-4 py-2 bg-white/10 hover:bg-white/20 rounded-lg transition-colors">
          Retry
        </button>
      </div>

      <!-- Model Grid -->
      <div v-else-if="filteredModels.length > 0" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        <div v-for="model in filteredModels" :key="model.id" @click="router.push(`/models/${model.id}`)" 
             class="group relative bg-white/5 hover:bg-white/10 border border-white/10 hover:border-white/20 rounded-2xl p-6 transition-all cursor-pointer hover:-translate-y-1 duration-300">
          
          <div class="flex justify-between items-start mb-4">
            <h3 class="text-xl font-semibold group-hover:text-white transition-colors">{{ model.name }}</h3>
            <span class="text-xs font-medium px-2.5 py-1 rounded-md bg-white/5 border border-white/10 text-muted-foreground">
              {{ model.type }}
            </span>
          </div>
          
          <p class="text-muted-foreground text-sm mb-6 line-clamp-2 leading-relaxed">
            {{ model.description }}
          </p>
          
          <div class="flex items-center justify-between text-sm text-muted-foreground mt-auto">
            <div class="flex items-center gap-2 group-hover:text-white/80 transition-colors">
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/></svg>
              {{ model.downloads.toLocaleString() }}
            </div>
            <div class="flex items-center gap-2">
              <div class="w-5 h-5 rounded-full bg-gradient-to-br from-blue-500 to-purple-600"></div>
              <span>{{ model.author }}</span>
            </div>
          </div>

          <!-- Hover Glow Effect -->
          <div class="absolute -inset-px rounded-2xl bg-gradient-to-br from-white/10 to-transparent opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none"></div>
        </div>
      </div>

      <!-- Empty State -->
      <div v-else class="text-center py-20">
        <p class="text-muted-foreground">No models found{{ searchQuery ? ' matching your search' : '' }}</p>
      </div>
    </main>

    <!-- Overlays -->
    <div v-if="showUpload" class="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm" @click.self="showUpload = false">
      <div class="w-full max-w-md bg-[#0a0a0c] border border-white/10 rounded-2xl p-6 shadow-2xl relative overflow-hidden">
        <div class="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-orange-500 to-pink-600"></div>
        <h2 class="text-2xl font-bold mb-2">Upload Model</h2>
        <p class="text-muted-foreground mb-6 text-sm">Share your reinforcement learning agent with the world.</p>
        
        <div class="space-y-4">
          <div class="space-y-2">
            <label class="text-sm font-medium text-gray-300">Model Name</label>
            <input 
              v-model="uploadModelName"
              type="text" 
              class="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-orange-500/50 transition-colors" 
              placeholder="e.g. PPO-CartPole-v1"
            >
          </div>
          
          <div class="space-y-2">
            <label class="text-sm font-medium text-gray-300">Description (optional)</label>
            <textarea 
              v-model="uploadDescription"
              class="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-orange-500/50 transition-colors resize-none"
              placeholder="Describe your model..."
              rows="3"
            ></textarea>
          </div>
          
          <div 
            @click="triggerFileInput"
            @dragover.prevent.stop="isDragging = true"
            @dragenter.prevent.stop="isDragging = true"
            @dragleave.prevent.stop="isDragging = false"
            @drop.prevent.stop="handleFileDrop"
            :class="isDragging ? 'border-white/30 bg-white/10' : 'border-white/10 hover:border-white/20 hover:bg-white/5'"
            class="border-2 border-dashed rounded-xl p-8 text-center transition-all cursor-pointer group"
          >
            <input 
              ref="fileInput"
              type="file" 
              @change="handleFileSelect"
              class="hidden"
              accept=".pt,.pth,.onnx,.zip,.tar,.gz,.h5,.ckpt,.json"
            >
            <div class="w-12 h-12 rounded-full bg-white/5 flex items-center justify-center mx-auto mb-4 group-hover:scale-110 transition-transform">
              <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-muted-foreground group-hover:text-white transition-colors"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" x2="12" y1="3" y2="15"/></svg>
            </div>
            <p v-if="!selectedFile" class="text-sm font-medium text-gray-300">Drop your model file here or click to browse</p>
            <p v-else class="text-sm font-medium text-white">{{ selectedFile.name }}</p>
            <p class="text-xs text-muted-foreground mt-1">.pt, .pth, .onnx, .zip, .tar, .gz, .h5, .ckpt, .json (max 500MB)</p>
            <p v-if="selectedFile" class="text-xs text-muted-foreground mt-1">
              Size: {{ (selectedFile.size / 1024 / 1024).toFixed(2) }} MB
            </p>
          </div>

          <!-- Upload Progress -->
          <div v-if="uploading" class="space-y-2">
            <div class="flex justify-between text-sm">
              <span class="text-muted-foreground">Uploading...</span>
              <span class="text-white">{{ Math.round(uploadProgress) }}%</span>
            </div>
            <div class="w-full bg-white/5 rounded-full h-2 overflow-hidden">
              <div 
                class="bg-gradient-to-r from-orange-500 to-pink-600 h-full transition-all duration-300"
                :style="{ width: `${uploadProgress}%` }"
              ></div>
            </div>
          </div>

          <!-- Upload Error -->
          <div v-if="uploadError" class="bg-destructive/20 border border-destructive/50 rounded-lg p-3">
            <p class="text-sm text-destructive">{{ uploadError }}</p>
          </div>

          <div class="flex justify-end gap-3 mt-6">
            <button 
              @click="cancelUpload"
              :disabled="uploading"
              class="px-4 py-2 text-sm font-medium text-muted-foreground hover:text-white transition-colors disabled:opacity-50"
            >
              Cancel
            </button>
            <button 
              @click="handleUpload"
              :disabled="!selectedFile || !uploadModelName || uploading"
              class="bg-gradient-to-r from-orange-500 to-pink-600 text-white px-6 py-2 rounded-lg text-sm font-medium shadow-lg shadow-orange-500/20 hover:shadow-orange-500/40 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {{ uploading ? 'Uploading...' : 'Upload' }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Auth Modal -->
    <div v-if="showAuth" class="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm" @click.self="showAuth = false">
      <div class="w-full max-w-sm bg-[#0a0a0c] border border-white/10 rounded-2xl p-8 shadow-2xl relative overflow-hidden">
        <div class="text-center mb-8">
          <div class="w-12 h-12 rounded-xl bg-gradient-to-br from-pink-500 to-purple-600 flex items-center justify-center text-white font-bold text-xl mx-auto mb-4 shadow-lg shadow-purple-500/30">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-6 h-6"><polygon points="12 2 2 7 12 12 22 7 12 2"></polygon><polyline points="2 17 12 22 22 17"></polyline><polyline points="2 12 12 17 22 12"></polyline></svg>
          </div>
          <h2 class="text-2xl font-bold">{{ isSignUp ? 'Create Account' : 'Welcome Back' }}</h2>
          <p class="text-muted-foreground text-sm mt-2">{{ isSignUp ? 'Sign up to start managing your models' : 'Sign in to manage your models' }}</p>
        </div>

        <!-- Error Message -->
        <div v-if="authError" class="mb-4 p-3 bg-destructive/20 border border-destructive/50 rounded-lg">
          <p class="text-sm text-destructive">{{ authError }}</p>
        </div>

        <div class="space-y-4">
          <!-- Sign Up Form -->
          <form v-if="isSignUp" @submit.prevent="handleSignUp" class="space-y-3">
            <input 
              v-model="signUpForm.username"
              type="text" 
              placeholder="Username" 
              required
              class="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-white/30 transition-colors"
            >
            <input 
              v-model="signUpForm.email"
              type="email" 
              placeholder="Email address" 
              required
              class="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-white/30 transition-colors"
            >
            <input 
              v-model="signUpForm.password"
              type="password" 
              placeholder="Password" 
              required
              minlength="6"
              class="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-white/30 transition-colors"
            >
            <div class="flex items-center gap-2">
              <input 
                v-model="signUpForm.isAdmin"
                type="checkbox" 
                id="isAdmin"
                class="w-4 h-4 rounded border-white/10 bg-white/5"
              >
              <label for="isAdmin" class="text-sm text-muted-foreground cursor-pointer">
                Create as admin user
              </label>
            </div>
            <button 
              type="submit"
              :disabled="authLoading"
              class="w-full bg-gradient-to-r from-purple-600 to-blue-600 text-white font-medium h-10 rounded-lg hover:opacity-90 transition-opacity shadow-lg shadow-purple-500/20 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {{ authLoading ? 'Creating...' : 'Sign Up' }}
            </button>
          </form>

          <!-- Sign In Form -->
          <form v-else @submit.prevent="handleSignIn" class="space-y-3">
            <input 
              v-model="signInForm.username"
              type="text" 
              placeholder="Username" 
              required
              class="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-white/30 transition-colors"
            >
            <input 
              v-model="signInForm.password"
              type="password" 
              placeholder="Password" 
              required
              class="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-white/30 transition-colors"
            >
            <button 
              type="submit"
              :disabled="authLoading"
              class="w-full bg-gradient-to-r from-purple-600 to-blue-600 text-white font-medium h-10 rounded-lg hover:opacity-90 transition-opacity shadow-lg shadow-purple-500/20 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {{ authLoading ? 'Signing in...' : 'Sign In' }}
            </button>
          </form>

          <div class="relative">
            <div class="absolute inset-0 flex items-center"><div class="w-full border-t border-white/10"></div></div>
            <div class="relative flex justify-center text-xs uppercase"><span class="bg-[#0a0a0c] px-2 text-muted-foreground">Or</span></div>
          </div>

          <button 
            @click="isSignUp = !isSignUp; authError = null"
            class="w-full text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            {{ isSignUp ? 'Already have an account? Sign in' : "Don't have an account? Sign up" }}
          </button>
        </div>
      </div>
    </div>

    <button class="fixed bottom-8 right-8 w-10 h-10 rounded-full bg-white/10 backdrop-blur flex items-center justify-center text-muted-foreground hover:text-white hover:bg-white/20 transition-all" title="Help">
      ?
    </button>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { models as modelsApi, authApi } from '../services/api'
import { useAuth } from '../composables/useAuth'
import { useFileUpload } from '../composables/useFileUpload'

const { login, logout, isAuthenticated } = useAuth()
const { uploading: isUploading, uploadProgress, uploadError: uploadErrorRef, uploadFile } = useFileUpload()
const fileInput = ref(null)
const selectedFile = ref(null)
const isDragging = ref(false)
const uploadModelName = ref('')
const uploadDescription = ref('')
const uploading = isUploading
const uploadError = uploadErrorRef

const router = useRouter()
const showUpload = ref(false)
const showAuth = ref(false)
const isSignUp = ref(false)
const authLoading = ref(false)
const authError = ref(null)
const signUpForm = ref({
  username: '',
  email: '',
  password: '',
  isAdmin: false
})
const signInForm = ref({
  username: '',
  password: ''
})
const models = ref([])
const loading = ref(true)
const error = ref(null)
const searchQuery = ref('')
const selectedFilter = ref('all')
const sortBy = ref('newest')

// Fetch models from API
const fetchModels = async () => {
  try {
    loading.value = true
    error.value = null
    const data = await modelsApi.list()
    
    // Transform API response to match component expectations
    models.value = data.map(model => ({
      id: model.id,
      name: model.name,
      type: 'policy-gradient', // Default type - you might want to add this to the backend
      description: model.description || 'No description available',
      downloads: model.versions?.length || 0, // Use version count as download proxy
      author: model.created_by || 'unknown'
    }))
  } catch (err) {
    console.error('Failed to fetch models:', err)
    error.value = err.message || 'Failed to load models'
  } finally {
    loading.value = false
  }
}

const handleSignUp = async () => {
  try {
    authLoading.value = true
    authError.value = null
    
    const response = await authApi.register(
      signUpForm.value.username,
      signUpForm.value.email,
      signUpForm.value.password,
      signUpForm.value.isAdmin
    )
    
    login(response.token)
    showAuth.value = false
    isSignUp.value = false
    signUpForm.value = { username: '', email: '', password: '', isAdmin: false }
  } catch (err) {
    authError.value = err.message || 'Failed to create account'
  } finally {
    authLoading.value = false
  }
}

const handleSignIn = async () => {
  try {
    authLoading.value = true
    authError.value = null
    
    const response = await authApi.login(
      signInForm.value.username,
      signInForm.value.password
    )
    
    login(response.token)
    showAuth.value = false
    signInForm.value = { username: '', password: '' }
  } catch (err) {
    authError.value = err.message || 'Invalid username or password'
  } finally {
    authLoading.value = false
  }
}

const triggerFileInput = () => {
  fileInput.value?.click()
}

const handleFileSelect = (event) => {
  const file = event.target.files[0]
  if (file) {
    selectedFile.value = file
    if (!uploadModelName.value) {
      // Auto-fill model name from filename
      uploadModelName.value = file.name.replace(/\.[^/.]+$/, '')
    }
  }
}

const handleFileDrop = (event) => {
  event.preventDefault()
  event.stopPropagation()
  isDragging.value = false
  const file = event.dataTransfer.files[0]
  if (file) {
    selectedFile.value = file
    if (!uploadModelName.value) {
      uploadModelName.value = file.name.replace(/\.[^/.]+$/, '')
    }
  }
}

const cancelUpload = () => {
  showUpload.value = false
  selectedFile.value = null
  uploadModelName.value = ''
  uploadDescription.value = ''
  uploadError.value = null
}

const handleUpload = async () => {
  if (!selectedFile.value || !uploadModelName.value) {
    return
  }

  try {
    await uploadFile(
      selectedFile.value,
      uploadModelName.value,
      uploadDescription.value
    )
    
    // Success - refresh models and close modal
    await fetchModels()
    cancelUpload()
    
    // Show success message (you could add a toast notification here)
    alert('Model uploaded successfully!')
  } catch (error) {
    // Error is already set in uploadError by useFileUpload
    console.error('Upload failed:', error)
    // The error message should already be in uploadError from useFileUpload
    // But ensure it's displayed
    if (!uploadError.value && error.message) {
      uploadError.value = error.message
    }
  }
}

onMounted(() => {
  fetchModels()
})

// Filtered and sorted models
const filteredModels = computed(() => {
  let result = [...models.value]
  
  // Apply search filter
  if (searchQuery.value) {
    const query = searchQuery.value.toLowerCase()
    result = result.filter(model => 
      model.name.toLowerCase().includes(query) ||
      model.description.toLowerCase().includes(query)
    )
  }
  
  // Apply type filter
  if (selectedFilter.value !== 'all') {
    result = result.filter(model => model.type === selectedFilter.value)
  }
  
  // Apply sorting
  if (sortBy.value === 'newest') {
    // Models are already in creation order from API
    result = [...result].reverse()
  } else if (sortBy.value === 'downloads') {
    result.sort((a, b) => b.downloads - a.downloads)
  }
  
  return result
})
</script>

