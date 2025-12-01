import { ref } from 'vue'
import { models as modelsApi, uploads as uploadsApi } from '../services/api'

// Simple SHA-256 hash function using Web Crypto API
async function calculateSHA256(file) {
  const arrayBuffer = await file.arrayBuffer()
  const hashBuffer = await crypto.subtle.digest('SHA-256', arrayBuffer)
  const hashArray = Array.from(new Uint8Array(hashBuffer))
  const hashHex = hashArray.map(b => b.toString(16).padStart(2, '0')).join('')
  return `sha256:${hashHex}`
}

export function useFileUpload() {
  const uploading = ref(false)
  const uploadProgress = ref(0)
  const uploadError = ref(null)

  const uploadFile = async (file, modelName, modelDescription, modelId = null) => {
    try {
      uploading.value = true
      uploadProgress.value = 0
      uploadError.value = null

      // Step 1: Calculate file hash
      uploadProgress.value = 5
      const fileHash = await calculateSHA256(file)
      
      // Step 2: Create model if modelId not provided
      if (!modelId) {
        uploadProgress.value = 10
        const model = await modelsApi.create({
          name: modelName,
          description: modelDescription || ''
        })
        modelId = model.id
      }

      // Step 3: Initiate upload session
      uploadProgress.value = 20
      const chunkSize = 5 * 1024 * 1024 // 5MB chunks
      const uploadInit = await uploadsApi.initiateUpload({
        filename: file.name,
        file_size: file.size,
        file_hash: fileHash,
        chunk_size: chunkSize,
        artifact_type: 'model',
        model_id: modelId
      })

      // Step 4: Upload chunks to presigned URLs
      // presigned_urls is an array of { part_number, url, expires_at }
      const totalChunks = uploadInit.presigned_urls.length
      const uploadedParts = []

      // Sort presigned URLs by part_number to ensure correct order
      const sortedUrls = [...uploadInit.presigned_urls].sort((a, b) => a.part_number - b.part_number)

      for (let i = 0; i < totalChunks; i++) {
        const presignedUrlData = sortedUrls[i]
        const partNumber = presignedUrlData.part_number
        const presignedUrl = presignedUrlData.url
        const start = (partNumber - 1) * chunkSize
        const end = Math.min(start + chunkSize, file.size)
        const chunk = file.slice(start, end)

        // Upload chunk directly to MinIO (bypasses backend)
        const response = await fetch(presignedUrl, {
          method: 'PUT',
          body: chunk,
          headers: {
            'Content-Type': 'application/octet-stream'
          }
        })

        if (!response.ok) {
          const errorText = await response.text().catch(() => response.statusText)
          throw new Error(`Failed to upload chunk ${partNumber}: ${response.status} ${errorText}`)
        }

        // Get ETag from response headers (required for multipart completion)
        const etag = response.headers.get('ETag') || response.headers.get('etag') || ''
        if (!etag) {
          throw new Error(`Missing ETag for chunk ${partNumber}`)
        }
        
        uploadedParts.push({
          part_number: partNumber,
          etag: etag.replace(/"/g, '') // Remove quotes from ETag
        })

        // Update progress
        uploadProgress.value = 20 + (70 * (i + 1) / totalChunks)
      }

      // Step 5: Complete upload
      uploadProgress.value = 90
      const result = await uploadsApi.completeUpload(uploadInit.upload_id, uploadedParts)
      
      uploadProgress.value = 100
      return {
        success: true,
        modelId: result.model_id,
        artifactId: result.artifact_id,
        version: result.version
      }
    } catch (error) {
      console.error('Upload error:', error)
      uploadError.value = error.message || 'Upload failed'
      throw error
    } finally {
      uploading.value = false
    }
  }

  return {
    uploading,
    uploadProgress,
    uploadError,
    uploadFile
  }
}

