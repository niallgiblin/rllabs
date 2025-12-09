// This service handles comments, discussions, and collaboration features

import { apiRequest } from './api.js'

export const collaboration = {
  async getComments(modelId) {
    const result = await apiRequest(`/api/models/${modelId}/comments`)
    return result || []
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
  
  
}

export default collaboration
