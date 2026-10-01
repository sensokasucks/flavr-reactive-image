# Checklist — Reactive Image

Home: `fridge-reactive-image/` (`reactive_image.py`). Legacy Node app: `fridge-reactive-image-legacy/` (reference only).

## Must keep

- [ ] Native Python app (tkinter + sounddevice + Pillow + pynput + optional pyserial). Not Electron.
- [ ] Mic tiers: idle → soft → speak → loud.
- [ ] Custom states: hold **or** toggle hotkeys (pynput). Priority **hold > toggle > audio**.
- [ ] Settings window is scrollable.
- [ ] HTTP control on **3851** for tablet / ESP. Default bind `127.0.0.1`; LAN only via `control.http_lan` (Settings checkbox). Cross-site browser requests refused unless `control.allow_cross_site`.
- [ ] Optional USB serial for Arduino.
- [ ] Bounce, live intensity, chroma-key background, debug HUD.
- [ ] Capture path is **Window Capture**, not a Webpage source.
- [ ] `build.bat` still produces `ReactiveImage.exe`.
- [ ] `start.bat` still runs from source.
- [ ] Legacy Node folder stays archived. Do not mix its renderer into the native app “to add a browser preview”.

## Drop risks

- Binding HTTP to `0.0.0.0` in the default config without saying so.
- Dropping serial when touching the HTTP control path.

## After-change verify

- [ ] README still lists custom states, HTTP :3851, serial, and `build.bat`.
- [ ] Root README / Sources still mention port 3851.
