const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('lunaDesktop', {
  setAlwaysOnTop: (enabled) => ipcRenderer.invoke('window:set-always-on-top', enabled),
  getAlwaysOnTop: () => ipcRenderer.invoke('window:get-always-on-top'),
  getLayout: () => ipcRenderer.invoke('window:get-layout'),
  getDisplay: () => ipcRenderer.invoke('window:get-display'),
  cycleDisplay: () => ipcRenderer.invoke('window:cycle-display'),
  setDisplay: (index) => ipcRenderer.invoke('window:set-display', index),
  onDisplayChanged: (cb) => {
    const handler = (_e, info) => cb(info);
    ipcRenderer.on('overlay:display-changed', handler);
    return () => ipcRenderer.removeListener('overlay:display-changed', handler);
  },
  setClickThrough: (enabled) => ipcRenderer.invoke('window:set-click-through', enabled),
  getClickThrough: () => ipcRenderer.invoke('window:get-click-through'),
  setIgnoreMouse: (ignore) => ipcRenderer.send('window:set-ignore-mouse', ignore),
  minimize: () => ipcRenderer.invoke('window:minimize'),
  close: () => ipcRenderer.invoke('window:close'),
});
