# Reactive Image (Native)

Native Windows app for an audio-reactive PNG avatar (XSplit / OBS **Window Capture**).

No browser. No Electron.

## Features

- Mic-driven tiers: idle → soft → speak → loud
- **Custom states** with **hold** or **toggle** hotkeys (e.g. F20 while held, `2` to lock a face)
- **Scrollable** settings window
- **HTTP control** for Android tablet / ESP Arduino on your LAN
- **USB serial** control for classic Arduino
- Bounce, live intensity, chroma-key background, debug HUD

## Run from source

```bat
cd /d G:\fridge-reactive-image
python -m pip install -r requirements.txt
python reactive_image.py
```

## Build .exe

```bat
build.bat
```

→ `dist\ReactiveImage.exe`

## Custom states

In **Settings → Custom states**:

1. **+ Add custom state**
2. Set a name, pick an image
3. Trigger:
   - **hold** – show while the hotkey is held (e.g. `f20`)
   - **toggle** – press once to switch; press again (or another toggle) to leave
4. Hotkey examples: `f20`, `2`, `a`, `space`
5. **Save settings**

Priority: **hold > toggle > audio**.

## Tablet / Arduino control

### HTTP (Android tablet, ESP32/ESP8266)

Enabled by default on port **3851**, **this PC only** (`127.0.0.1`).
To use a tablet / ESP, tick **Allow devices on my network** in Settings → Remote control (`control.http_lan: true`, binds `0.0.0.0`).

Requests that a browser marks as coming from another web site are refused, so a random page cannot flip your avatar. Typed URLs, bookmarks, HTTP Shortcuts, Tasker and ESP boards are unaffected. Set `control.allow_cross_site: true` only if you drive it from your own web control page on another origin.

From a device on your network (with LAN access ticked):

```
http://<pc-ip>:3851/status
http://<pc-ip>:3851/hold/<state_id>
http://<pc-ip>:3851/release/<state_id>
http://<pc-ip>:3851/toggle/<state_id>
http://<pc-ip>:3851/clear
```

State **id** is shown when you create a custom state (e.g. `s_a1b2c3`).  
On Android, use a browser bookmark, **HTTP Shortcuts**, or Tasker.

### USB serial (Arduino)

Enable in Settings, pick the COM port.

Send newline-terminated commands:

```
HOLD s_a1b2c3
RELEASE s_a1b2c3
TOGGLE s_a1b2c3
CLEAR
```

## XSplit

Window Capture → **Reactive Avatar**. Optional Color Key on the green background.

— Sensoka's Workshop
