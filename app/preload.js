// Preload: exposes a minimal, safe bridge to the renderer.
// No Node APIs leak into the page; only these explicit calls are available.

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("spotiflac", {
  getPort: () => ipcRenderer.invoke("get-port"),
  chooseFolder: () => ipcRenderer.invoke("choose-folder"),
  openPath: (target) => ipcRenderer.invoke("open-path", target),
});
