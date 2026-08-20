const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('mioPetChat', {
  isElectron: true,
  hide: () => ipcRenderer.send('mio-pet-chat:hide'),
})
