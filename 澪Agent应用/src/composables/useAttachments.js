/** Attachments operations. Reactive values and cross-domain callbacks are explicitly injected.
 * This module owns behavior, not application-global mutable state.
 * Dependencies are accessors so async callbacks see the latest state.
 */

export function useAttachments(deps) {
  function readFileAsDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result || ''))
      reader.onerror = () => reject(new Error(`无法读取文件：${file.name}`))
      reader.readAsDataURL(file)
    })
  }
  function isTextAttachment(file) {
    const extension = file.name.toLowerCase().match(/\.[^.]+$/)?.[0] || ''
    return file.type.startsWith('text/') || [
      '.txt', '.md', '.markdown', '.json', '.jsonl', '.log',
      '.py', '.js', '.ts', '.vue', '.html', '.css', '.xml', '.yaml', '.yml',
    ].includes(extension)
  }
  function fileExtension(file) {
    return file.name.toLowerCase().match(/\.[^.]+$/)?.[0] || ''
  }
  function isImageAttachment(file) {
    return file.type.startsWith('image/') || [
      '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.dib', '.tif', '.tiff',
      '.avif', '.heic', '.heif', '.svg',
    ].includes(fileExtension(file))
  }
  function isDocumentAttachment(file) {
    const extension = file.name.toLowerCase().match(/\.[^.]+$/)?.[0] || ''
    return [
      '.pdf', '.docx', '.csv', '.tsv', '.xlsx',
      '.mp4', '.mkv', '.mov', '.webm', '.avi',
    ].includes(extension)
  }
  async function addSelectedFiles(selectedFiles) {
    if (!selectedFiles.length) return
    const limits = deps.attachmentLimits.value
    if (deps.attachments.value.length + selectedFiles.length > limits.maxCount) {
      deps.errorMessage.value = `每次最多添加 ${limits.maxCount} 个附件。`
      return
    }

    try {
      for (const file of selectedFiles) {
        if (isImageAttachment(file)) {
          if (file.size > limits.imageMaxBytes) throw new Error(`图片超过 ${(limits.imageMaxBytes / 1024 / 1024).toFixed(0)}MB：${file.name}`)
          const dataUrl = await readFileAsDataUrl(file)
          deps.attachments.value.push({
            id: `${Date.now()}-${crypto.randomUUID?.() || Math.random()}-${file.name}`,
            kind: 'image',
            name: file.name,
            mime_type: file.type || 'application/octet-stream',
            size: file.size,
            data_url: dataUrl,
            preview_url: dataUrl,
          })
          continue
        }
        if (isDocumentAttachment(file)) {
          if (file.size > limits.documentMaxBytes) throw new Error(`文档超过 ${(limits.documentMaxBytes / 1024 / 1024).toFixed(0)}MB：${file.name}`)
          const dataUrl = await readFileAsDataUrl(file)
          deps.attachments.value.push({
            id: `${Date.now()}-${crypto.randomUUID?.() || Math.random()}-${file.name}`,
            kind: 'document',
            name: file.name,
            mime_type: file.type || 'application/octet-stream',
            size: file.size,
            data_url: dataUrl,
          })
          continue
        }
        if (isTextAttachment(file)) {
          if (file.size > limits.textMaxBytes) throw new Error(`文本文件超过 ${(limits.textMaxBytes / 1024).toFixed(0)}KB：${file.name}`)
          const text = await file.text()
          if (text.length > limits.textMaxChars) throw new Error(`文本文件超过 ${limits.textMaxChars.toLocaleString('zh-CN')} 字：${file.name}`)
          deps.attachments.value.push({
            id: `${Date.now()}-${crypto.randomUUID?.() || Math.random()}-${file.name}`,
            kind: 'text',
            name: file.name,
            mime_type: file.type || 'text/plain',
            size: file.size,
            text,
          })
          continue
        }
        if (file.size > limits.documentMaxBytes) throw new Error(`文件超过 ${(limits.documentMaxBytes / 1024 / 1024).toFixed(0)}MB：${file.name}`)
        deps.attachments.value.push({
          id: `${Date.now()}-${crypto.randomUUID?.() || Math.random()}-${file.name}`,
          kind: 'file',
          name: file.name,
          mime_type: file.type || 'application/octet-stream',
          size: file.size,
          data_url: await readFileAsDataUrl(file),
        })
      }
      deps.errorMessage.value = ''
    } catch (error) {
      deps.errorMessage.value = error.message
    }
  }
  async function handleFileSelection(event) {
    const selectedFiles = [...(event.target.files || [])]
    event.target.value = ''
    await addSelectedFiles(selectedFiles)
  }
  async function handleComposerPaste(event) {
    const clipboardFiles = [...(event.clipboardData?.items || [])]
      .filter((item) => item.kind === 'file')
      .map((item) => item.getAsFile())
      .filter(Boolean)
    if (!clipboardFiles.length) return
    event.preventDefault()
    await addSelectedFiles(clipboardFiles)
  }
  function dragContainsFiles(event) {
    return [...(event.dataTransfer?.types || [])].includes('Files')
  }
  function handleFileDragEnter(event) {
    if (!dragContainsFiles(event)) return
    event.preventDefault()
    deps.fileDragDepth += 1
    deps.isFileDragging.value = true
  }
  function handleFileDragOver(event) {
    if (!dragContainsFiles(event)) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'copy'
  }
  function handleFileDragLeave(event) {
    if (!deps.isFileDragging.value) return
    deps.fileDragDepth = Math.max(0, deps.fileDragDepth - 1)
    if (!deps.fileDragDepth) deps.isFileDragging.value = false
  }
  async function handleFileDrop(event) {
    if (!dragContainsFiles(event)) return
    event.preventDefault()
    deps.fileDragDepth = 0
    deps.isFileDragging.value = false
    await addSelectedFiles([...(event.dataTransfer?.files || [])])
  }
  function removeAttachment(id) {
    deps.attachments.value = deps.attachments.value.filter((item) => item.id !== id)
  }
  function formatFileSize(bytes) {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  }
  function turnAttachments(turn) {
    return turn.parts.flatMap((part) => part.attachments || [])
  }
  return { readFileAsDataUrl, isTextAttachment, fileExtension, isImageAttachment, isDocumentAttachment, addSelectedFiles, handleFileSelection, handleComposerPaste, dragContainsFiles, handleFileDragEnter, handleFileDragOver, handleFileDragLeave, handleFileDrop, removeAttachment, formatFileSize, turnAttachments }
}
