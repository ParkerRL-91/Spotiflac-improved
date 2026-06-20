// Electron main process for Spotiflac.
//
// Responsibilities:
//   1. Spawn the Python sidecar (frozen binary in production, `python -m
//      server` in development) and wait for it to announce its port.
//   2. Create the application window pointed at the bundled renderer, handing
//      it the sidecar port.
//   3. Tear the sidecar down cleanly on quit so no orphaned process survives.
//
// The sidecar is local-only (binds 127.0.0.1) and the renderer talks to it
// over HTTP/WebSocket; the main process just supervises its lifecycle and
// provides the few native capabilities the UI needs (folder picker, open path).

const { app, BrowserWindow, ipcMain, dialog, shell, Menu } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

let mainWindow = null;
let sidecar = null;
let sidecarPort = null;
let sidecarReady = null; // Promise resolved with the port.

const isDev = process.env.SPOTIFLAC_DEV === "1" || !app.isPackaged;

function resolveSidecar() {
  // In production we ship a PyInstaller one-folder build under resources/sidecar.
  if (!isDev) {
    const base = path.join(process.resourcesPath, "sidecar");
    const candidate = path.join(base, "spotiflac-sidecar");
    if (fs.existsSync(candidate)) {
      return { command: candidate, args: [], cwd: base };
    }
  }
  // Development: run the module from the repo root using the active python.
  const repoRoot = path.join(__dirname, "..");
  const py = process.env.SPOTIFLAC_PYTHON || "python3";
  return { command: py, args: ["-m", "server"], cwd: repoRoot };
}

function startSidecar() {
  if (sidecarReady) return sidecarReady;

  const { command, args, cwd } = resolveSidecar();
  sidecarReady = new Promise((resolve, reject) => {
    const env = Object.assign({}, process.env, {
      SPOTIFLAC_HOST: "127.0.0.1",
      PYTHONUNBUFFERED: "1",
    });
    sidecar = spawn(command, args, { cwd, env });

    const timeout = setTimeout(() => {
      reject(new Error("Sidecar did not become ready in time"));
    }, 30000);

    let buffer = "";
    sidecar.stdout.on("data", (chunk) => {
      buffer += chunk.toString();
      const match = buffer.match(/SPOTIFLAC_READY (\d+)/);
      if (match) {
        clearTimeout(timeout);
        sidecarPort = parseInt(match[1], 10);
        resolve(sidecarPort);
      }
      process.stdout.write(`[sidecar] ${chunk}`);
    });
    sidecar.stderr.on("data", (chunk) => {
      process.stderr.write(`[sidecar] ${chunk}`);
    });
    sidecar.on("exit", (code) => {
      if (sidecarPort === null) {
        clearTimeout(timeout);
        reject(new Error(`Sidecar exited early with code ${code}`));
      }
      sidecar = null;
    });
  });
  return sidecarReady;
}

function stopSidecar() {
  if (sidecar) {
    sidecar.kill("SIGTERM");
    sidecar = null;
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 760,
    minWidth: 880,
    minHeight: 600,
    titleBarStyle: "hiddenInset",
    backgroundColor: "#0d0f12",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.loadFile(path.join(__dirname, "renderer", "index.html"));
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

// ---- IPC bridge -----------------------------------------------------------
ipcMain.handle("get-port", async () => {
  if (sidecarPort !== null) return sidecarPort;
  return startSidecar();
});

ipcMain.handle("choose-folder", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Choose download folder",
    properties: ["openDirectory", "createDirectory"],
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle("open-path", async (_e, target) => {
  if (target) await shell.openPath(target);
});

// ---- Lifecycle ------------------------------------------------------------
app.whenReady().then(async () => {
  try {
    await startSidecar();
  } catch (err) {
    dialog.showErrorBox(
      "Spotiflac could not start",
      `The download engine failed to launch:\n\n${err.message}`
    );
  }
  createWindow();
  buildMenu();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", stopSidecar);
app.on("will-quit", stopSidecar);

function buildMenu() {
  const template = [
    {
      label: app.name,
      submenu: [
        { role: "about" },
        { type: "separator" },
        { role: "hide" },
        { role: "quit" },
      ],
    },
    {
      label: "Edit",
      submenu: [
        { role: "cut" },
        { role: "copy" },
        { role: "paste" },
        { role: "selectAll" },
      ],
    },
    {
      label: "View",
      submenu: [
        { role: "reload" },
        { role: "toggleDevTools" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}
