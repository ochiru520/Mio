const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('mioDesktop', {
  getState: () => ipcRenderer.invoke('mio-desktop:get-state'),
  setInteractive: (interactive) => ipcRenderer.send('mio-desktop:set-interactive', Boolean(interactive)),
  setClickThroughLocked: (enabled) => ipcRenderer.send('mio-desktop:set-click-through-locked', Boolean(enabled)),
  setLockControlAnchor: (bounds) => ipcRenderer.send('mio-desktop:set-lock-control-anchor', bounds),
  setAlwaysOnTop: (enabled) => ipcRenderer.send('mio-desktop:set-always-on-top', Boolean(enabled)),
  setChatWindowOpen: (open) => ipcRenderer.send('mio-desktop:set-chat-window-open', Boolean(open)),
  toggleChatWindow: (anchor) => ipcRenderer.invoke('mio-desktop:toggle-chat-window', anchor || {}),
  showChatWindow: (anchor) => ipcRenderer.invoke('mio-desktop:show-chat-window', anchor || {}),
  restoreVisibility: () => ipcRenderer.send('mio-desktop:restore-visibility'),
  importModel: () => ipcRenderer.invoke('mio-desktop:import-model'),
  selectModel: (modelId) => ipcRenderer.invoke('mio-desktop:select-model', String(modelId || '')),
  openAgent: () => ipcRenderer.invoke('mio-desktop:open-agent'),
  close: () => ipcRenderer.send('mio-desktop:close'),
  onModelsChanged: (callback) => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('mio-desktop:models-changed', listener)
    return () => ipcRenderer.removeListener('mio-desktop:models-changed', listener)
  },
  onClickThroughChanged: (callback) => {
    const listener = (_event, enabled) => callback(Boolean(enabled))
    ipcRenderer.on('mio-desktop:click-through-changed', listener)
    return () => ipcRenderer.removeListener('mio-desktop:click-through-changed', listener)
  },
})
