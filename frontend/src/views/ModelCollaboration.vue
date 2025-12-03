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
          <button class="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors">Sign In</button>
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
        <div class="flex gap-3">
           <button 
            @click="handleDownload"
            :disabled="downloading || !model || !model.versions || model.versions.length === 0"
            class="bg-white/5 hover:bg-white/10 border border-white/10 text-foreground px-4 py-2 rounded-lg font-medium transition-colors flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
            :title="(!model || !model.versions || model.versions.length === 0) ? 'No versions available for download. Upload a file to create a version.' : 'Download latest version'"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/></svg>
            {{ downloading ? 'Preparing...' : (model && model.versions && model.versions.length > 0 ? 'Download' : 'No Versions') }}
          </button>
          <button @click="goToCollaboration" class="bg-white/5 hover:bg-white/10 border border-white/10 text-foreground px-4 py-2 rounded-lg font-medium transition-colors flex items-center gap-2">
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
            Discussion
          </button>
          <button class="bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white px-6 py-2 rounded-lg font-medium transition-all shadow-lg shadow-blue-500/20">
            Run in Colab
          </button>
        </div>
      </div>

      <!-- Content Grid -->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
        <!-- Main Content -->
        <div class="lg:col-span-2 space-y-8">
          <!-- Performance Chart (Mock) -->
          <div class="bg-white/5 border border-white/10 rounded-2xl p-6">
            <h3 class="text-lg font-semibold mb-4">Training Performance</h3>
            <div class="h-64 flex items-end gap-2">
              <div v-for="i in 20" :key="i" 
                   class="flex-1 bg-gradient-to-t from-blue-500/20 to-purple-500/50 rounded-t-sm hover:from-blue-500/40 hover:to-purple-500/70 transition-colors"
                   :style="{ height: `${30 + Math.random() * 70}%` }"></div>
            </div>
            <div class="flex justify-between mt-2 text-xs text-muted-foreground">
              <span>Step 0</span>
              <span>Step 10M</span>
            </div>
          </div>

          <!-- Readme / Details -->
          <div class="bg-white/5 border border-white/10 rounded-2xl p-8">
            <h3 class="text-lg font-semibold mb-4">Model Details</h3>
            <div class="prose prose-invert max-w-none text-muted-foreground">
              <p>This model was trained using PPO with the following hyperparameters:</p>
              <ul class="list-disc pl-5 space-y-1 mt-2 mb-4">
                <li>Learning rate: 2.5e-4</li>
                <li>Clip range: 0.1</li>
                <li>Entropy coefficient: 0.01</li>
                <li>Batch size: 256</li>
              </ul>
              <p>It achieves state-of-the-art performance on the Atari Breakout environment, averaging 400+ score over 100 episodes.</p>
            </div>
          </div>

           
        </div>

        <!-- Sidebar -->
        <div class="space-y-6">
          <!-- Metadata Card -->
          <div class="bg-white/5 border border-white/10 rounded-2xl p-6 space-y-4">
            <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-wider">Metadata</h3>
            
            <div class="flex justify-between items-center py-2 border-b border-white/5">
              <span class="text-muted-foreground text-sm">Framework</span>
              <span class="text-white font-medium">PyTorch</span>
            </div>
            <div class="flex justify-between items-center py-2 border-b border-white/5">
              <span class="text-muted-foreground text-sm">Algorithm</span>
              <span class="text-white font-medium">PPO</span>
            </div>
            <div class="flex justify-between items-center py-2 border-b border-white/5">
              <span class="text-muted-foreground text-sm">License</span>
              <span class="text-white font-medium">MIT</span>
            </div>
            <div class="flex justify-between items-center py-2 border-b border-white/5">
              <span class="text-muted-foreground text-sm">Downloads</span>
              <span class="text-white font-medium">{{ model?.downloads?.toLocaleString() || 0 }}</span>
            </div>
            <div class="flex justify-between items-center py-2 border-b border-white/5">
              <span class="text-muted-foreground text-sm">Versions</span>
              <span class="text-white font-medium">{{ model?.versions?.length || 0 }}</span>
            </div>
            <div class="flex justify-between items-center py-2">
              <span class="text-muted-foreground text-sm">Created By</span>
              <span class="text-white font-medium">{{ model?.createdBy || 'Unknown' }}</span>
            </div>
          </div>

          <!-- Contributors -->
          <div class="bg-white/5 border border-white/10 rounded-2xl p-6">
             <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">Contributors</h3>
             <div class="flex -space-x-2 overflow-hidden">
               <div v-for="i in 3" :key="i" class="inline-block h-8 w-8 rounded-full ring-2 ring-[#030213] bg-gradient-to-br from-gray-700 to-gray-600"></div>
               <div class="h-8 w-8 rounded-full ring-2 ring-[#030213] bg-[#1a1b1e] flex items-center justify-center text-xs text-gray-400">+2</div>
             </div>
          </div>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { models as modelsApi, downloads as downloadsApi } from '../services/api'

const route = useRoute()
const router = useRouter()
const model = ref(null)
const loading = ref(true)
const error = ref(null)
const downloading = ref(false)

const goToCollaboration = () => {
  router.push({ name: 'Collaboration', params: { modelId: model.value.id } })
}

// Fetch model details
const fetchModel = async () => {
  try {
    loading.value = true
    error.value = null
    const id = parseInt(route.params.id)
    console.log('Fetching model:', id)
    
    const data = await modelsApi.get(id)
    console.log('Model data received:', data)
    
    // Fetch versions separately if not included in model response
    let versions = data.versions || []
    console.log('Versions from model response:', versions)
    
    if (!versions || versions.length === 0) {
      try {
        // Try to fetch versions from the versions endpoint
        console.log('Fetching versions separately...')
        versions = await modelsApi.getVersions(id)
        console.log('Versions fetched separately:', versions)
      } catch (versionErr) {
        console.warn('Could not fetch versions:', versionErr)
        versions = []
      }
    }
    
    // Sort versions by version number (descending - latest first)
    versions = versions.sort((a, b) => (b.version || 0) - (a.version || 0))
    console.log('Sorted versions:', versions)
    
    model.value = {
      id: data.id,
      name: data.name,
      description: data.description || 'No description available',
      downloads: versions.length,
      createdBy: data.created_by,
      versions: versions
    }
    
    console.log('Model state set:', model.value)
  } catch (err) {
    console.error('Failed to fetch model:', err)
    error.value = err.message || 'Failed to load model'
  } finally {
    loading.value = false
  }
}

// Download model
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
    
    // Get the latest version (first in sorted array, or highest version number)
    const latestVersion = model.value.versions[0]
    console.log('Latest version:', latestVersion)
    
    // Extract artifact ID (content_hash)
    let artifactId = latestVersion.content_hash
    
    console.log('Artifact ID from version:', artifactId)
    
    // Ensure artifact ID is in correct format (sha256:...)
    if (!artifactId) {
      console.error('Version missing content_hash:', latestVersion)
      throw new Error('Version does not have a content hash. The model may not have been fully uploaded.')
    }
    
    // Normalize format: ensure it starts with 'sha256:'
    if (!artifactId.startsWith('sha256:')) {
      // If it's just the hash without prefix, add it
      if (/^[a-f0-9]{64}$/i.test(artifactId)) {
        artifactId = `sha256:${artifactId}`
        console.log('Normalized artifact ID:', artifactId)
      } else {
        throw new Error(`Invalid artifact ID format: ${artifactId}. Expected sha256:... or 64-character hex string.`)
      }
    }
    
    console.log('Requesting download URL for:', artifactId)
    
    // Get download URL from API
    const downloadData = await downloadsApi.getDownloadUrl(artifactId)
    
    console.log('Download response:', downloadData)
    
    if (!downloadData || !downloadData.download_url) {
      console.error('Invalid download response:', downloadData)
      throw new Error('Invalid download response from server. Please try again.')
    }
    
    // Replace Docker internal hostname with localhost for browser access
    // Backend should generate URLs with localhost:9000, but this is a fallback
    let downloadUrl = downloadData.download_url
    downloadUrl = downloadUrl.replace(/http:\/\/minio:9000/g, 'http://localhost:9000')
    
    // Open download URL in new tab
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

onMounted(() => {
  fetchModel()
})
</script>

