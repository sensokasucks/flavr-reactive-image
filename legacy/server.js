/**
 * Reactive Image – pure Node server (no Electron)
 * Opens Edge/Chrome in --app mode for Window Capture in XSplit.
 */
const http = require('http');
const fs = require('fs');
const path = require('path');
const { URL } = require('url');
const { exec } = require('child_process');

const PORT = parseInt(process.env.PORT || '3850', 10);
const ROOT = __dirname;
const UPLOADS = path.join(ROOT, 'uploads');
const RENDERER = path.join(ROOT, 'renderer');
const CONFIG_PATH = path.join(ROOT, 'config.json');

if (!fs.existsSync(UPLOADS)) fs.mkdirSync(UPLOADS, { recursive: true });

const DEFAULT_CONFIG = {
  images: {
    idle: null,
    speakingSoft: null,
    speaking: null,
    speakingLoud: null,
    idleBlink: null,
    speakingBlink: null,
    muted: null
  },
  audio: {
    deviceId: 'default',
    threshold: 18,
    loudThreshold: 45,
    softRatio: 0.45,
    smoothing: 0.55,
    sensitivity: 1.0
  },
  effects: {
    bounce: true,
    bounceStrength: 12,
    bounceStrengthMax: 28,
    bounceScaleWithVolume: true,
    bounceDuration: 280,
    liveIntensity: true,
    liveIntensityMax: 8,
    crossfadeMs: 120,
    dimIdle: true,
    dimOpacity: 0.72,
    blinkEnabled: true,
    blinkMinInterval: 2.5,
    blinkMaxInterval: 6.5,
    blinkDuration: 160
  },
  display: {
    maxWidth: 480,
    maxHeight: 480,
    backgroundColor: '#00FF00',
    showDebugHud: true
  },
  forceMuted: false
};

function loadConfig() {
  try {
    if (fs.existsSync(CONFIG_PATH)) {
      const raw = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
      return {
        ...DEFAULT_CONFIG,
        ...raw,
        images: { ...DEFAULT_CONFIG.images, ...(raw.images || {}) },
        audio: { ...DEFAULT_CONFIG.audio, ...(raw.audio || {}) },
        effects: { ...DEFAULT_CONFIG.effects, ...(raw.effects || {}) },
        display: { ...DEFAULT_CONFIG.display, ...(raw.display || {}) }
      };
    }
  } catch (e) {
    console.warn('[config] load failed:', e.message);
  }
  return JSON.parse(JSON.stringify(DEFAULT_CONFIG));
}

function saveConfig(cfg) {
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(cfg, null, 2), 'utf8');
}

let config = loadConfig();
let latestDebug = {
  vol: 0,
  tier: 'idle',
  key: null,
  speaking: false,
  blinking: false,
  threshold: 18,
  loudThreshold: 45
};

const IMAGE_SLOTS = Object.keys(DEFAULT_CONFIG.images);

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp'
};

function send(res, status, body, headers = {}) {
  const data = typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body);
  res.writeHead(status, {
    'Content-Type': headers['Content-Type'] || (typeof body === 'object' ? 'application/json' : 'text/plain'),
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET,POST,DELETE,OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Cache-Control': 'no-store',
    ...headers
  });
  res.end(data);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

function serveStatic(filePath, res) {
  if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
    send(res, 404, 'Not found');
    return;
  }
  const ext = path.extname(filePath).toLowerCase();
  const mime = MIME[ext] || 'application/octet-stream';
  res.writeHead(200, {
    'Content-Type': mime,
    'Access-Control-Allow-Origin': '*',
    'Cache-Control': ext.match(/\.(png|jpe?g|gif|webp)$/) ? 'no-cache' : 'no-store'
  });
  fs.createReadStream(filePath).pipe(res);
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url || '/', `http://127.0.0.1:${PORT}`);
  const pathname = decodeURIComponent(url.pathname);

  if (req.method === 'OPTIONS') {
    send(res, 204, '');
    return;
  }

  if (pathname === '/api/config' && req.method === 'GET') {
    send(res, 200, config);
    return;
  }

  if (pathname === '/api/config' && req.method === 'POST') {
    try {
      const raw = await readBody(req);
      const body = JSON.parse(raw.toString('utf8') || '{}');
      config = {
        ...config,
        ...body,
        images: { ...config.images, ...(body.images || {}) },
        audio: { ...config.audio, ...(body.audio || {}) },
        effects: { ...config.effects, ...(body.effects || {}) },
        display: { ...config.display, ...(body.display || {}) }
      };
      saveConfig(config);
      send(res, 200, { ok: true, config });
    } catch (e) {
      send(res, 400, { ok: false, error: e.message });
    }
    return;
  }

  if (pathname === '/api/debug' && req.method === 'GET') {
    send(res, 200, latestDebug);
    return;
  }

  if (pathname === '/api/debug' && req.method === 'POST') {
    try {
      const raw = await readBody(req);
      latestDebug = { ...latestDebug, ...JSON.parse(raw.toString('utf8') || '{}') };
      send(res, 200, { ok: true });
    } catch (e) {
      send(res, 400, { ok: false, error: e.message });
    }
    return;
  }

  if (pathname === '/api/upload' && req.method === 'POST') {
    try {
      const raw = await readBody(req);
      const body = JSON.parse(raw.toString('utf8') || '{}');
      if (!IMAGE_SLOTS.includes(body.slot)) {
        send(res, 400, { ok: false, error: 'Invalid slot' });
        return;
      }
      if (!body.data || typeof body.data !== 'string') {
        send(res, 400, { ok: false, error: 'Missing data' });
        return;
      }
      const safeName = (body.filename || 'image.png').replace(/[^a-zA-Z0-9._-]/g, '_');
      const filename = Date.now() + '-' + safeName;
      const buf = Buffer.from(body.data, 'base64');
      if (buf.length > 8 * 1024 * 1024) {
        send(res, 400, { ok: false, error: 'File too large (max 8MB)' });
        return;
      }
      const prev = config.images[body.slot];
      if (prev) {
        const prevPath = path.join(UPLOADS, prev);
        if (fs.existsSync(prevPath)) try { fs.unlinkSync(prevPath); } catch (_) {}
      }
      fs.writeFileSync(path.join(UPLOADS, filename), buf);
      config.images[body.slot] = filename;
      saveConfig(config);
      send(res, 200, { ok: true, slot: body.slot, filename, url: '/uploads/' + filename });
    } catch (e) {
      send(res, 400, { ok: false, error: e.message });
    }
    return;
  }

  const delMatch = pathname.match(/^\/api\/image\/([a-zA-Z]+)$/);
  if (delMatch && req.method === 'DELETE') {
    const slot = delMatch[1];
    if (!(slot in config.images)) {
      send(res, 400, { ok: false, error: 'Invalid slot' });
      return;
    }
    const prev = config.images[slot];
    if (prev) {
      const prevPath = path.join(UPLOADS, prev);
      if (fs.existsSync(prevPath)) try { fs.unlinkSync(prevPath); } catch (_) {}
    }
    config.images[slot] = null;
    saveConfig(config);
    send(res, 200, { ok: true });
    return;
  }

  if (pathname === '/api/health') {
    send(res, 200, { ok: true, port: PORT });
    return;
  }

  // pages
  if (pathname === '/' || pathname === '/settings.html' || pathname === '/settings') {
    serveStatic(path.join(RENDERER, 'settings.html'), res);
    return;
  }
  if (pathname === '/avatar.html' || pathname === '/avatar') {
    serveStatic(path.join(RENDERER, 'avatar.html'), res);
    return;
  }
  if (pathname.startsWith('/uploads/')) {
    serveStatic(path.join(UPLOADS, path.basename(pathname)), res);
    return;
  }
  // renderer assets
  const base = path.basename(pathname);
  if (base.match(/^(avatar|settings)\.(js|css)$/)) {
    serveStatic(path.join(RENDERER, base), res);
    return;
  }

  send(res, 404, 'Not found');
});

function openAppWindow(url, width, height) {
  const w = width || 520;
  const h = height || 560;
  const platform = process.platform;

  if (platform === 'win32') {
    // Edge first, then Chrome — --app gives a dedicated window (good for Window Capture)
    const edge = `cmd /c start "" msedge --app="${url}" --window-size=${w},${h}`;
    const chrome = `cmd /c start "" chrome --app="${url}" --window-size=${w},${h}`;
    exec(edge, (err) => {
      if (err) {
        exec(chrome, (err2) => {
          if (err2) {
            console.warn('[launch] Could not open Edge or Chrome. Open manually:', url);
            exec(`cmd /c start "" "${url}"`);
          }
        });
      }
    });
  } else if (platform === 'darwin') {
    exec(`open -a "Google Chrome" --args --app="${url}"`);
  } else {
    exec(`xdg-open "${url}"`);
  }
}

server.listen(PORT, '127.0.0.1', () => {
  const avatarUrl = `http://127.0.0.1:${PORT}/avatar.html`;
  const settingsUrl = `http://127.0.0.1:${PORT}/settings.html`;

  console.log('=== Reactive Image ===');
  console.log(`Avatar   : ${avatarUrl}`);
  console.log(`Settings : ${settingsUrl}`);
  console.log('');
  console.log('Capture the Avatar window in XSplit (Window Capture).');
  console.log('Press Ctrl+C to stop.');
  console.log('');

  // small delay so server is fully ready
  setTimeout(() => {
    openAppWindow(avatarUrl, 520, 580);
    setTimeout(() => openAppWindow(settingsUrl, 780, 900), 600);
  }, 300);
});
