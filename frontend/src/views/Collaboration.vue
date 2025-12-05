<template>
  <div class="min-h-screen text-foreground bg-background">
    <div class="fixed inset-0 -z-10 overflow-hidden pointer-events-none">
      <div class="absolute -top-[20%] -left-[10%] w-[70vw] h-[70vw] rounded-full bg-[#9333ea]/30 blur-[120px] animate-float"></div>
      <div class="absolute top-[20%] -right-[10%] w-[60vw] h-[60vw] rounded-full bg-[#4f46e5]/30 blur-[120px] animate-float-delayed"></div>
    </div>

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

    <main class="max-w-3xl mx-auto px-6 py-12">
      <button @click="router.back()" class="flex items-center gap-2 text-muted-foreground hover:text-foreground mb-8 transition-colors group">
        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="group-hover:-translate-x-1 transition-transform"><path d="m15 18-6-6 6-6"/></svg>
        Back to Model
      </button>

      <div class="bg-white/5 border border-white/10 rounded-2xl">
        <div class="p-6 border-b border-white/10">
          <h2 class="text-2xl font-bold flex items-center gap-3">
            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
            Discussion
          </h2>
          <p class="text-muted-foreground mt-1">Model ID: {{ modelId }}</p>
        </div>

        <div class="p-6 space-y-6">
          <!-- Comment Input -->
          <div v-if="isAuthenticated" class="flex items-start gap-4">
            <div class="w-10 h-10 rounded-full bg-gradient-to-br from-gray-700 to-gray-600 flex-shrink-0"></div>
            <div class="flex-grow">
              <textarea
                v-model="newCommentContent"
                class="w-full bg-white/5 border border-white/10 rounded-lg p-3 text-foreground placeholder-muted-foreground focus:outline-none focus:ring-2 focus:ring-purple-500 transition-all"
                placeholder="Add a comment..."
                rows="3"
              ></textarea>
              <div class="mt-3 flex justify-end">
                <button @click="postComment(null, newCommentContent)" class="bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white px-5 py-2 rounded-lg font-medium transition-all shadow-lg shadow-blue-500/20 disabled:opacity-50" :disabled="!newCommentContent.trim()">
                  Post Comment
                </button>
              </div>
            </div>
          </div>
          <div v-else class="text-center p-8 bg-white/5 rounded-lg">
            <p class="text-muted-foreground">You must be signed in to join the discussion.</p>
          </div>

          <!-- Comments List -->
          <div v-if="loading" class="text-center py-10">
            <div class="inline-block animate-spin rounded-full h-10 w-10 border-b-2 border-white"></div>
            <p class="mt-3 text-muted-foreground">Loading comments...</p>
          </div>
          <div v-else-if="error" class="text-center py-10">
            <p class="text-destructive">{{ error }}</p>
          </div>
          <div v-else-if="comments.length === 0" class="text-center py-10">
            <p class="text-muted-foreground">No comments yet. Be the first to start the discussion!</p>
          </div>
          <div v-else class="space-y-6">
            <CommentNode 
              v-for="comment in comments" 
              :key="comment.id" 
              :comment="comment"
              :current-user="currentUser"
              @post-reply="postComment"
              @delete-comment="fetchComments"
              @update-comment="updateComment"
            />
          </div>
        </div>
      </div>
    </main>

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
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { collaboration } from '../services/collaboration'
import { authApi } from '../services/api'
import { useAuth } from '../composables/useAuth'
import CommentNode from '../components/CommentNode.vue'

const route = useRoute()
const router = useRouter()
const { login, logout, isAuthenticated, currentUser } = useAuth()

const modelId = ref(route.params.modelId)
const comments = ref([])
const loading = ref(true)
const error = ref(null)
const newCommentContent = ref('')

// Auth state
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

const fetchComments = async () => {
  try {
    loading.value = true
    const response = await collaboration.getComments(modelId.value)
    comments.value = response.data
  } catch (err) {
    error.value = err.message || 'Failed to load comments.'
  } finally {
    loading.value = false
  }
}

const postComment = async (parentId, content) => {
  if (!isAuthenticated.value) {
    alert('Please sign in to post a comment.')
    return
  }

  const payload = {
    content: content,
    authorId: currentUser.value.username,
    authorName: currentUser.value.username,
    parentId: parentId
  }

  try {
    await collaboration.createComment(modelId.value, payload)
    if (!parentId) {
      newCommentContent.value = ''
    }
    fetchComments() // Refresh comments
  } catch (err) {
    alert('Failed to post comment: ' + err.message)
  }
}

const updateComment = async (commentId, content) => {
  try {
    await collaboration.updateComment(commentId, { content })
    fetchComments() // Refresh comments
  } catch (err) {
    alert('Failed to update comment: ' + err.message)
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

onMounted(fetchComments)
</script>

<style scoped>
@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-20px); }
}
.animate-float {
  animation: float 6s ease-in-out infinite;
}
.animate-float-delayed {
  animation: float 8s ease-in-out infinite 2s;
}
</style>
