# Reactive Image (Standalone) — legacy Node version

> **Prefer the Python app:** `../fridge-reactive-image/`  
> This Node version is archived reference source only. `node_modules` was removed.

Audio-reactive PNG avatar for **XSplit / OBS**.

No Electron. Pure Node.js — opens **Edge or Chrome in app mode** so you get a real window to capture.

## Features

- Dedicated Avatar window (Window Capture in XSplit)
- Settings window with **live trigger + level meter**
- Volume tiers: idle → soft → speak → loud
- Bounce scales with loudness + live intensity
- Configurable **background color** (chroma key)
- Optional debug HUD on the avatar
- Mic device picker

## Install & run (Windows)

You only need **Node.js**. No `npm install` of heavy packages.

```bat
cd /d G:\fridge-reactive-image-legacy

:: optional: remove old broken Electron install
rmdir /s /q node_modules
del package-lock.json

npm start
```

That runs `node server.js`. Two app windows should open:

1. **Reactive Avatar** — capture this  
2. **Settings** — images, thresholds, debug

If windows don’t auto-open, visit:

- Avatar:   http://127.0.0.1:3850/avatar.html  
- Settings: http://127.0.0.1:3850/settings.html  

Tip: in Edge/Chrome you can also use menu → open as app, or just bookmark those URLs.

## XSplit

1. Start the app (`npm start`)
2. Allow microphone when the Avatar window asks
3. **Add source → Window Capture** → pick the Avatar window  
4. Optional: **Color Key** matching your background (default `#00FF00` green)

## First-time setup

1. In Settings, choose Idle + Speaking images (Soft/Loud optional)
2. Pick your mic, set speaking / loud thresholds using the live meter
3. Save
4. Hide the debug HUD on the avatar if you don’t want it in the capture

## Notes

- Keep the terminal/`npm start` running while you stream
- Config is saved to `config.json` next to `server.js`
- Images live in `uploads/`
- Port defaults to `3850` (`set PORT=3851` to change)

— Sensoka's Workshop
