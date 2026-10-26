import { reactive } from 'vue'

/** Owns the application confirmation/prompt state and Promise settlement. */
export function useAppDialog() {
  const appDialog = reactive({
    open: false,
    mode: 'confirm',
    title: '请确认',
    message: '',
    value: '',
    confirmText: '确定',
    cancelText: '取消',
    danger: false,
    multiline: false,
    resolve: null,
  })

  function settleAppDialog(result) {
    const resolve = appDialog.resolve
    appDialog.open = false
    appDialog.resolve = null
    if (resolve) resolve(result)
  }

  function showAppConfirm({ title = '请确认', message = '', confirmText = '确定', cancelText = '取消', danger = false } = {}) {
    return new Promise((resolve) => {
      Object.assign(appDialog, {
        open: true,
        mode: 'confirm',
        title,
        message,
        value: '',
        confirmText,
        cancelText,
        danger,
        multiline: false,
        resolve,
      })
    })
  }

  function showAppPrompt({ title, message = '', value = '', confirmText = '保存', multiline = false } = {}) {
    return new Promise((resolve) => {
      Object.assign(appDialog, {
        open: true,
        mode: 'prompt',
        title,
        message,
        value,
        confirmText,
        cancelText: '取消',
        danger: false,
        multiline,
        resolve,
      })
    })
  }

  function confirmAppDialog() {
    settleAppDialog(appDialog.mode === 'prompt' ? appDialog.value : true)
  }

  function cancelAppDialog() {
    settleAppDialog(appDialog.mode === 'prompt' ? null : false)
  }

  return { appDialog, showAppConfirm, showAppPrompt, confirmAppDialog, cancelAppDialog }
}
