<template>
  <div class="min-h-screen text-foreground relative overflow-hidden">
    <!-- Same Background Gradients -->
    <div class="fixed inset-0 -z-10 overflow-hidden pointer-events-none">
      <div class="absolute -top-[20%] -left-[10%] w-[70vw] h-[70vw] rounded-full bg-[#9333ea]/30 blur-[120px] animate-float"></div>
      <div class="absolute top-[20%] -right-[10%] w-[60vw] h-[60vw] rounded-full bg-[#4f46e5]/30 blur-[120px] animate-float-delayed"></div>
    </div>

    <!-- Navigation -->
    <nav class="sticky top-0 z-50 border-b border-white/10 bg-background/60 backdrop-blur-xl">
      <div class="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
        <div class="flex items-center gap-2 cursor-pointer" @click="router.push('/')">
          <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-pink-500 to-purple-600 flex items-center justify-center text-white font-bold">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-5 h-5"><polygon points="12 2 2 7 12 12 22 7 12 2"></polygon><polyline points="2 17 12 22 22 17"></polyline><polyline points="2 12 12 17 22 12"></polyline></svg>
          </div>
          <span class="text-xl font-bold tracking-tight">RLLabs</span>
        </div>

        <div class="flex items-center gap-4">
          <button v-if="!isAuthenticated" @click="showAuth = true; isSignUp = false" class="text-sm font-medium border border-white/10 hover:bg-white/5 px-4 py-2 rounded-lg transition-colors">Sign In</button>
          <button v-else @click="logout" class="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors">Sign Out</button>
        </div>
      </div>
    </nav>

    <main class="max-w-7xl mx-auto px-6 py-12">
      <!-- Back Button -->
      <button @click="router.back()" class="flex items-center gap-2 text-muted-foreground hover:text-foreground mb-8 transition-colors group">
        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="group-hover:-translate-x-1 transition-transform"><path d="m15 18-6-6 6-6"/></svg>
        Back to Models
      </button>

      <!-- Loading State -->
      <div v-if="loading" class="text-center py-20">
        <div class="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-white"></div>
        <p class="mt-4 text-muted-foreground">Loading model...</p>
      </div>

      <!-- Error State -->
      <div v-else-if="error" class="text-center py-20">
        <p class="text-destructive mb-4">{{ error }}</p>
        <button @click="fetchModel" class="px-4 py-2 bg-white/10 hover:bg-white/20 rounded-lg transition-colors">
          Retry
        </button>
      </div>

      <!-- Model Header -->
      <div v-else-if="model" class="flex flex-col md:flex-row justify-between items-start gap-6 mb-12">
        <div>
          <div class="flex items-center gap-3 mb-2">
            <h1 class="text-3xl font-bold">{{ model.name }}</h1>
            <span v-if="model.versions && model.versions.length > 0" class="px-2.5 py-1 rounded-md bg-white/5 border border-white/10 text-sm text-muted-foreground">
              v{{ model.versions.length }}
            </span>
            <span v-else class="px-2.5 py-1 rounded-md bg-yellow-500/20 border border-yellow-500/30 text-sm text-yellow-400">
              No versions yet
            </span>
          </div>
          <p class="text-xl text-muted-foreground max-w-2xl">{{ model.description }}</p>
        </div>
        <div class="flex gap-3 flex-wrap">
          <button 
            @click="handleDownload"
            :disabled="downloading || !model || !model.versions || model.versions.length === 0"
            class="bg-white/5 hover:bg-white/10 border border-white/10 text-foreground px-4 py-2 rounded-lg font-medium transition-colors flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
            :title="(!model || !model.versions || model.versions.length === 0) ? 'No versions available for download. Upload a file to create a version.' : 'Download latest version'"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/></svg>
            {{ downloading ? 'Preparing...' : (model && model.versions && model.versions.length > 0 ? 'Download' : 'No Versions') }}
          </button>
          <button 
            v-if="isAuthenticated && model && model.versions && model.versions.length > 0"
            @click="showVersionModal = true"
            class="bg-white/5 hover:bg-white/10 border border-white/10 text-foreground px-4 py-2 rounded-lg font-medium transition-colors flex items-center gap-2"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3h18v18H3zM12 8v8M8 12h8"/></svg>
            Versions ({{ model.versions.length }})
          </button>
          <button 
            v-if="isAuthenticated && isOwner"
            @click="showTrainingModal = true"
            class="bg-gradient-to-r from-green-600 to-emerald-600 hover:from-green-500 hover:to-emerald-500 text-white px-4 py-2 rounded-lg font-medium transition-all shadow-lg shadow-green-500/20 flex items-center gap-2"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
            Train Model
          </button>
          <button @click="goToCollaboration" class="bg-white/5 hover:bg-white/10 border border-white/10 text-foreground px-4 py-2 rounded-lg font-medium transition-colors flex items-center gap-2">
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
            Discussion
          </button>
          <button 
            v-if="isAuthenticated && (isOwner || currentUser?.is_admin)"
            @click="handleDeleteModel"
            :disabled="deleting"
            class="bg-destructive/20 border border-destructive/50 text-destructive px-4 py-2 rounded-lg font-medium transition-colors flex items-center gap-2 hover:bg-destructive/30 disabled:opacity-50"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>
            {{ deleting ? 'Deleting...' : 'Delete' }}
          </button>
        </div>
      </div>

      <!-- Content Grid -->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
        <!-- Main Content -->
        <div class="lg:col-span-2 space-y-8">
          <!-- Model Description -->
          <div class="bg-white/5 border border-white/10 rounded-2xl p-8">
            <h3 class="text-lg font-semibold mb-4">Description</h3>
            <div class="prose prose-invert max-w-none text-muted-foreground">
              <p>{{ model?.description || 'No description available' }}</p>
            </div>
          </div>
        </div>

        <!-- Sidebar -->
        <div class="space-y-6">
          <!-- Metadata Card -->
          <div class="bg-white/5 border border-white/10 rounded-2xl p-6 space-y-4">
            <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-wider">Metadata</h3>
            
            <div class="flex justify-between items-center py-2 border-b border-white/5">
              <span class="text-muted-foreground text-sm">Downloads</span>
              <span class="text-white font-medium">{{ model?.downloads?.toLocaleString() || 0 }}</span>
            </div>
            <div class="flex justify-between items-center py-2 border-b border-white/5">
              <span class="text-muted-foreground text-sm">Versions</span>
              <span class="text-white font-medium">{{ model?.versions?.length || 0 }}</span>
            </div>
            <div class="flex justify-between items-center py-2 border-b border-white/5">
              <span class="text-muted-foreground text-sm">Created By</span>
              <span class="text-white font-medium">{{ model?.createdBy || 'Unknown' }}</span>
            </div>
            <div v-if="model?.createdAt" class="flex justify-between items-center py-2">
              <span class="text-muted-foreground text-sm">Created</span>
              <span class="text-white font-medium">{{ new Date(model.createdAt).toLocaleDateString() }}</span>
            </div>
          </div>

          <!-- Training Jobs -->
          <div v-if="isAuthenticated && isOwner" class="bg-white/5 border border-white/10 rounded-2xl p-6">
            <div class="flex items-center justify-between mb-4">
              <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-wider">Training Jobs</h3>
              <button @click="fetchTrainingJobs" class="text-xs text-muted-foreground hover:text-foreground transition-colors">
                Refresh
              </button>
            </div>
            <div v-if="loadingJobs" class="text-center py-4">
              <div class="inline-block animate-spin rounded-full h-4 w-4 border-b-2 border-white"></div>
            </div>
            <div v-else-if="trainingJobs.length === 0" class="text-sm text-muted-foreground text-center py-4">
              No training jobs yet
            </div>
            <div v-else class="space-y-2">
              <div v-for="job in trainingJobs.slice(0, 5)" :key="job.job_id" class="text-sm">
                <div class="flex items-center justify-between">
                  <span class="text-foreground font-medium">{{ job.job_id }}</span>
                  <span :class="{
                    'text-green-400': job.status === 'completed',
                    'text-yellow-400': job.status === 'running',
                    'text-blue-400': job.status === 'queued',
                    'text-red-400': job.status === 'failed',
                    'text-muted-foreground': !job.status
                  }" class="text-xs capitalize">{{ job.status || 'unknown' }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>

    <!-- Version Management Modal -->
    <div v-if="showVersionModal" class="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm" @click.self="showVersionModal = false">
      <div class="w-full max-w-2xl bg-[#0a0a0c] border border-white/10 rounded-2xl p-6 shadow-2xl relative overflow-hidden max-h-[80vh] overflow-y-auto">
        <div class="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-blue-500 to-purple-600"></div>
        <div class="flex items-center justify-between mb-6">
          <h2 class="text-2xl font-bold">Model Versions</h2>
          <button @click="showVersionModal = false" class="text-muted-foreground hover:text-foreground transition-colors">
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>
        
        <div v-if="model && model.versions && model.versions.length > 0" class="space-y-3">
          <div v-for="version in model.versions" :key="version.version" 
               class="bg-white/5 border border-white/10 rounded-lg p-4 hover:bg-white/10 transition-colors">
            <div class="flex items-center justify-between">
              <div>
                <div class="flex items-center gap-2 mb-1">
                  <span class="font-semibold">Version {{ version.version }}</span>
                  <span v-if="version === model.versions[0]" class="text-xs px-2 py-0.5 rounded bg-green-500/20 text-green-400">Latest</span>
                </div>
                <p class="text-sm text-muted-foreground">
                  Hash: {{ version.content_hash?.substring(0, 16) }}...
                </p>
                <p v-if="version.created_at" class="text-xs text-muted-foreground mt-1">
                  Created: {{ new Date(version.created_at).toLocaleDateString() }}
                </p>
              </div>
              <button 
                @click="handleDownloadVersion(version)"
                :disabled="downloading"
                class="bg-white/5 hover:bg-white/10 border border-white/10 px-3 py-1.5 rounded text-sm font-medium transition-colors disabled:opacity-50"
              >
                Download
              </button>
            </div>
          </div>
        </div>
        <div v-else class="text-center py-8 text-muted-foreground">
          No versions available
        </div>
      </div>
    </div>

    <!-- Training Job Creation Modal -->
    <div v-if="showTrainingModal" class="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm" @click.self="showTrainingModal = false">
      <div class="w-full max-w-md bg-[#0a0a0c] border border-white/10 rounded-2xl p-6 shadow-2xl relative overflow-hidden">
        <div class="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-green-500 to-emerald-600"></div>
        <div class="flex items-center justify-between mb-6">
          <h2 class="text-2xl font-bold">Create Training Job</h2>
          <button @click="showTrainingModal = false" class="text-muted-foreground hover:text-foreground transition-colors">
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>
        
        <div class="space-y-4">
          <p class="text-sm text-muted-foreground mb-4">
            Create a training job using the latest model version. You'll need to upload config, dataset, and model artifacts first.
          </p>
          
          <div class="space-y-3">
            <div>
              <label class="text-sm font-medium text-gray-300 mb-2 block">Config Artifact ID</label>
              <input 
                v-model="trainingForm.configArtifactId"
                type="text" 
                placeholder="sha256:..."
                class="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-green-500/50 transition-colors"
              >
            </div>
            
            <div>
              <label class="text-sm font-medium text-gray-300 mb-2 block">Dataset Artifact ID</label>
              <input 
                v-model="trainingForm.datasetArtifactId"
                type="text" 
                placeholder="sha256:..."
                class="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-green-500/50 transition-colors"
              >
            </div>
            
            <div>
              <label class="text-sm font-medium text-gray-300 mb-2 block">Model Artifact ID</label>
              <input 
                v-model="trainingForm.modelArtifactId"
                type="text" 
                placeholder="sha256:... (or leave empty to use latest version)"
                class="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-green-500/50 transition-colors"
              >
              <p class="text-xs text-muted-foreground mt-1">Leave empty to use the latest model version</p>
            </div>
          </div>

          <div v-if="trainingError" class="bg-destructive/20 border border-destructive/50 rounded-lg p-3">
            <p class="text-sm text-destructive">{{ trainingError }}</p>
          </div>

          <div class="flex justify-end gap-3 mt-6">
            <button 
              @click="showTrainingModal = false"
              class="px-4 py-2 text-sm font-medium text-muted-foreground hover:text-white transition-colors"
            >
              Cancel
            </button>
            <button 
              @click="handleCreateTrainingJob"
              :disabled="creatingJob || !trainingForm.configArtifactId || !trainingForm.datasetArtifactId"
              class="bg-gradient-to-r from-green-600 to-emerald-600 text-white px-6 py-2 rounded-lg text-sm font-medium shadow-lg shadow-green-500/20 hover:shadow-green-500/40 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {{ creatingJob ? 'Creating...' : 'Create Job' }}
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
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { models as modelsApi, downloads as downloadsApi, authApi } from '../services/api'
import { useAuth } from '../composables/useAuth'
import { training as trainingApi } from '../services/training'

const route = useRoute()
const router = useRouter()
const { login, logout, isAuthenticated, currentUser } = useAuth()
const model = ref(null)
const loading = ref(true)
const error = ref(null)
const downloading = ref(false)
const deleting = ref(false)
const isOwner = ref(false)
const showVersionModal = ref(false)
const showTrainingModal = ref(false)
const selectedVersion = ref(null)
const trainingJobs = ref([])
const loadingJobs = ref(false)

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

const goToCollaboration = () => {
  router.push({ name: 'Collaboration', params: { modelId: model.value.id } })
}

const fetchModel = async () => {
  try {
    loading.value = true
    error.value = null
    const id = parseInt(route.params.id)
    console.log('Fetching model:', id)
    
    let data
    try {
      data = await modelsApi.get(id)
      console.log('Model data received:', data)
    } catch (err) {
      console.warn('API unavailable, showing demo model:', err)
      data = {
        id: id,
        name: 'Demo PPO Model',
        description: 'This is a demo model for testing the UI. The API is currently unavailable.',
        created_by: 'Demo User',
        created_at: new Date().toISOString(),
        versions: []
      }
    }
    
    if (!data) {
      data = {
        id: id,
        name: 'Demo PPO Model',
        description: 'This is a demo model for testing the UI. The API is currently unavailable.',
        created_by: 'Demo User',
        created_at: new Date().toISOString(),
        versions: []
      }
    }
    
    if (isAuthenticated.value) {
      try {
        const ownership = await modelsApi.checkOwnership(id)
        isOwner.value = ownership.is_owner || false
      } catch (err) {
        console.warn('Failed to check ownership:', err)
        isOwner.value = false
      }
    }
    
    let versions = data.versions || []
    console.log('Versions from model response:', versions)
    
    if (!versions || versions.length === 0) {
      try {
        console.log('Fetching versions separately...')
        versions = await modelsApi.getVersions(id)
        console.log('Versions fetched separately:', versions)
      } catch (versionErr) {
        console.warn('Could not fetch versions:', versionErr)
        versions = []
      }
    }
    
    versions = versions.sort((a, b) => (b.version || 0) - (a.version || 0))
    console.log('Sorted versions:', versions)
    
    model.value = {
      id: data.id,
      name: data.name,
      description: data.description || 'No description available',
      downloads: versions.length,
      createdBy: data.created_by,
      createdAt: data.created_at,
      versions: versions
    }
    
    console.log('Model state set:', model.value)
    
    if (isAuthenticated.value && isOwner.value) {
      fetchTrainingJobs()
    }
  } catch (err) {
    console.error('Failed to fetch model:', err)
    error.value = err.message || 'Failed to load model'
  } finally {
    loading.value = false
  }
}

const trainingForm = ref({
  configArtifactId: '',
  datasetArtifactId: '',
  modelArtifactId: ''
})
const creatingJob = ref(false)
const trainingError = ref(null)

const normalizeArtifactId = (id) => {
  if (!id) return id
  if (id.startsWith('sha256:')) return id
  if (/^[a-f0-9]{64}$/i.test(id.trim())) {
    return `sha256:${id.trim()}`
  }
  return id
}

const handleCreateTrainingJob = async () => {
  if (!model.value) return
  
  try {
    creatingJob.value = true
    trainingError.value = null
    
    let modelArtifactId = trainingForm.value.modelArtifactId
    if (!modelArtifactId && model.value.versions && model.value.versions.length > 0) {
      modelArtifactId = model.value.versions[0].content_hash
    }
    
    if (!modelArtifactId) {
      throw new Error('No model artifact ID available. Please upload a model version first.')
    }
    
    const configId = normalizeArtifactId(trainingForm.value.configArtifactId)
    const datasetId = normalizeArtifactId(trainingForm.value.datasetArtifactId)
    const normalizedModelId = normalizeArtifactId(modelArtifactId)
    
    const job = await trainingApi.createJob({
      config_artifact_id: configId,
      dataset_artifact_id: datasetId,
      model_artifact_id: normalizedModelId,
      model_id: model.value.id
    })
    
    await fetchTrainingJobs()
    
    trainingForm.value = {
      configArtifactId: '',
      datasetArtifactId: '',
      modelArtifactId: ''
    }
    showTrainingModal.value = false
    
    alert(`Training job created successfully! Job ID: ${job.job_id}`)
  } catch (err) {
    console.error('Failed to create training job:', err)
    trainingError.value = err.message || 'Failed to create training job'
  } finally {
    creatingJob.value = false
  }
}

const fetchTrainingJobs = async () => {
  if (!isAuthenticated.value) return
  
  try {
    loadingJobs.value = true
    const jobs = await trainingApi.listJobs()
    if (!jobs || !Array.isArray(jobs)) {
      trainingJobs.value = []
      return
    }
    trainingJobs.value = jobs.filter(job => job.model_id === model.value?.id) || []
  } catch (err) {
    console.error('Failed to fetch training jobs:', err)
    trainingJobs.value = []
  } finally {
    loadingJobs.value = false
  }
}

const handleDeleteModel = async () => {
  if (!model.value) return
  
  if (!confirm('Are you sure you want to delete this model? This action cannot be undone.')) {
    return
  }

  try {
    deleting.value = true
    await modelsApi.delete(model.value.id)
    router.push('/')
  } catch (err) {
    console.error('Failed to delete model:', err)
    alert(`Failed to delete model: ${err.message || 'Unknown error'}`)
  } finally {
    deleting.value = false
  }
}

const handleDownloadVersion = async (version) => {
  if (!version || !version.content_hash) {
    alert('Version does not have a content hash')
    return
  }
  
  try {
    downloading.value = true
    let artifactId = version.content_hash
    
    if (!artifactId.startsWith('sha256:')) {
      if (/^[a-f0-9]{64}$/i.test(artifactId)) {
        artifactId = `sha256:${artifactId}`
      } else {
        throw new Error(`Invalid artifact ID format: ${artifactId}`)
      }
    }
    
    const downloadData = await downloadsApi.getDownloadUrl(artifactId)
    
    if (!downloadData || !downloadData.download_url) {
      throw new Error('Invalid download response from server')
    }
    
    let downloadUrl = downloadData.download_url
    if (downloadUrl.includes('minio:9000')) {
      downloadUrl = downloadUrl.replace(/http:\/\/minio:9000/g, 'http://localhost:9000')
    }
    
    window.open(downloadUrl, '_blank')
  } catch (err) {
    console.error('Download error:', err)
    alert(`Download failed: ${err.message || 'Unknown error'}`)
  } finally {
    downloading.value = false
  }
}

const handleDownload = async () => {
  console.log('Download button clicked', { model: model.value })
  
  if (!model.value) {
    alert('Model not loaded')
    return
  }
  
  if (!model.value.versions || model.value.versions.length === 0) {
    console.warn('No versions available', { versions: model.value.versions })
    alert('No versions available for download. Please ensure the model has been uploaded.')
    return
  }
  
  try {
    downloading.value = true
    
    const latestVersion = model.value.versions[0]
    console.log('Latest version:', latestVersion)
    
    let artifactId = latestVersion.content_hash
    
    console.log('Artifact ID from version:', artifactId)
    
    if (!artifactId) {
      console.error('Version missing content_hash:', latestVersion)
      throw new Error('Version does not have a content hash. The model may not have been fully uploaded.')
    }
    
    if (!artifactId.startsWith('sha256:')) {
      if (/^[a-f0-9]{64}$/i.test(artifactId)) {
        artifactId = `sha256:${artifactId}`
        console.log('Normalized artifact ID:', artifactId)
      } else {
        throw new Error(`Invalid artifact ID format: ${artifactId}. Expected sha256:... or 64-character hex string.`)
      }
    }
    
    console.log('Requesting download URL for:', artifactId)
    
    const downloadData = await downloadsApi.getDownloadUrl(artifactId)
    
    console.log('Download response:', downloadData)
    
    if (!downloadData || !downloadData.download_url) {
      console.error('Invalid download response:', downloadData)
      throw new Error('Invalid download response from server. Please try again.')
    }
    
    let downloadUrl = downloadData.download_url
    if (downloadUrl.includes('minio:9000')) {
      downloadUrl = downloadUrl.replace(/http:\/\/minio:9000/g, 'http://localhost:9000')
    }
    
    console.log('Opening download URL:', downloadUrl)
    window.open(downloadUrl, '_blank')
  } catch (err) {
    console.error('Download error:', err)
    const errorMessage = err.message || 'Failed to get download URL'
    alert(`Download failed: ${errorMessage}`)
  } finally {
    downloading.value = false
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

onMounted(() => {
  fetchModel()
})
</script>
