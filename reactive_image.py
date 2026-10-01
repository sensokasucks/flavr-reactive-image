#!/usr/bin/env python3
"""
Reactive Image – native Windows app for XSplit / OBS Window Capture.
Audio-reactive PNG avatar + custom states (hotkeys) + tablet/Arduino control.
"""

from __future__ import annotations

import json
import random
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import numpy as np
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

try:
    import sounddevice as sd
except ImportError:
    sd = None

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

try:
    from pynput import keyboard as pynput_kb
except ImportError:
    pynput_kb = None

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None


# ---------------------------------------------------------------------------
# Paths / config
# ---------------------------------------------------------------------------

def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


DATA_DIR = app_dir()
CONFIG_PATH = DATA_DIR / "config.json"
UPLOADS_DIR = DATA_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG: dict[str, Any] = {
    "images": {
        "idle": None,
        "speakingSoft": None,
        "speaking": None,
        "speakingLoud": None,
        "idleBlink": None,
        "speakingBlink": None,
        "muted": None,
    },
    "custom_states": [
        # Example (disabled by default – user adds real ones in Settings):
        # {"id": "wave", "name": "Wave", "image": None, "trigger": "hold", "hotkey": "f20", "enabled": False},
    ],
    "audio": {
        "device": None,
        "threshold": 18.0,
        "loud_threshold": 45.0,
        "soft_ratio": 0.45,
        "smoothing": 0.55,
        "sensitivity": 1.0,
    },
    "effects": {
        "bounce": True,
        "bounce_strength": 12,
        "bounce_strength_max": 28,
        "bounce_scale_with_volume": True,
        "live_intensity": True,
        "live_intensity_max": 8,
        "dim_idle": True,
        "dim_opacity": 0.72,
        "blink_enabled": True,
        "blink_min_s": 2.5,
        "blink_max_s": 6.5,
        "blink_duration_ms": 160,
    },
    "display": {
        "max_width": 480,
        "max_height": 480,
        "background_color": "#00FF00",
        "show_debug_hud": True,
        "always_on_top": True,
        "window_width": 520,
        "window_height": 560,
    },
    "control": {
        "http_enabled": True,
        "http_port": 3851,
        # Off = this PC only (127.0.0.1). On = tablets / ESP on your LAN (0.0.0.0).
        "http_lan": False,
        # Web pages on other sites may not drive the avatar unless you allow it.
        "allow_cross_site": False,
        "serial_enabled": False,
        "serial_port": "",
        "serial_baud": 115200,
    },
    "force_muted": False,
}


def deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            cfg = deep_merge(DEFAULT_CONFIG, raw)
            if not isinstance(cfg.get("custom_states"), list):
                cfg["custom_states"] = []
            return cfg
    except Exception as e:
        print("config load failed:", e)
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# ---------------------------------------------------------------------------
# Audio engine
# ---------------------------------------------------------------------------

class AudioEngine:
    def __init__(self):
        self.volume = 0.0
        self._lock = threading.Lock()
        self._stream = None
        self._running = False
        self.smoothing = 0.55
        self.sensitivity = 1.0
        self._smooth = 0.0

    def list_devices(self):
        if sd is None:
            return []
        out = []
        try:
            for i, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] > 0:
                    out.append({"index": i, "name": d["name"]})
        except Exception as e:
            print("device list failed:", e)
        return out

    def start(self, device=None, smoothing=0.55, sensitivity=1.0) -> bool:
        self.stop()
        if sd is None:
            return False
        self.smoothing = float(smoothing)
        self.sensitivity = float(sensitivity)
        self._smooth = 0.0
        self._running = True
        try:
            self._stream = sd.InputStream(
                device=device,
                channels=1,
                samplerate=44100,
                blocksize=1024,
                callback=self._callback,
            )
            self._stream.start()
            return True
        except Exception as e:
            print("audio start failed:", e)
            self._running = False
            self._stream = None
            return False

    def stop(self):
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _callback(self, indata, frames, time_info, status):
        if not self._running:
            return
        data = indata[:, 0] if indata.ndim > 1 else indata
        rms = float(np.sqrt(np.mean(np.square(data.astype(np.float64)))))
        vol = min(100.0, rms * 500.0 * self.sensitivity)
        s = self.smoothing
        self._smooth = self._smooth * s + vol * (1.0 - s)
        with self._lock:
            self.volume = self._smooth

    def get_volume(self) -> float:
        with self._lock:
            return self.volume


# ---------------------------------------------------------------------------
# Image helpers + audio tier resolve
# ---------------------------------------------------------------------------

def load_pil(path: Optional[str]) -> Optional["Image.Image"]:
    if not path or Image is None:
        return None
    p = Path(path)
    if not p.is_file():
        p2 = UPLOADS_DIR / path
        if p2.is_file():
            p = p2
        else:
            return None
    try:
        img = Image.open(p)
        if img.mode not in ("RGBA", "RGB"):
            img = img.convert("RGBA")
        return img
    except Exception as e:
        print("image load failed:", path, e)
        return None


def fit_image(img: "Image.Image", max_w: int, max_h: int) -> "Image.Image":
    w, h = img.size
    if w <= 0 or h <= 0:
        return img
    scale = min(max_w / w, max_h / h, 1.0)
    if scale >= 0.999:
        return img
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    return img.resize((nw, nh), Image.Resampling.LANCZOS)


SPEAKING_KEYS = {"speaking", "speakingSoft", "speakingLoud", "speakingBlink"}


def resolve_audio_key(cfg: dict, volume: float, blinking: bool) -> Optional[str]:
    images = cfg.get("images") or {}
    audio = cfg.get("audio") or {}
    t = float(audio.get("threshold", 18))
    loud = max(t + 1.0, float(audio.get("loud_threshold", 45)))
    soft_ratio = float(audio.get("soft_ratio", 0.45))

    def has(slot: str) -> bool:
        return bool(images.get(slot))

    if cfg.get("force_muted") and has("muted"):
        return "muted"
    if volume < t:
        if blinking and has("idleBlink"):
            return "idleBlink"
        return images.get("idle") and "idle" or (images.get("speaking") and "speaking") or None
    if volume >= loud and has("speakingLoud"):
        return "speakingLoud"
    if has("speakingSoft"):
        soft_end = t + (loud - t) * soft_ratio
        if volume < soft_end:
            return "speakingSoft"
    if blinking and has("speakingBlink"):
        return "speakingBlink"
    for slot in ("speaking", "speakingSoft", "speakingLoud", "idle"):
        if has(slot):
            return slot
    return None


def tier_name(cfg: dict, volume: float, key: Optional[str]) -> str:
    if key and key.startswith("custom:"):
        return key.split(":", 1)[-1]
    if key == "muted":
        return "muted"
    if key in ("idleBlink", "speakingBlink"):
        return "blink"
    audio = cfg.get("audio") or {}
    t = float(audio.get("threshold", 18))
    loud = max(t + 1.0, float(audio.get("loud_threshold", 45)))
    if volume < t:
        return "idle"
    if volume >= loud and (cfg.get("images") or {}).get("speakingLoud"):
        return "loud"
    if (cfg.get("images") or {}).get("speakingSoft"):
        soft_end = t + (loud - t) * float(audio.get("soft_ratio", 0.45))
        if volume < soft_end:
            return "soft"
    return "speak"


def volume_intensity(cfg: dict, volume: float) -> float:
    audio = cfg.get("audio") or {}
    t = float(audio.get("threshold", 18))
    loud = max(t + 1.0, float(audio.get("loud_threshold", 45)))
    if volume <= t:
        return 0.0
    return min(1.0, (volume - t) / (loud - t))


# ---------------------------------------------------------------------------
# Override / custom state controller (hotkeys + network + serial)
# ---------------------------------------------------------------------------

class StateController:
    """
    Priority (highest first):
      1) active holds (hotkey or remote)
      2) active toggle (hotkey or remote)
      3) audio-reactive
    """

    def __init__(self, app: "App"):
        self.app = app
        self._lock = threading.Lock()
        self.held: list[str] = []          # state ids, last pressed on top
        self.toggle_id: Optional[str] = None
        self._listener = None
        self._http = None
        self._http_thread = None
        self._serial = None
        self._serial_thread = None
        self._serial_stop = threading.Event()
        self._pressed_keys: set = set()

    def custom_by_id(self, sid: str) -> Optional[dict]:
        for s in self.app.cfg.get("custom_states") or []:
            if s.get("id") == sid and s.get("enabled", True):
                return s
        return None

    def active_override(self) -> Optional[str]:
        """Return display key 'custom:<id>' or None."""
        with self._lock:
            if self.held:
                sid = self.held[-1]
                st = self.custom_by_id(sid)
                if st and st.get("image"):
                    return f"custom:{sid}"
            if self.toggle_id:
                st = self.custom_by_id(self.toggle_id)
                if st and st.get("image"):
                    return f"custom:{self.toggle_id}"
        return None

    def status_text(self) -> str:
        with self._lock:
            parts = []
            if self.held:
                parts.append("hold=" + ",".join(self.held))
            if self.toggle_id:
                parts.append("toggle=" + self.toggle_id)
            return " ".join(parts) if parts else "none"

    # --- actions ---
    def hold_press(self, sid: str):
        with self._lock:
            if sid in self.held:
                self.held.remove(sid)
            self.held.append(sid)

    def hold_release(self, sid: str):
        with self._lock:
            if sid in self.held:
                self.held.remove(sid)

    def toggle(self, sid: str):
        with self._lock:
            if self.toggle_id == sid:
                self.toggle_id = None
            else:
                self.toggle_id = sid

    def clear_all(self):
        with self._lock:
            self.held.clear()
            self.toggle_id = None

    def apply_command(self, line: str):
        """Parse serial/HTTP style commands."""
        line = (line or "").strip()
        if not line:
            return
        parts = line.split()
        cmd = parts[0].upper()
        arg = parts[1] if len(parts) > 1 else ""
        if cmd in ("HOLD", "PRESS") and arg:
            self.hold_press(arg)
        elif cmd in ("RELEASE", "UP") and arg:
            self.hold_release(arg)
        elif cmd in ("TOGGLE", "SET") and arg:
            self.toggle(arg)
        elif cmd in ("CLEAR", "OFF", "RESET"):
            self.clear_all()
        elif cmd == "ON" and arg:
            # force toggle on
            with self._lock:
                self.toggle_id = arg
        elif cmd == "STATE" and arg:
            with self._lock:
                self.toggle_id = arg

    # --- hotkeys ---
    def _parse_hotkey(self, name: str):
        if pynput_kb is None or not name:
            return None
        n = name.strip().lower()
        special = {
            "f1": pynput_kb.Key.f1, "f2": pynput_kb.Key.f2, "f3": pynput_kb.Key.f3,
            "f4": pynput_kb.Key.f4, "f5": pynput_kb.Key.f5, "f6": pynput_kb.Key.f6,
            "f7": pynput_kb.Key.f7, "f8": pynput_kb.Key.f8, "f9": pynput_kb.Key.f9,
            "f10": pynput_kb.Key.f10, "f11": pynput_kb.Key.f11, "f12": pynput_kb.Key.f12,
            "f13": pynput_kb.Key.f13, "f14": pynput_kb.Key.f14, "f15": pynput_kb.Key.f15,
            "f16": pynput_kb.Key.f16, "f17": pynput_kb.Key.f17, "f18": pynput_kb.Key.f18,
            "f19": pynput_kb.Key.f19, "f20": pynput_kb.Key.f20,
            "space": pynput_kb.Key.space, "enter": pynput_kb.Key.enter,
            "tab": pynput_kb.Key.tab, "esc": pynput_kb.Key.esc,
            "shift": pynput_kb.Key.shift, "ctrl": pynput_kb.Key.ctrl, "alt": pynput_kb.Key.alt,
        }
        if n in special:
            return special[n]
        if len(n) == 1:
            return pynput_kb.KeyCode.from_char(n)
        return None

    def _key_matches(self, key, target) -> bool:
        if target is None:
            return False
        if key == target:
            return True
        # char keys sometimes come as KeyCode
        try:
            if hasattr(key, "char") and hasattr(target, "char") and key.char and target.char:
                return key.char.lower() == target.char.lower()
        except Exception:
            pass
        return False

    def restart_hotkeys(self):
        self.stop_hotkeys()
        if pynput_kb is None:
            return
        states = [s for s in (self.app.cfg.get("custom_states") or []) if s.get("enabled", True) and s.get("hotkey")]
        if not states:
            return

        # map key object -> list of (state_id, trigger)
        bindings = []
        for s in states:
            k = self._parse_hotkey(str(s.get("hotkey") or ""))
            if k is not None:
                bindings.append((k, s["id"], str(s.get("trigger") or "toggle").lower()))

        if not bindings:
            return

        def on_press(key):
            for target, sid, trigger in bindings:
                if self._key_matches(key, target):
                    kid = (sid, trigger)
                    if kid in self._pressed_keys:
                        return
                    self._pressed_keys.add(kid)
                    if trigger == "hold":
                        self.hold_press(sid)
                    else:
                        self.toggle(sid)
                    return

        def on_release(key):
            for target, sid, trigger in bindings:
                if self._key_matches(key, target):
                    kid = (sid, trigger)
                    self._pressed_keys.discard(kid)
                    if trigger == "hold":
                        self.hold_release(sid)
                    return

        self._listener = pynput_kb.Listener(on_press=on_press, on_release=on_release)
        self._listener.daemon = True
        self._listener.start()

    def stop_hotkeys(self):
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        self._pressed_keys.clear()

    # --- HTTP control (Android tablet / ESP) ---
    def restart_http(self):
        self.stop_http()
        ctrl = self.app.cfg.get("control") or {}
        if not ctrl.get("http_enabled", True):
            return
        port = int(ctrl.get("http_port", 3851))
        bind = "0.0.0.0" if ctrl.get("http_lan", False) else "127.0.0.1"
        allow_cross_site = bool(ctrl.get("allow_cross_site", False))
        controller = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def _send(self, code=200, body="ok", ctype="text/plain"):
                data = body.encode("utf-8") if isinstance(body, str) else body
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                if allow_cross_site:
                    self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                # Browsers tag requests made by other web sites; refuse those so a
                # random page (even an <img> tag) cannot flip the avatar.
                site = (self.headers.get("Sec-Fetch-Site") or "").lower()
                if not allow_cross_site and site in ("cross-site", "same-site"):
                    self._send(403, "cross-site requests are off (control.allow_cross_site)")
                    return
                u = urlparse(self.path)
                path = u.path.strip("/")
                qs = parse_qs(u.query)
                parts = path.split("/") if path else []

                if not parts or parts[0] in ("", "status"):
                    st = {
                        "override": controller.active_override(),
                        "held": list(controller.held),
                        "toggle": controller.toggle_id,
                        "states": [
                            {"id": s.get("id"), "name": s.get("name"), "trigger": s.get("trigger")}
                            for s in (controller.app.cfg.get("custom_states") or [])
                            if s.get("enabled", True)
                        ],
                    }
                    self._send(200, json.dumps(st), "application/json")
                    return

                cmd = parts[0].lower()
                arg = parts[1] if len(parts) > 1 else (qs.get("id") or qs.get("state") or [""])[0]

                if cmd in ("hold", "press") and arg:
                    controller.hold_press(arg)
                    self._send(200, f"hold {arg}")
                elif cmd in ("release", "up") and arg:
                    controller.hold_release(arg)
                    self._send(200, f"release {arg}")
                elif cmd in ("toggle", "set") and arg:
                    controller.toggle(arg)
                    self._send(200, f"toggle {arg}")
                elif cmd in ("on", "state") and arg:
                    with controller._lock:
                        controller.toggle_id = arg
                    self._send(200, f"on {arg}")
                elif cmd in ("clear", "off", "reset"):
                    controller.clear_all()
                    self._send(200, "cleared")
                elif cmd == "help":
                    self._send(
                        200,
                        "GET /status\n"
                        "GET /hold/<id>\nGET /release/<id>\n"
                        "GET /toggle/<id>\nGET /on/<id>\nGET /clear\n",
                    )
                else:
                    self._send(404, "unknown – try /help")

        try:
            self._http = HTTPServer((bind, port), Handler)
            self._http_thread = threading.Thread(target=self._http.serve_forever, daemon=True)
            self._http_thread.start()
            where = "LAN (tablet/ESP)" if bind == "0.0.0.0" else "this PC only"
            print(f"[control] HTTP listening on http://{bind}:{port}/  ({where})")
        except Exception as e:
            print("[control] HTTP failed:", e)
            self._http = None

    def stop_http(self):
        if self._http is not None:
            try:
                self._http.shutdown()
            except Exception:
                pass
            self._http = None

    # --- Serial (USB Arduino) ---
    def restart_serial(self):
        self.stop_serial()
        if serial is None:
            return
        ctrl = self.app.cfg.get("control") or {}
        if not ctrl.get("serial_enabled"):
            return
        port = (ctrl.get("serial_port") or "").strip()
        baud = int(ctrl.get("serial_baud") or 115200)
        if not port:
            return
        self._serial_stop.clear()

        def loop():
            try:
                ser = serial.Serial(port, baud, timeout=0.2)
                self._serial = ser
                print(f"[control] Serial open {port} @ {baud}")
                buf = ""
                while not self._serial_stop.is_set():
                    try:
                        raw = ser.read(256)
                        if raw:
                            buf += raw.decode("utf-8", errors="ignore")
                            while "\n" in buf:
                                line, buf = buf.split("\n", 1)
                                self.apply_command(line.strip())
                    except Exception:
                        time.sleep(0.1)
                ser.close()
            except Exception as e:
                print("[control] Serial failed:", e)
            self._serial = None

        self._serial_thread = threading.Thread(target=loop, daemon=True)
        self._serial_thread.start()

    def stop_serial(self):
        self._serial_stop.set()
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    def restart_all(self):
        self.restart_hotkeys()
        self.restart_http()
        self.restart_serial()

    def stop_all(self):
        self.stop_hotkeys()
        self.stop_http()
        self.stop_serial()


# ---------------------------------------------------------------------------
# Avatar window
# ---------------------------------------------------------------------------

class AvatarWindow:
    def __init__(self, root: tk.Tk, app: "App"):
        self.app = app
        self.cfg = app.cfg
        disp = self.cfg["display"]

        self.win = tk.Toplevel(root)
        self.win.title("Reactive Avatar")
        self.win.geometry(f"{disp.get('window_width', 520)}x{disp.get('window_height', 560)}")
        self.win.configure(bg=disp.get("background_color", "#00FF00"))
        self.win.protocol("WM_DELETE_WINDOW", self.on_close)
        if disp.get("always_on_top", True):
            self.win.attributes("-topmost", True)

        self.canvas = tk.Canvas(self.win, highlightthickness=0, bg=disp.get("background_color", "#00FF00"))
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.hud_var = tk.StringVar(value="")
        self.hud = tk.Label(
            self.win, textvariable=self.hud_var, justify=tk.LEFT, anchor="sw",
            bg="#0a0c10", fg="#e8eaef", font=("Segoe UI", 9), padx=8, pady=6,
        )

        self._photo = None
        self._current_key = None
        self._pil_cache: dict[str, Image.Image] = {}
        self._bounce_until = 0.0
        self._bounce_amp = 0.0
        self._was_speaking = False
        self._blink_until = 0.0
        self._next_blink = 0.0
        self._blinking = False

        self.win.bind("<Configure>", lambda e: setattr(self, "_current_key", None))
        self._tick()

    def on_close(self):
        self.app.quit_app()

    def apply_display(self):
        disp = self.cfg["display"]
        bg = disp.get("background_color", "#00FF00")
        self.win.configure(bg=bg)
        self.canvas.configure(bg=bg)
        try:
            self.win.attributes("-topmost", bool(disp.get("always_on_top", True)))
        except Exception:
            pass
        if disp.get("show_debug_hud"):
            self.hud.place(x=10, rely=1.0, anchor="sw", y=-10)
        else:
            self.hud.place_forget()
        self._pil_cache.clear()
        self._current_key = None

    def _get_pil(self, key: str) -> Optional[Image.Image]:
        if key in self._pil_cache:
            return self._pil_cache[key]
        name = None
        if key.startswith("custom:"):
            sid = key.split(":", 1)[1]
            for s in self.cfg.get("custom_states") or []:
                if s.get("id") == sid:
                    name = s.get("image")
                    break
        else:
            name = (self.cfg.get("images") or {}).get(key)
        if not name:
            return None
        path = name if Path(name).is_file() else str(UPLOADS_DIR / name)
        img = load_pil(path)
        if img is None:
            return None
        self._pil_cache[key] = img
        return img

    def _tick(self):
        try:
            self._update_frame()
        except Exception as e:
            print("frame error:", e)
        self.win.after(33, self._tick)

    def _update_frame(self):
        cfg = self.cfg
        vol = self.app.audio.get_volume()
        now = time.time()
        efx = cfg.get("effects") or {}

        if efx.get("blink_enabled"):
            if self._next_blink <= 0:
                mn, mx = float(efx.get("blink_min_s", 2.5)), float(efx.get("blink_max_s", 6.5))
                self._next_blink = now + mn + random.random() * (mx - mn)
            if now >= self._next_blink and not self._blinking:
                self._blinking = True
                self._blink_until = now + float(efx.get("blink_duration_ms", 160)) / 1000.0
            if self._blinking and now >= self._blink_until:
                self._blinking = False
                mn, mx = float(efx.get("blink_min_s", 2.5)), float(efx.get("blink_max_s", 6.5))
                self._next_blink = now + mn + random.random() * (mx - mn)
        else:
            self._blinking = False

        # overrides beat audio
        override = self.app.controller.active_override()
        if override:
            key = override
        else:
            key = resolve_audio_key(cfg, vol, self._blinking)

        tier = tier_name(cfg, vol, key)
        speaking = (key in SPEAKING_KEYS) if key else False

        if efx.get("bounce") and speaking and not self._was_speaking:
            intensity = volume_intensity(cfg, vol)
            base = float(efx.get("bounce_strength", 12))
            mx = float(efx.get("bounce_strength_max", 28))
            amp = base + (mx - base) * intensity if efx.get("bounce_scale_with_volume") else base
            self._bounce_amp = amp
            self._bounce_until = now + 0.28
        self._was_speaking = speaking

        offset = 0.0
        if now < self._bounce_until:
            t = 1.0 - (self._bounce_until - now) / 0.28
            if t < 0.3:
                offset = -self._bounce_amp * (t / 0.3)
            elif t < 0.55:
                offset = -self._bounce_amp * (1.0 - (t - 0.3) / 0.25) * 0.25
            elif t < 0.75:
                offset = -self._bounce_amp * ((t - 0.55) / 0.2) * 0.12
        elif efx.get("live_intensity") and speaking:
            offset = -float(efx.get("live_intensity_max", 8)) * volume_intensity(cfg, vol)

        self._current_key = key
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        max_w = min(int((cfg.get("display") or {}).get("max_width", 480)), cw - 10)
        max_h = min(int((cfg.get("display") or {}).get("max_height", 480)), ch - 10)

        self.canvas.delete("all")
        if key:
            pil = self._get_pil(key)
            if pil is not None:
                fitted = fit_image(pil, max_w, max_h)
                if efx.get("dim_idle") and key in ("idle", "idleBlink") and not override:
                    dim = float(efx.get("dim_opacity", 0.72))
                    if fitted.mode != "RGBA":
                        fitted = fitted.convert("RGBA")
                    alpha = fitted.split()[-1].point(lambda p: int(p * dim))
                    fitted.putalpha(alpha)
                self._photo = ImageTk.PhotoImage(fitted)
                self.canvas.create_image(cw // 2, ch // 2 + int(offset), image=self._photo, anchor=tk.CENTER)

        if (cfg.get("display") or {}).get("show_debug_hud"):
            lines = [
                f"Trigger: {tier.upper()}",
                f"Key: {key or '—'}",
                f"Level: {vol:5.1f}%",
                f"Override: {self.app.controller.status_text()}",
            ]
            if self._blinking and not override:
                lines.append("blink")
            self.hud_var.set("\n".join(lines))

        self.app.debug_state = {
            "vol": round(vol, 1),
            "tier": tier,
            "key": key,
            "speaking": speaking,
            "blinking": self._blinking,
            "override": self.app.controller.status_text(),
            "threshold": float((cfg.get("audio") or {}).get("threshold", 18)),
            "loud_threshold": float((cfg.get("audio") or {}).get("loud_threshold", 45)),
        }


# ---------------------------------------------------------------------------
# Settings (scrollable)
# ---------------------------------------------------------------------------

SLOT_LABELS = [
    ("idle", "Idle"),
    ("speakingSoft", "Soft speak (opt)"),
    ("speaking", "Speaking"),
    ("speakingLoud", "Loud (opt)"),
    ("idleBlink", "Idle blink (opt)"),
    ("speakingBlink", "Speak blink (opt)"),
    ("muted", "Muted (opt)"),
]


class SettingsWindow:
    def __init__(self, root: tk.Tk, app: "App"):
        self.app = app
        self.cfg = app.cfg
        self.win = tk.Toplevel(root)
        self.win.title("Reactive Image – Settings")
        self.win.geometry("700x780")
        self.win.minsize(480, 400)
        self.win.protocol("WM_DELETE_WINDOW", self.win.withdraw)

        style = ttk.Style(self.win)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        # ---- scrollable shell ----
        shell = ttk.Frame(self.win)
        shell.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(shell, highlightthickness=0)
        vsb = ttk.Scrollbar(shell, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.outer = ttk.Frame(self.canvas, padding=12)
        self._win_id = self.canvas.create_window((0, 0), window=self.outer, anchor="nw")

        def _on_frame_configure(_event=None):
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))

        def _on_canvas_configure(event):
            self.canvas.itemconfigure(self._win_id, width=event.width)

        self.outer.bind("<Configure>", _on_frame_configure)
        self.canvas.bind("<Configure>", _on_canvas_configure)

        def _wheel(event):
            # Windows / Mac
            delta = -1 * int(event.delta / 120) if event.delta else 0
            self.canvas.yview_scroll(delta, "units")

        def _wheel_linux(event):
            self.canvas.yview_scroll(-1 if event.num == 4 else 1, "units")

        self.canvas.bind_all("<MouseWheel>", _wheel)
        self.canvas.bind_all("<Button-4>", _wheel_linux)
        self.canvas.bind_all("<Button-5>", _wheel_linux)

        outer = self.outer

        # Live debug
        dbg = ttk.LabelFrame(outer, text="Live state", padding=8)
        dbg.pack(fill=tk.X, pady=(0, 10))
        self.dbg_tier = ttk.Label(dbg, text="idle", font=("Segoe UI", 14, "bold"))
        self.dbg_tier.pack(anchor="w")
        self.dbg_info = ttk.Label(dbg, text="Level: 0%  |  Key: —")
        self.dbg_info.pack(anchor="w", pady=(4, 6))
        self.meter = ttk.Progressbar(dbg, maximum=100, mode="determinate")
        self.meter.pack(fill=tk.X)

        # Base images
        img_frame = ttk.LabelFrame(outer, text="Base images (audio)", padding=8)
        img_frame.pack(fill=tk.X, pady=(0, 10))
        self.img_labels = {}
        for slot, label in SLOT_LABELS:
            row = ttk.Frame(img_frame)
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=label, width=18).pack(side=tk.LEFT)
            lab = ttk.Label(row, text="(none)", width=24)
            lab.pack(side=tk.LEFT, padx=4)
            self.img_labels[slot] = lab
            ttk.Button(row, text="Choose…", command=lambda s=slot: self.pick_image(s)).pack(side=tk.LEFT, padx=2)
            ttk.Button(row, text="Clear", command=lambda s=slot: self.clear_image(s)).pack(side=tk.LEFT)

        # Custom states
        self.custom_frame = ttk.LabelFrame(outer, text="Custom states (hotkeys / tablet / Arduino)", padding=8)
        self.custom_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(
            self.custom_frame,
            text="Hold = while key held · Toggle = press to switch until next change · Hotkey e.g. f20, 2, a",
            wraplength=640,
        ).pack(anchor="w", pady=(0, 6))
        self.custom_list = ttk.Frame(self.custom_frame)
        self.custom_list.pack(fill=tk.X)
        ttk.Button(self.custom_frame, text="+ Add custom state", command=self.add_custom_state).pack(anchor="w", pady=6)
        self._custom_rows: list[dict] = []

        # Audio
        aud = ttk.LabelFrame(outer, text="Audio", padding=8)
        aud.pack(fill=tk.X, pady=(0, 10))
        r = ttk.Frame(aud)
        r.pack(fill=tk.X, pady=2)
        ttk.Label(r, text="Input device").pack(side=tk.LEFT)
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(r, textvariable=self.device_var, state="readonly", width=40)
        self.device_combo.pack(side=tk.LEFT, padx=6)
        ttk.Button(r, text="Refresh", command=self.refresh_devices).pack(side=tk.LEFT)
        self.thresh_var = tk.DoubleVar(value=18)
        self.loud_var = tk.DoubleVar(value=45)
        self.smooth_var = tk.DoubleVar(value=0.55)
        self._slider(aud, "Speaking threshold %", self.thresh_var, 1, 70)
        self._slider(aud, "Loud threshold %", self.loud_var, 5, 90)
        self._slider(aud, "Smoothing", self.smooth_var, 0.0, 0.95, res=0.05)

        # Effects
        efx = ttk.LabelFrame(outer, text="Effects", padding=8)
        efx.pack(fill=tk.X, pady=(0, 10))
        self.bounce_var = tk.BooleanVar(value=True)
        self.bounce_scale_var = tk.BooleanVar(value=True)
        self.live_var = tk.BooleanVar(value=True)
        self.dim_var = tk.BooleanVar(value=True)
        self.blink_var = tk.BooleanVar(value=True)
        self.muted_var = tk.BooleanVar(value=False)
        self.hud_var = tk.BooleanVar(value=True)
        self.top_var = tk.BooleanVar(value=True)
        for text, var in [
            ("Bounce on speak", self.bounce_var),
            ("Scale bounce with volume", self.bounce_scale_var),
            ("Live intensity motion", self.live_var),
            ("Dim when idle", self.dim_var),
            ("Auto blink", self.blink_var),
            ("Force muted", self.muted_var),
            ("Debug HUD on avatar", self.hud_var),
            ("Always on top", self.top_var),
        ]:
            ttk.Checkbutton(efx, text=text, variable=var).pack(anchor="w")
        self.bounce_str = tk.DoubleVar(value=12)
        self.bounce_max = tk.DoubleVar(value=28)
        self.live_max = tk.DoubleVar(value=8)
        self._slider(efx, "Bounce @ threshold (px)", self.bounce_str, 0, 40)
        self._slider(efx, "Bounce max loud (px)", self.bounce_max, 0, 60)
        self._slider(efx, "Live intensity max (px)", self.live_max, 0, 24)

        # Display
        disp = ttk.LabelFrame(outer, text="Display", padding=8)
        disp.pack(fill=tk.X, pady=(0, 10))
        r = ttk.Frame(disp)
        r.pack(fill=tk.X)
        ttk.Label(r, text="Background").pack(side=tk.LEFT)
        self.bg_var = tk.StringVar(value="#00FF00")
        ttk.Entry(r, textvariable=self.bg_var, width=10).pack(side=tk.LEFT, padx=6)
        ttk.Button(r, text="Pick…", command=self.pick_color).pack(side=tk.LEFT)
        for c, name in [("#00FF00", "Green"), ("#FF00FF", "Magenta"), ("#000000", "Black")]:
            ttk.Button(r, text=name, command=lambda col=c: self.bg_var.set(col)).pack(side=tk.LEFT, padx=2)
        self.max_w = tk.IntVar(value=480)
        self.max_h = tk.IntVar(value=480)
        r2 = ttk.Frame(disp)
        r2.pack(fill=tk.X, pady=4)
        ttk.Label(r2, text="Max W").pack(side=tk.LEFT)
        ttk.Entry(r2, textvariable=self.max_w, width=6).pack(side=tk.LEFT, padx=4)
        ttk.Label(r2, text="Max H").pack(side=tk.LEFT)
        ttk.Entry(r2, textvariable=self.max_h, width=6).pack(side=tk.LEFT, padx=4)

        # Remote control
        ctrl = ttk.LabelFrame(outer, text="Remote control (tablet / Arduino)", padding=8)
        ctrl.pack(fill=tk.X, pady=(0, 10))
        self.http_en = tk.BooleanVar(value=True)
        self.http_port = tk.IntVar(value=3851)
        self.http_lan = tk.BooleanVar(value=False)
        self.serial_en = tk.BooleanVar(value=False)
        self.serial_port = tk.StringVar(value="")
        self.serial_baud = tk.IntVar(value=115200)
        ttk.Checkbutton(ctrl, text="Enable HTTP control server", variable=self.http_en).pack(anchor="w")
        ttk.Checkbutton(
            ctrl,
            text="Allow devices on my network (tablet / ESP) — otherwise this PC only",
            variable=self.http_lan,
            command=lambda: self._update_http_hint(),
        ).pack(anchor="w")
        r = ttk.Frame(ctrl)
        r.pack(fill=tk.X, pady=2)
        ttk.Label(r, text="HTTP port").pack(side=tk.LEFT)
        ttk.Entry(r, textvariable=self.http_port, width=8).pack(side=tk.LEFT, padx=6)
        self.http_hint = ttk.Label(ctrl, text="", wraplength=640)
        self.http_hint.pack(anchor="w")
        ttk.Checkbutton(ctrl, text="Enable USB serial (Arduino)", variable=self.serial_en).pack(anchor="w", pady=(8, 0))
        r = ttk.Frame(ctrl)
        r.pack(fill=tk.X, pady=2)
        ttk.Label(r, text="COM port").pack(side=tk.LEFT)
        self.serial_combo = ttk.Combobox(r, textvariable=self.serial_port, width=18)
        self.serial_combo.pack(side=tk.LEFT, padx=4)
        ttk.Button(r, text="Refresh ports", command=self.refresh_serial_ports).pack(side=tk.LEFT)
        ttk.Label(r, text="Baud").pack(side=tk.LEFT, padx=(8, 0))
        ttk.Entry(r, textvariable=self.serial_baud, width=8).pack(side=tk.LEFT, padx=4)
        ttk.Label(
            ctrl,
            text="Serial lines: HOLD <id>  |  RELEASE <id>  |  TOGGLE <id>  |  CLEAR\n"
                 "HTTP:  http://<pc-ip>:<port>/hold/<id>  /release/<id>  /toggle/<id>  /clear  /status",
            wraplength=640,
        ).pack(anchor="w", pady=4)

        # Save
        btn_row = ttk.Frame(outer)
        btn_row.pack(fill=tk.X, pady=8)
        ttk.Button(btn_row, text="Save settings", command=self.save).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Show avatar", command=self.app.show_avatar).pack(side=tk.LEFT, padx=8)
        ttk.Button(btn_row, text="Clear overrides", command=self.app.controller.clear_all).pack(side=tk.LEFT)
        self.status = ttk.Label(btn_row, text="")
        self.status.pack(side=tk.LEFT, padx=8)

        self.load_into_ui()
        self.refresh_devices()
        self.refresh_serial_ports()
        self._poll_debug()

    def _slider(self, parent, label, var, from_, to, res=1.0):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text=label, width=28).pack(side=tk.LEFT)
        val = ttk.Label(row, width=6)
        val.pack(side=tk.RIGHT)

        def upd(_=None, v=var, lab=val):
            lab.config(text=f"{v.get():.2f}" if res < 1 else f"{int(v.get())}")

        ttk.Scale(row, from_=from_, to=to, variable=var, command=upd).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=4
        )
        upd()

    def _rebuild_custom_rows(self):
        for child in self.custom_list.winfo_children():
            child.destroy()
        self._custom_rows = []
        for st in self.cfg.get("custom_states") or []:
            self._add_custom_row(st)

    def _add_custom_row(self, st: dict):
        row = ttk.Frame(self.custom_list)
        row.pack(fill=tk.X, pady=3)
        enabled = tk.BooleanVar(value=st.get("enabled", True))
        name_var = tk.StringVar(value=st.get("name") or st.get("id") or "")
        trigger_var = tk.StringVar(value=st.get("trigger") or "toggle")
        hotkey_var = tk.StringVar(value=st.get("hotkey") or "")
        img_lab = ttk.Label(row, text=(st.get("image") or "(no image)"), width=16)

        ttk.Checkbutton(row, variable=enabled, width=2).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=name_var, width=12).pack(side=tk.LEFT, padx=2)
        ttk.Combobox(row, textvariable=trigger_var, values=["hold", "toggle"], width=8, state="readonly").pack(
            side=tk.LEFT, padx=2
        )
        ttk.Entry(row, textvariable=hotkey_var, width=8).pack(side=tk.LEFT, padx=2)
        img_lab.pack(side=tk.LEFT, padx=2)

        sid = st.get("id") or str(uuid.uuid4())[:8]

        def choose():
            path = filedialog.askopenfilename(
                title="Custom state image",
                filetypes=[("Images", "*.png;*.jpg;*.jpeg;*.gif;*.webp"), ("All", "*.*")],
            )
            if not path:
                return
            from shutil import copy2
            ext = Path(path).suffix.lower() or ".png"
            dest_name = f"custom_{sid}{ext}"
            copy2(path, UPLOADS_DIR / dest_name)
            st["image"] = dest_name
            img_lab.config(text=dest_name)
            self.app.on_config_changed()

        def remove():
            states = self.cfg.get("custom_states") or []
            self.cfg["custom_states"] = [s for s in states if s.get("id") != sid]
            save_config(self.cfg)
            self._rebuild_custom_rows()
            self.app.on_config_changed()

        ttk.Button(row, text="Image…", command=choose).pack(side=tk.LEFT, padx=2)
        ttk.Button(row, text="X", width=3, command=remove).pack(side=tk.LEFT)

        self._custom_rows.append({
            "id": sid,
            "enabled": enabled,
            "name": name_var,
            "trigger": trigger_var,
            "hotkey": hotkey_var,
            "st": st,
        })

    def add_custom_state(self):
        sid = "s_" + str(uuid.uuid4())[:6]
        st = {"id": sid, "name": "New state", "image": None, "trigger": "toggle", "hotkey": "", "enabled": True}
        self.cfg.setdefault("custom_states", []).append(st)
        save_config(self.cfg)
        self._add_custom_row(st)
        self.app.on_config_changed()

    def _collect_custom_states(self) -> list:
        out = []
        for row in self._custom_rows:
            st = dict(row["st"])
            st["id"] = row["id"]
            st["enabled"] = bool(row["enabled"].get())
            st["name"] = (row["name"].get() or row["id"]).strip()
            st["trigger"] = (row["trigger"].get() or "toggle").strip().lower()
            st["hotkey"] = (row["hotkey"].get() or "").strip().lower()
            out.append(st)
        return out

    def load_into_ui(self):
        cfg = self.cfg
        a, e, d, c = cfg["audio"], cfg["effects"], cfg["display"], cfg.get("control") or {}
        self.thresh_var.set(a.get("threshold", 18))
        self.loud_var.set(a.get("loud_threshold", 45))
        self.smooth_var.set(a.get("smoothing", 0.55))
        self.bounce_var.set(e.get("bounce", True))
        self.bounce_scale_var.set(e.get("bounce_scale_with_volume", True))
        self.live_var.set(e.get("live_intensity", True))
        self.dim_var.set(e.get("dim_idle", True))
        self.blink_var.set(e.get("blink_enabled", True))
        self.muted_var.set(cfg.get("force_muted", False))
        self.hud_var.set(d.get("show_debug_hud", True))
        self.top_var.set(d.get("always_on_top", True))
        self.bounce_str.set(e.get("bounce_strength", 12))
        self.bounce_max.set(e.get("bounce_strength_max", 28))
        self.live_max.set(e.get("live_intensity_max", 8))
        self.bg_var.set(d.get("background_color", "#00FF00"))
        self.max_w.set(d.get("max_width", 480))
        self.max_h.set(d.get("max_height", 480))
        self.http_en.set(c.get("http_enabled", True))
        self.http_port.set(c.get("http_port", 3851))
        self.http_lan.set(bool(c.get("http_lan", False)))
        self.serial_en.set(c.get("serial_enabled", False))
        self.serial_port.set(c.get("serial_port") or "")
        self.serial_baud.set(c.get("serial_baud", 115200))
        self._refresh_img_labels()
        self._rebuild_custom_rows()
        self._update_http_hint()

    def _update_http_hint(self):
        port = int(self.http_port.get() or 3851)
        if self.http_lan.get():
            text = f"From tablet/Arduino on same network:  http://<this-pc-ip>:{port}/toggle/your_state_id"
        else:
            text = (f"This PC only:  http://127.0.0.1:{port}/toggle/your_state_id  "
                    "(tick the box above for tablets / ESP on your network)")
        self.http_hint.config(text=text)

    def _refresh_img_labels(self):
        for slot, lab in self.img_labels.items():
            name = (self.cfg.get("images") or {}).get(slot)
            lab.config(text=name if name else "(none)")

    def refresh_devices(self):
        devices = self.app.audio.list_devices()
        names = ["(Default)"] + [f"{d['index']}: {d['name']}" for d in devices]
        self.device_combo["values"] = names
        cur = (self.cfg.get("audio") or {}).get("device")
        if cur is None:
            self.device_combo.current(0)
        else:
            match = next((i + 1 for i, d in enumerate(devices) if d["index"] == cur), 0)
            self.device_combo.current(match)

    def refresh_serial_ports(self):
        ports = []
        if list_ports is not None:
            try:
                ports = [p.device for p in list_ports.comports()]
            except Exception:
                pass
        self.serial_combo["values"] = ports
        if self.serial_port.get() and self.serial_port.get() not in ports and ports:
            pass
        elif ports and not self.serial_port.get():
            self.serial_port.set(ports[0])

    def pick_image(self, slot: str):
        path = filedialog.askopenfilename(
            title=f"Choose image for {slot}",
            filetypes=[("Images", "*.png;*.jpg;*.jpeg;*.gif;*.webp"), ("All files", "*.*")],
        )
        if not path:
            return
        from shutil import copy2
        ext = Path(path).suffix.lower() or ".png"
        dest_name = f"{slot}{ext}"
        try:
            copy2(path, UPLOADS_DIR / dest_name)
        except Exception as e:
            messagebox.showerror("Copy failed", str(e))
            return
        self.cfg.setdefault("images", {})[slot] = dest_name
        save_config(self.cfg)
        self._refresh_img_labels()
        self.app.on_config_changed()
        self.status.config(text=f"{slot} set")

    def clear_image(self, slot: str):
        name = (self.cfg.get("images") or {}).get(slot)
        if name:
            p = UPLOADS_DIR / name
            if p.is_file():
                try:
                    p.unlink()
                except Exception:
                    pass
        self.cfg.setdefault("images", {})[slot] = None
        save_config(self.cfg)
        self._refresh_img_labels()
        self.app.on_config_changed()

    def pick_color(self):
        c = colorchooser.askcolor(color=self.bg_var.get(), title="Background color")
        if c and c[1]:
            self.bg_var.set(c[1])

    def save(self):
        dev_sel = self.device_combo.current()
        device = None
        if dev_sel > 0:
            devices = self.app.audio.list_devices()
            if 0 <= dev_sel - 1 < len(devices):
                device = devices[dev_sel - 1]["index"]

        self.cfg["audio"]["device"] = device
        self.cfg["audio"]["threshold"] = float(self.thresh_var.get())
        self.cfg["audio"]["loud_threshold"] = float(self.loud_var.get())
        self.cfg["audio"]["smoothing"] = float(self.smooth_var.get())
        self.cfg["effects"]["bounce"] = bool(self.bounce_var.get())
        self.cfg["effects"]["bounce_scale_with_volume"] = bool(self.bounce_scale_var.get())
        self.cfg["effects"]["live_intensity"] = bool(self.live_var.get())
        self.cfg["effects"]["dim_idle"] = bool(self.dim_var.get())
        self.cfg["effects"]["blink_enabled"] = bool(self.blink_var.get())
        self.cfg["effects"]["bounce_strength"] = float(self.bounce_str.get())
        self.cfg["effects"]["bounce_strength_max"] = float(self.bounce_max.get())
        self.cfg["effects"]["live_intensity_max"] = float(self.live_max.get())
        self.cfg["force_muted"] = bool(self.muted_var.get())
        self.cfg["display"]["show_debug_hud"] = bool(self.hud_var.get())
        self.cfg["display"]["always_on_top"] = bool(self.top_var.get())
        self.cfg["display"]["background_color"] = self.bg_var.get().strip() or "#00FF00"
        self.cfg["display"]["max_width"] = int(self.max_w.get())
        self.cfg["display"]["max_height"] = int(self.max_h.get())
        self.cfg["custom_states"] = self._collect_custom_states()
        prev_ctrl = dict(self.cfg.get("control") or {})
        self.cfg["control"] = {
            **prev_ctrl,
            "http_enabled": bool(self.http_en.get()),
            "http_port": int(self.http_port.get() or 3851),
            "http_lan": bool(self.http_lan.get()),
            "serial_enabled": bool(self.serial_en.get()),
            "serial_port": self.serial_port.get().strip(),
            "serial_baud": int(self.serial_baud.get() or 115200),
        }
        save_config(self.cfg)
        self.app.on_config_changed()
        self._update_http_hint()
        self.status.config(text="Saved ✓")
        self.win.after(2000, lambda: self.status.config(text=""))

    def _poll_debug(self):
        st = self.app.debug_state
        if st:
            self.dbg_tier.config(text=str(st.get("tier", "idle")).upper())
            self.dbg_info.config(
                text=f"Level: {st.get('vol', 0)}%  |  Key: {st.get('key') or '—'}  |  "
                     f"Override: {st.get('override') or 'none'}"
            )
            try:
                self.meter["value"] = float(st.get("vol") or 0)
            except Exception:
                pass
        if self.win.winfo_exists():
            self.win.after(100, self._poll_debug)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class App:
    def __init__(self):
        if Image is None:
            messagebox.showerror("Missing dependency", "Pillow is required.\n\npip install Pillow sounddevice numpy pynput")
            sys.exit(1)
        if sd is None:
            messagebox.showerror("Missing dependency", "sounddevice is required.\n\npip install sounddevice numpy")
            sys.exit(1)

        self.cfg = load_config()
        self.audio = AudioEngine()
        self.debug_state: dict = {}
        self.root = tk.Tk()
        self.root.withdraw()

        self.controller = StateController(self)
        self.avatar = AvatarWindow(self.root, self)
        self.settings = SettingsWindow(self.root, self)

        self._start_audio()
        self.controller.restart_all()
        if pynput_kb is None:
            print("[warn] pynput not installed – hotkeys disabled. pip install pynput")

    def _start_audio(self):
        a = self.cfg.get("audio") or {}
        ok = self.audio.start(
            device=a.get("device"),
            smoothing=a.get("smoothing", 0.55),
            sensitivity=a.get("sensitivity", 1.0),
        )
        if not ok:
            messagebox.showwarning("Microphone", "Could not open the microphone.\nCheck the device in Settings.")

    def on_config_changed(self):
        self.cfg = load_config()
        self.avatar.cfg = self.cfg
        self.settings.cfg = self.cfg
        self.avatar.apply_display()
        self._start_audio()
        self.controller.restart_all()

    def show_avatar(self):
        try:
            self.avatar.win.deiconify()
            self.avatar.win.lift()
        except Exception:
            pass

    def quit_app(self):
        self.controller.stop_all()
        self.audio.stop()
        try:
            self.root.destroy()
        except Exception:
            pass
        sys.exit(0)

    def run(self):
        self.root.mainloop()


def main():
    App().run()


if __name__ == "__main__":
    main()
