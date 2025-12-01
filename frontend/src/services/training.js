// Training Service API (for future integration)
// This service will handle model training orchestration and job management

// Import shared API utilities
import { apiRequest } from './api.js'

export const training = {
  // Training Jobs API (to be implemented)
  async listJobs() {
    return apiRequest('/api/training/jobs')
  },
  
  async getJob(jobId) {
    return apiRequest(`/api/training/jobs/${jobId}`)
  },
  
  async createJob(jobConfig) {
    return apiRequest('/api/training/jobs', {
      method: 'POST',
      body: JSON.stringify(jobConfig)
    })
  },
  
  async cancelJob(jobId) {
    return apiRequest(`/api/training/jobs/${jobId}/cancel`, {
      method: 'POST'
    })
  },
  
  async getJobLogs(jobId) {
    return apiRequest(`/api/training/jobs/${jobId}/logs`)
  },
  
  async getJobStatus(jobId) {
    return apiRequest(`/api/training/jobs/${jobId}/status`)
  },
  
  // Checkpoints API (to be implemented)
  async listCheckpoints(jobId) {
    return apiRequest(`/api/training/jobs/${jobId}/checkpoints`)
  },
  
  async getCheckpoint(checkpointId) {
    return apiRequest(`/api/training/checkpoints/${checkpointId}`)
  }
}

export default training

