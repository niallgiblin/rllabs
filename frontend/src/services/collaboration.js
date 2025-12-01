// Collaboration Service API (for future integration)
// This service will handle comments, discussions, and collaboration features

// Import shared API utilities
import { apiRequest } from './api.js'

export const collaboration = {
  // Comments API (to be implemented)
  async getComments(modelId) {
    return apiRequest(`/api/models/${modelId}/comments`)
  },
  
  async createComment(modelId, comment) {
    return apiRequest(`/api/models/${modelId}/comments`, {
      method: 'POST',
      body: JSON.stringify(comment)
    })
  },
  
  async updateComment(commentId, comment) {
    return apiRequest(`/api/comments/${commentId}`, {
      method: 'PUT',
      body: JSON.stringify(comment)
    })
  },
  
  async deleteComment(commentId) {
    return apiRequest(`/api/comments/${commentId}`, {
      method: 'DELETE'
    })
  },
  
  // Discussions API (to be implemented)
  async getDiscussions(modelId) {
    return apiRequest(`/api/models/${modelId}/discussions`)
  },
  
  async createDiscussion(modelId, discussion) {
    return apiRequest(`/api/models/${modelId}/discussions`, {
      method: 'POST',
      body: JSON.stringify(discussion)
    })
  }
}

export default collaboration

