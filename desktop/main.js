/**
 * Transparent desktop shell — fullscreen on ONE monitor at a time.
 * Hub button cycles Screen 1 ↔ Screen 2 (…N).
 *
 * Always-on-top is locked (screen-saver level).
 * Ctrl+Shift+Q quits · Ctrl+Shift+D DevTools
 */

const { app, BrowserWindow, globalShortcut, ipcMain, screen } = require('electron');
const path = require('path');

const VITE_URL = 'http://127.0.0.1:5173';
const CHARACTER = process.argv.slice(2).find((a) => !a.startsWith('-')) || '';

let mainWindow = null;
let clickThrough = false;
let pinTimer = null;
/** Index into screen.getAllDisplays() */
let displayIndex = 0;

function listDisplays() {
  return screen.getAllDisplays();
}

function getActiveDisplay() {
  const displays = listDisplays();
  if (!displays.length) return screen.getPrimaryDisplay();
  displayIndex = ((displayIndex % displays.length) + displays.length) % displays.length;
  return displays[displayIndex];
}

function applyPinnedTop(win = mainWindow) {
  if (!win || win.isDestroyed()) return;
  win.setAlwaysOnTop(true, 'screen-saver');
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
}

function applyDisplayBounds(win = mainWindow) {
  if (!win || win.isDestroyed()) return;
  const displays = listDisplays();
  if (!displays.length) return;
  if (displayIndex >= displays.length) displayIndex = 0;
  const d = displays[displayIndex];
  const bounds = d.bounds;
  console.log(
    `[shell] monitor ${displayIndex + 1}/${displays.length} ` +
    `${bounds.width}x${bounds.height} @${bounds.x},${bounds.y}`,
  );
  win.setBounds(bounds);
  win.setResizable(false);
  applyPinnedTop(win);
}

function displayInfo() {
  const displays = listDisplays();
  const d = getActiveDisplay();
  return {
    index: displayIndex,
    label: displayIndex + 1,
    displayCount: displays.length,
    displayId: d.id,
    bounds: d.bounds,
  };
}

function setDisplay(index) {
  const displays = listDisplays();
  if (!displays.length) return displayInfo();
  displayIndex = ((Number(index) % displays.length) + displays.length) % displays.length;
  applyDisplayBounds(mainWindow);
  const info = displayInfo();
  mainWindow?.webContents.send('overlay:display-changed', info);
  return info;
}

function cycleDisplay() {
  const displays = listDisplays();
  if (displays.length < 2) return setDisplay(displayIndex);
  return setDisplay(displayIndex + 1);
}

function getLayout() {
  const d = getActiveDisplay();
  const b = d.bounds;
  return {
    window: b,
    primary: screen.getPrimaryDisplay().bounds,
    // Single-monitor window → avatar center is the window center.
    primaryCenterX: 0.5,
    primaryCenterY: 0.5,
    display: displayInfo(),
  };
}

function createWindow() {
  // Start on the primary monitor.
  const primaryId = screen.getPrimaryDisplay().id;
  const displays = listDisplays();
  displayIndex = Math.max(0, displays.findIndex((d) => d.id === primaryId));

  const target =
    `${VITE_URL}/?transparent=1` + (CHARACTER ? `&character=${CHARACTER}` : '');
  console.log(`[shell] alwaysOnTop=locked character=${CHARACTER || '<server>'}`);
  console.log(`[shell] -> ${target}`);

  mainWindow = new BrowserWindow({
    ...getActiveDisplay().bounds,
    title: 'Luna Desktop',
    show: false,
    transparent: true,
    frame: false,
    hasShadow: false,
    backgroundColor: '#00000000',
    alwaysOnTop: true,
    skipTaskbar: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  mainWindow.loadURL(target);
  mainWindow.once('ready-to-show', () => {
    applyDisplayBounds(mainWindow);
    mainWindow.setBackgroundColor('#00000000');
    mainWindow.show();
    applyPinnedTop(mainWindow);
  });
  mainWindow.on('blur', () => applyPinnedTop(mainWindow));
  mainWindow.on('focus', () => applyPinnedTop(mainWindow));
  mainWindow.webContents.on('did-finish-load', () => {
    console.log('[shell] loaded');
    applyPinnedTop(mainWindow);
    mainWindow.webContents.send('overlay:display-changed', displayInfo());
  });
  mainWindow.webContents.on('did-fail-load', (_e, code, desc) =>
    console.error(`[shell] load failed (${code} ${desc}) — is Vite running on :5173?`),
  );
  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  return mainWindow;
}

function setClickThrough(enabled) {
  clickThrough = Boolean(enabled);
  if (!mainWindow) return;
  if (clickThrough) {
    mainWindow.setIgnoreMouseEvents(true, { forward: true });
  } else {
    mainWindow.setIgnoreMouseEvents(false);
  }
  applyPinnedTop(mainWindow);
}

app.whenReady().then(() => {
  createWindow();

  pinTimer = setInterval(() => applyPinnedTop(mainWindow), 2000);

  screen.on('display-added', () => mainWindow && applyDisplayBounds(mainWindow));
  screen.on('display-removed', () => {
    const displays = listDisplays();
    if (displayIndex >= displays.length) displayIndex = 0;
    if (mainWindow) applyDisplayBounds(mainWindow);
  });
  screen.on('display-metrics-changed', () => {
    if (mainWindow) applyDisplayBounds(mainWindow);
  });

  ipcMain.handle('window:set-always-on-top', () => {
    applyPinnedTop(mainWindow);
    return true;
  });
  ipcMain.handle('window:get-always-on-top', () => true);
  ipcMain.handle('window:get-layout', () => getLayout());
  ipcMain.handle('window:get-display', () => displayInfo());
  ipcMain.handle('window:cycle-display', () => cycleDisplay());
  ipcMain.handle('window:set-display', (_e, index) => setDisplay(index));

  ipcMain.handle('window:set-click-through', (_e, enabled) => {
    setClickThrough(enabled);
    return clickThrough;
  });
  ipcMain.handle('window:get-click-through', () => clickThrough);

  ipcMain.on('window:set-ignore-mouse', (_e, ignore) => {
    if (!mainWindow || !clickThrough) return;
    if (ignore) mainWindow.setIgnoreMouseEvents(true, { forward: true });
    else mainWindow.setIgnoreMouseEvents(false);
  });

  ipcMain.handle('window:minimize', () => {
    mainWindow?.minimize();
  });
  ipcMain.handle('window:close', () => {
    app.quit();
  });

  globalShortcut.register('CommandOrControl+Shift+Q', () => app.quit());
  globalShortcut.register('CommandOrControl+Shift+D', () => {
    mainWindow?.webContents.toggleDevTools();
  });
  console.log('[shell] Ctrl+Shift+Q quit · hub monitor button cycles screens');
});

app.on('will-quit', () => {
  if (pinTimer) clearInterval(pinTimer);
  globalShortcut.unregisterAll();
});

app.on('window-all-closed', () => app.quit());
