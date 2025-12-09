import { ref } from 'vue'
import { models as modelsApi, uploads as uploadsApi } from '../services/api'

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
  const currentUploadId = ref(null)
  const abortController = ref(null)

  const abortUpload = async () => {
    if (currentUploadId.value) {
      try {
        await uploadsApi.abortUpload(currentUploadId.value)
      } catch (error) {
        console.error('Failed to abort upload:', error)
      }
    }
    
    if (abortController.value) {
      abortController.value.abort()
      abortController.value = null
    }
    
    uploading.value = false
    uploadProgress.value = 0
    currentUploadId.value = null
    uploadError.value = 'Upload cancelled'
  }

  const uploadFile = async (file, modelName, modelDescription, modelId = null) => {
    try {
      uploading.value = true
      uploadProgress.value = 0
      uploadError.value = null
      abortController.value = new AbortController()

      uploadProgress.value = 5
      const fileHash = await calculateSHA256(file)
      
      if (!modelId) {
        uploadProgress.value = 10
        const model = await modelsApi.create({
          name: modelName,
          description: modelDescription || ''
        })
        modelId = model.id
      }

      uploadProgress.value = 20
      const chunkSize = 5 * 1024 * 1024 
      const uploadInit = await uploadsApi.initiateUpload({
        filename: file.name,
        file_size: file.size,
        file_hash: fileHash,
        chunk_size: chunkSize,
        artifact_type: 'model',
        model_id: modelId
      })

      currentUploadId.value = uploadInit.upload_id

      const totalChunks = uploadInit.presigned_urls.length
      const uploadedParts = []

      const sortedUrls = [...uploadInit.presigned_urls].sort((a, b) => a.part_number - b.part_number)

      for (let i = 0; i < totalChunks; i++) {
        if (abortController.value?.signal.aborted) {
          throw new Error('Upload cancelled')
        }

        const presignedUrlData = sortedUrls[i]
        const partNumber = presignedUrlData.part_number
        const presignedUrl = presignedUrlData.url
        
        const start = (partNumber - 1) * chunkSize
        const end = Math.min(start + chunkSize, file.size)
        const chunk = file.slice(start, end)

        const response = await fetch(presignedUrl, {
          method: 'PUT',
          body: chunk,
          headers: {
            'Content-Type': 'application/octet-stream'
          },
          signal: abortController.value?.signal
        })

        if (!response.ok) {
          const errorText = await response.text().catch(() => response.statusText)
          throw new Error(`Failed to upload chunk ${partNumber}: ${response.status} ${errorText}`)
        }

        const etag = response.headers.get('ETag') || response.headers.get('etag') || ''
        if (!etag) {
          throw new Error(`Missing ETag for chunk ${partNumber}`)
        }
        
        uploadedParts.push({
          part_number: partNumber,
          etag: etag.replace(/"/g, '') 
        })

        uploadProgress.value = 20 + (70 * (i + 1) / totalChunks)
      }

      if (abortController.value?.signal.aborted) {
        throw new Error('Upload cancelled')
      }

      uploadProgress.value = 90
      const result = await uploadsApi.completeUpload(uploadInit.upload_id, uploadedParts)
      
      uploadProgress.value = 100
      currentUploadId.value = null
      abortController.value = null
      
      return {
        success: true,
        modelId: result.model_id,
        artifactId: result.artifact_id,
        version: result.version
      }
    } catch (error) {
      console.error('Upload error:', error)
      uploadError.value = error.message || 'Upload failed'
      
      if (currentUploadId.value && !abortController.value?.signal.aborted) {
        try {
          await uploadsApi.abortUpload(currentUploadId.value)
        } catch (abortError) {
          console.error('Failed to abort upload on backend:', abortError)
        }
      }
      
      currentUploadId.value = null
      abortController.value = null
      throw error
    } finally {
      uploading.value = false
    }
  }

  return {
    uploading,
    uploadProgress,
    uploadError,
    uploadFile,
    abortUpload
  }
}
