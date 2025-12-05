<template>
  <div class="comment-node flex items-start gap-4">
    <div class="w-10 h-10 rounded-full bg-gradient-to-br from-gray-700 to-gray-600 flex-shrink-0 mt-1">
      <span class="flex items-center justify-center h-full text-sm font-bold">{{ initials }}</span>
    </div>
    <div class="flex-grow">
      <div v-if="!isEditing" class="bg-white/5 border border-white/10 rounded-lg">
        <div class="px-4 py-3 border-b border-white/10 flex justify-between items-center">
          <div class="flex items-center gap-2">
            <span class="font-semibold text-white">{{ comment.authorName }}</span>
            <span v-if="comment.isCreator" class="px-2 py-0.5 rounded-full bg-yellow-500/20 text-xs text-yellow-400 font-medium" title="Model Creator">★ Creator</span>
          </div>
          <span class="text-xs text-muted-foreground">{{ formattedDate }}</span>
        </div>
        <div class="px-4 py-3 text-muted-foreground">
          <p>{{ comment.content }}</p>
        </div>
      </div>
      <div v-else>
        <textarea
          v-model="editableContent"
          class="w-full bg-white/5 border border-white/10 rounded-lg p-3 text-foreground placeholder-muted-foreground focus:outline-none focus:ring-2 focus:ring-purple-500 transition-all"
          rows="3"
        ></textarea>
      </div>
      <div class="mt-2 flex items-center gap-4">
        <template v-if="!isEditing">
          <button @click="showReplyForm = !showReplyForm" class="text-xs font-medium text-muted-foreground hover:text-white transition-colors">Reply</button>
          <button v-if="currentUser && comment.authorId === currentUser.username" @click="isEditing = true" class="text-xs font-medium text-muted-foreground hover:text-white transition-colors">Edit</button>
          <button v-if="currentUser && comment.authorId === currentUser.username" @click="deleteComment" class="text-xs font-medium text-red-400 hover:text-red-300 transition-colors">Delete</button>
        </template>
        <template v-else>
          <button @click="saveEdit" class="text-xs font-medium text-green-400 hover:text-green-300 transition-colors">Save</button>
          <button @click="isEditing = false" class="text-xs font-medium text-muted-foreground hover:text-white transition-colors">Cancel</button>
        </template>
      </div>

      <div v-if="showReplyForm" class="mt-4 flex items-start gap-4">
        <div class="w-8 h-8 rounded-full bg-gradient-to-br from-gray-700 to-gray-600 flex-shrink-0"></div>
        <div class="flex-grow">
          <textarea
            v-model="replyContent"
            class="w-full bg-white/5 border border-white/10 rounded-lg p-2 text-foreground placeholder-muted-foreground focus:outline-none focus:ring-2 focus:ring-purple-500 transition-all"
            placeholder="Write a reply..."
            rows="2"
          ></textarea>
          <div class="mt-2 flex justify-end gap-2">
            <button @click="showReplyForm = false" class="text-xs font-medium text-muted-foreground hover:text-white transition-colors px-3 py-1">Cancel</button>
            <button @click="submitReply" class="bg-blue-600 hover:bg-blue-500 text-white px-4 py-1 rounded-md text-xs font-medium transition-colors disabled:opacity-50" :disabled="!replyContent.trim()">
              Post Reply
            </button>
          </div>
        </div>
      </div>

      <div v-if="comment.replies && comment.replies.length > 0" class="mt-4 pl-8 border-l-2 border-white/10 space-y-4">
        <CommentNode 
          v-for="reply in comment.replies" 
          :key="reply.id" 
          :comment="reply"
          :current-user="currentUser"
          @post-reply="emitPostReply"
          @delete-comment="emitDeleteComment"
          @update-comment="emitUpdateComment"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { collaboration } from '../services/collaboration'

const props = defineProps({
  comment: {
    type: Object,
    required: true
  },
  currentUser: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['post-reply', 'delete-comment', 'update-comment'])

const isEditing = ref(false)
const editableContent = ref(props.comment.content)
const showReplyForm = ref(false)
const replyContent = ref('')

watch(() => props.comment.content, (newContent) => {
  editableContent.value = newContent
})

const initials = computed(() => {
  return props.comment.authorName.substring(0, 2).toUpperCase()
})

const formattedDate = computed(() => {
  return new Date(props.comment.createdAt).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
  })
})

const submitReply = () => {
  if (!replyContent.value.trim()) return
  emit('post-reply', props.comment.id, replyContent.value)
  replyContent.value = ''
  showReplyForm.value = false
}

const deleteComment = async () => {
  if (confirm('Are you sure you want to delete this comment?')) {
    try {
      await collaboration.deleteComment(props.comment.id)
      emit('delete-comment')
    } catch (err) {
      alert('Failed to delete comment: ' + err.message)
    }
  }
}

const saveEdit = () => {
  if (!editableContent.value.trim()) return
  emit('update-comment', props.comment.id, editableContent.value)
  isEditing.value = false
}

const emitPostReply = (parentId, content) => {
  emit('post-reply', parentId, content)
}

const emitDeleteComment = () => {
  emit('delete-comment')
}

const emitUpdateComment = (commentId, content) => {
  emit('update-comment', commentId, content)
}
</script>
