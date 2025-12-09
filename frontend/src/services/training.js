// This service handles model training orchestration and job management

import { apiRequest } from './api.js'

export const training = {
  async listJobs() {
    const result = await apiRequest('/api/training-jobs')
    return result || []
  },
  
  async getJob(jobId) {
    return apiRequest(`/api/training-jobs/${jobId}`)
  },
  
  async createJob(jobConfig) {
    return apiRequest('/api/training-jobs', {
      method: 'POST',
      body: JSON.stringify(jobConfig)
    })
  },
  
  async cancelJob(jobId) {
    return apiRequest(`/api/training-jobs/${jobId}/cancel`, {
      method: 'POST'
    })
  },
  
  async getJobLogs(jobId) {
    return apiRequest(`/api/training-jobs/${jobId}/logs`)
  },
  
  async getJobStatus(jobId) {
    return apiRequest(`/api/training-jobs/${jobId}/status`)
  },
  
  async listCheckpoints(jobId) {
    const result = await apiRequest(`/api/training-jobs/${jobId}/checkpoints`)
    return result || [] 
  },
  
  async getCheckpoint(checkpointId) {
    return apiRequest(`/api/training/checkpoints/${checkpointId}`)
  }
}

export default training

