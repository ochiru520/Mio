const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('mioLockControl', {
  toggle: () => ipcRenderer.send('mio-lock-control:toggle'),
  onState: (callback) => {
    const listener = (_event, state) => callback(state || {})
    ipcRenderer.on('mio-lock-control:state', listener)
    return () => ipcRenderer.removeListener('mio-lock-control:state', listener)
  },
})
