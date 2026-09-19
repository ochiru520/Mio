/** Qq operations. Reactive values and cross-domain callbacks are explicitly injected.
 * This module owns behavior, not application-global mutable state.
 * Dependencies are accessors so async callbacks see the latest state.
 */

export function useQq(deps) {
  function applyQqStatus(status) {
    deps.bootstrap.value = {
      ...(deps.bootstrap.value || {}),
      qq: { ...(deps.bootstrap.value?.qq || {}), ...(status || {}) },
    }
  }
  function qqStartupMessage(status) {
    if (status?.account_ready && status?.websocket_connected) return `OneBot 已连接，机器人 QQ ${status.connected_account || ''} 可以使用`
    if (status?.diagnostic_code === 'account_mismatch') return status.diagnostic_message
    if (status?.webui_reachable && status?.account_ready) return '机器人 QQ 已登录，正在等待 OneBot 连接'
    if (status?.webui_reachable) return 'NapCat WebUI 已就绪，正在准备登录二维码'
    if (status?.qq_process_running) return '机器人 QQ 已启动，正在等待 NapCat WebUI'
    if (status?.napcat_process_running) return 'NapCat 启动器已运行，正在等待机器人 QQ'
    return '启动命令已发送，正在等待 NapCat 进程'
  }
  async function waitForQqStartup({ attempts = 60, delayMs = 750 } = {}) {
    let lastStatus = null
    let lastRequestError = null
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try {
        lastStatus = await deps.request('/api/agent/qq/status')
        lastRequestError = null
        applyQqStatus(lastStatus)
        deps.qqSetupResult.value = {
          ...(deps.qqSetupResult.value || {}),
          stage: 'starting',
          message: qqStartupMessage(lastStatus),
          diagnostic: lastStatus.diagnostic_message || '',
        }
        if (lastStatus.webui_reachable || (lastStatus.websocket_connected && lastStatus.account_ready)) return lastStatus
      } catch (error) {
        lastRequestError = error
        deps.qqSetupResult.value = {
          ...(deps.qqSetupResult.value || {}),
          stage: 'starting',
          message: 'NapCat 启动状态暂时读取失败，正在重试',
          diagnostic: error.message || '',
        }
      }
      if (attempt < attempts - 1) {
        await new Promise((resolve) => window.setTimeout(resolve, delayMs))
      }
    }
    const diagnostic = lastStatus?.diagnostic_message
      || lastRequestError?.message
      || 'NapCat、机器人 QQ 和 WebUI 都没有就绪'
    throw new Error(`NapCat 启动未完成：${diagnostic}。请确认电脑已安装并登录过 NT QQ，再点击“重启NapCat”`)
  }
  async function controlQq(action) {
    deps.qqBusy.value = action
    deps.errorMessage.value = ''
    try {
      const result = await deps.request(`/api/agent/qq/${action}`, { method: 'POST', body: '{}' })
      if (['start', 'restart'].includes(action)) {
        const status = await waitForQqStartup()
        if (!status.account_ready) {
          await deps.request('/api/agent/qq/login', { method: 'POST', body: '{}' })
          await loadQqQrCode({ attempts: 30 })
        }
      }
      if (action === 'login') await loadQqQrCode({ attempts: 30 })
      if (action === 'stop') clearQqQrImage()
      await deps.loadBootstrap({ quiet: true })
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.qqBusy.value = ''
    }
  }
  async function setupQqChannel() {
    const account = deps.qqAccountDraft.value.trim()
    if (!/^\d{5,12}$/.test(account)) {
      deps.showSettingsFeedback('qq', 'error', '机器人 QQ 号必须是 5 到 12 位数字')
      return false
    }
    deps.qqBusy.value = 'setup'
    deps.qqSetupResult.value = null
    deps.errorMessage.value = ''
    try {
      let result = await deps.request('/api/agent/qq/setup', {
        method: 'POST',
        body: JSON.stringify({ account, target_user_id: deps.qqTestTargetDraft.value.trim() }),
      })
      if (result.stage === 'installing') {
        deps.showSettingsFeedback('qq', 'success', 'NapCat 正在安装，完成后会自动继续配置')
        for (let attempt = 0; attempt < 1350; attempt += 1) {
          await new Promise((resolve) => window.setTimeout(resolve, 2000))
          const progress = await deps.request('/api/dependencies/napcat/status')
          if (progress.error) throw new Error(progress.error)
          if (progress.done && !progress.installing) break
          if (attempt === 1349) throw new Error('NapCat 安装等待超过 45 分钟，请检查安装窗口')
        }
        result = await deps.request('/api/agent/qq/setup', {
          method: 'POST',
          body: JSON.stringify({ account, target_user_id: deps.qqTestTargetDraft.value.trim() }),
        })
      }
      deps.qqSetupResult.value = result
      const status = await waitForQqStartup()
      if (!status.account_ready) {
        if (!result.force_qr_login) {
          await deps.request('/api/agent/qq/login', { method: 'POST', body: '{}' })
        }
        const qrcodeLoaded = await loadQqQrCode({ attempts: 30 })
        if (!qrcodeLoaded) throw new Error(deps.qqQrError.value || 'NapCat 已启动，但登录二维码还没有准备好')
      }
      await deps.loadBootstrap({ quiet: true })
      deps.showSettingsFeedback(
        'qq',
        'success',
        status.account_ready ? `NapCat 已启动，机器人 QQ ${status.connected_account || account} 已登录` : 'NapCat 已启动并写入 OneBot，请用手机 QQ 扫码',
      )
      return true
    } catch (error) {
      deps.errorMessage.value = error.message
      deps.showSettingsFeedback('qq', 'error', error.message || 'QQ 通道配置失败')
      return false
    } finally {
      deps.qqBusy.value = ''
    }
  }
  async function testQqDelivery() {
    const account = deps.qqAccountDraft.value.trim()
    const target = deps.qqTestTargetDraft.value.trim()
    if (!/^\d{5,12}$/.test(account) || !/^\d{5,12}$/.test(target)) {
      deps.showSettingsFeedback('qq', 'error', '请填写机器人 QQ 和接收测试消息的 QQ 号')
      return false
    }
    deps.qqBusy.value = 'test-delivery'
    try {
      const result = await deps.request('/api/agent/qq/test-delivery', {
        method: 'POST',
        body: JSON.stringify({ account, target_user_id: target }),
      })
      deps.qqSetupResult.value = result
      if (!result.delivery_confirmed) throw new Error(result.diagnostic || 'NapCat 没有确认发送')
      deps.showSettingsFeedback('qq', 'success', `NapCat 已确认发送${result.message_id ? `，消息 ID ${result.message_id}` : ''}`)
      return true
    } catch (error) {
      deps.errorMessage.value = error.message
      deps.showSettingsFeedback('qq', 'error', error.message || '测试消息发送失败')
      return false
    } finally {
      deps.qqBusy.value = ''
    }
  }
  function clearQqQrImage() {
    deps.qqQrLoadVersion += 1
    if (deps.qqQrImageUrl.value) URL.revokeObjectURL(deps.qqQrImageUrl.value)
    deps.qqQrImageUrl.value = ''
    deps.qqQrLoading.value = false
    deps.qqQrError.value = ''
  }
  async function loadQqQrCode({ attempts = 16 } = {}) {
    const loadVersion = ++deps.qqQrLoadVersion
    if (deps.qqQrImageUrl.value) URL.revokeObjectURL(deps.qqQrImageUrl.value)
    deps.qqQrImageUrl.value = ''
    deps.qqQrLoading.value = true
    deps.qqQrError.value = ''
    let lastError = '二维码暂时还没准备好'

    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try {
        const response = await fetch(`/api/agent/qq/qrcode?ts=${Date.now()}`, {
          headers: { Accept: 'image/png,image/*' },
          cache: 'no-store',
        })
        if (response.ok) {
          const blob = await response.blob()
          if (!blob.size || !blob.type.startsWith('image/')) throw new Error('二维码图片数据无效')
          const imageUrl = URL.createObjectURL(blob)
          if (loadVersion !== deps.qqQrLoadVersion) {
            URL.revokeObjectURL(imageUrl)
            return false
          }
          deps.qqQrImageUrl.value = imageUrl
          deps.qqQrLoading.value = false
          return true
        }
        try {
          const payload = await response.json()
          lastError = payload.detail || lastError
        } catch {
          lastError = `二维码读取失败：HTTP ${response.status}`
        }
        if (response.status !== 404) break
      } catch (error) {
        lastError = error.message || lastError
      }
      if (attempt < attempts - 1) await new Promise((resolve) => window.setTimeout(resolve, 500))
    }

    if (loadVersion === deps.qqQrLoadVersion) {
      deps.qqQrLoading.value = false
      deps.qqQrError.value = `${lastError}，请重新获取`
    }
    return false
  }
  async function saveGroupChatSettings() {
    if (deps.qqBusy.value) return
    deps.qqBusy.value = 'group-settings'
    deps.errorMessage.value = ''
    try {
      const groupIds = deps.groupIdsDraft.value
        .replaceAll('，', ',')
        .replaceAll('；', ',')
        .split(/[;,]/)
        .map((item) => item.trim())
        .filter(Boolean)
      const result = await deps.request('/api/agent/qq/group-settings', {
        method: 'PATCH',
        body: JSON.stringify({
          enabled: deps.groupChatSettings.value.enabled,
          group_ids: [...new Set(groupIds)],
          mention_required: deps.groupChatSettings.value.mention_required,
        }),
      })
      deps.groupChatSettings.value = { ...deps.groupChatSettings.value, ...result.group_chat }
      deps.groupIdsDraft.value = (result.group_chat.group_ids || []).join(', ')
      deps.savedGroupChatSettings.value = deps.normalizedGroupChatSnapshot()
      deps.bootstrap.value = {
        ...(deps.bootstrap.value || {}),
        qq: { ...(deps.bootstrap.value?.qq || {}), group_chat: result.group_chat },
      }
      deps.showSettingsFeedback('qq', 'success', 'QQ群聊设置已保存')
      return true
    } catch (error) {
      deps.errorMessage.value = error.message
      deps.showSettingsFeedback('qq', 'error', 'QQ群聊设置保存失败')
      return false
    } finally {
      deps.qqBusy.value = ''
    }
  }
  async function clearGroupChatContext() {
    if (deps.qqBusy.value) return
    deps.qqBusy.value = 'group-context'
    deps.errorMessage.value = ''
    try {
      const result = await deps.request('/api/agent/qq/group-context/clear', { method: 'POST', body: '{}' })
      deps.groupChatSettings.value = { ...deps.groupChatSettings.value, ...result.group_chat }
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.qqBusy.value = ''
    }
  }
  return { applyQqStatus, qqStartupMessage, waitForQqStartup, controlQq, setupQqChannel, testQqDelivery, clearQqQrImage, loadQqQrCode, saveGroupChatSettings, clearGroupChatContext }
}
