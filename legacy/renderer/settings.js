(() => {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];

  const SLOT_META = [
    { id: 'idle', label: 'Idle', opt: false },
    { id: 'speakingSoft', label: 'Soft speak', opt: true },
    { id: 'speaking', label: 'Speaking', opt: false },
    { id: 'speakingLoud', label: 'Loud', opt: true },
    { id: 'idleBlink', label: 'Idle blink', opt: true },
    { id: 'speakingBlink', label: 'Speak blink', opt: true },
    { id: 'muted', label: 'Muted', opt: true }
  ];

  let currentConfig = null;

  function buildSlots() {
    const root = $('#slots');
    if (!root) return;
    root.innerHTML = '';
    for (const s of SLOT_META) {
      const div = document.createElement('div');
      div.className = 'slot';
      div.dataset.slot = s.id;
      div.innerHTML =
        '<label class="slot-title">' + s.label +
        (s.opt ? ' <span class="opt">opt</span>' : '') +
        '</label>' +
        '<div class="preview" id="prev-' + s.id + '"><span>No image</span></div>' +
        '<input type="file" accept="image/png,image/jpeg,image/gif,image/webp,image/*" id="file-' + s.id + '" class="file-input" />' +
        '<button type="button" class="btn-clear" data-clear="' + s.id + '">Clear</button>';
      root.appendChild(div);

      const input = div.querySelector('input[type="file"]');
      input.addEventListener('change', (e) => {
        const f = e.target.files && e.target.files[0];
        if (f) uploadSlot(s.id, f);
        e.target.value = '';
      });
    }

    $$('.btn-clear').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const slot = btn.dataset.clear;
        try {
          await fetch('/api/image/' + slot, { method: 'DELETE' });
          if (currentConfig && currentConfig.images) currentConfig.images[slot] = null;
          updatePreviews(currentConfig || { images: {} });
        } catch (e) {
          console.error(e);
        }
      });
    });
  }

  async function loadConfig() {
    try {
      const res = await fetch('/api/config');
      currentConfig = await res.json();
      applyToUI(currentConfig);
      updatePreviews(currentConfig);
      updateMarks();
    } catch (e) {
      console.error('loadConfig failed', e);
      $('#saveStatus').textContent = 'Cannot reach server – is npm start running?';
    }
  }

  function applyToUI(cfg) {
    if (!cfg) return;
    const a = cfg.audio || {};
    const efx = cfg.effects || {};
    const disp = cfg.display || {};

    $('#threshold').value = a.threshold ?? 18;
    $('#threshVal').textContent = a.threshold ?? 18;
    $('#loudThreshold').value = a.loudThreshold ?? 45;
    $('#loudVal').textContent = a.loudThreshold ?? 45;
    $('#smoothing').value = a.smoothing ?? 0.55;
    $('#smoothVal').textContent = Number(a.smoothing ?? 0.55).toFixed(2);

    $('#bounce').checked = !!efx.bounce;
    $('#bounceScaleWithVolume').checked = efx.bounceScaleWithVolume !== false;
    $('#liveIntensity').checked = efx.liveIntensity !== false;
    $('#dimIdle').checked = !!efx.dimIdle;
    $('#blinkEnabled').checked = !!efx.blinkEnabled;
    $('#forceMuted').checked = !!cfg.forceMuted;
    $('#showDebugHud').checked = disp.showDebugHud !== false;

    $('#bounceStrength').value = efx.bounceStrength ?? 12;
    $('#bounceVal').textContent = efx.bounceStrength ?? 12;
    $('#bounceStrengthMax').value = efx.bounceStrengthMax ?? 28;
    $('#bounceMaxVal').textContent = efx.bounceStrengthMax ?? 28;
    $('#liveIntensityMax').value = efx.liveIntensityMax ?? 8;
    $('#liveMaxVal').textContent = efx.liveIntensityMax ?? 8;
    $('#crossfade').value = efx.crossfadeMs ?? 120;
    $('#fadeVal').textContent = efx.crossfadeMs ?? 120;
    $('#dimOpacity').value = efx.dimOpacity ?? 0.72;
    $('#dimVal').textContent = Number(efx.dimOpacity ?? 0.72).toFixed(2);

    const bg = disp.backgroundColor || '#00FF00';
    try {
      if (/^#[0-9a-fA-F]{6}$/.test(bg)) $('#bgColor').value = bg;
    } catch (_) {}
    $('#bgColorText').value = bg;
    $('#maxWidth').value = disp.maxWidth ?? 480;
    $('#maxHeight').value = disp.maxHeight ?? 480;

    const sel = $('#deviceSelect');
    if (a.deviceId && [...sel.options].some((o) => o.value === a.deviceId)) {
      sel.value = a.deviceId;
    }
  }

  function updateMarks() {
    const speak = $('#threshold');
    const loud = $('#loudThreshold');
    if ($('#markSpeak') && speak) $('#markSpeak').style.left = speak.value + '%';
    if ($('#markLoud') && loud) $('#markLoud').style.left = loud.value + '%';
  }

  function updatePreviews(cfg) {
    const images = (cfg && cfg.images) || {};
    for (const s of SLOT_META) {
      const el = $('#prev-' + s.id);
      if (!el) continue;
      el.innerHTML = '';
      if (images[s.id]) {
        const img = document.createElement('img');
        img.src = '/uploads/' + images[s.id] + '?t=' + Date.now();
        img.alt = s.id;
        el.appendChild(img);
      } else {
        el.innerHTML = '<span>No image</span>';
      }
    }
  }

  async function saveSettings() {
    let bg = ($('#bgColorText').value || '#00FF00').trim();
    if (!/^#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$/.test(bg)) bg = $('#bgColor').value;

    const body = {
      audio: {
        deviceId: $('#deviceSelect').value || 'default',
        threshold: Number($('#threshold').value),
        loudThreshold: Number($('#loudThreshold').value),
        softRatio: 0.45,
        smoothing: Number($('#smoothing').value),
        sensitivity: 1.0
      },
      effects: {
        bounce: $('#bounce').checked,
        bounceScaleWithVolume: $('#bounceScaleWithVolume').checked,
        bounceStrength: Number($('#bounceStrength').value),
        bounceStrengthMax: Number($('#bounceStrengthMax').value),
        bounceDuration: 280,
        liveIntensity: $('#liveIntensity').checked,
        liveIntensityMax: Number($('#liveIntensityMax').value),
        crossfadeMs: Number($('#crossfade').value),
        dimIdle: $('#dimIdle').checked,
        dimOpacity: Number($('#dimOpacity').value),
        blinkEnabled: $('#blinkEnabled').checked,
        blinkMinInterval: 2.5,
        blinkMaxInterval: 6.5,
        blinkDuration: 160
      },
      display: {
        maxWidth: Number($('#maxWidth').value) || 480,
        maxHeight: Number($('#maxHeight').value) || 480,
        backgroundColor: bg,
        showDebugHud: $('#showDebugHud').checked
      },
      forceMuted: $('#forceMuted').checked
    };

    try {
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      const data = await res.json();
      if (data.ok) {
        currentConfig = data.config;
        $('#saveStatus').textContent = 'Saved ✓';
        setTimeout(() => { $('#saveStatus').textContent = ''; }, 2200);
      } else {
        $('#saveStatus').textContent = 'Save failed';
      }
    } catch (e) {
      $('#saveStatus').textContent = 'Server error – is npm start running?';
    }
  }

  function fileToBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const result = String(reader.result || '');
        const base64 = result.includes(',') ? result.split(',')[1] : result;
        resolve({ base64, name: file.name });
      };
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  }

  async function uploadSlot(slot, file) {
    $('#saveStatus').textContent = 'Uploading ' + slot + '…';
    try {
      const { base64, name } = await fileToBase64(file);
      const res = await fetch('/api/upload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slot, filename: name, data: base64 })
      });
      const data = await res.json();
      if (data.ok) {
        if (!currentConfig) currentConfig = { images: {} };
        if (!currentConfig.images) currentConfig.images = {};
        currentConfig.images[slot] = data.filename;
        updatePreviews(currentConfig);
        $('#saveStatus').textContent = slot + ' uploaded ✓';
        setTimeout(() => { $('#saveStatus').textContent = ''; }, 1800);
      } else {
        $('#saveStatus').textContent = data.error || 'Upload failed';
        alert(data.error || 'Upload failed');
      }
    } catch (e) {
      console.error(e);
      $('#saveStatus').textContent = 'Upload error';
      alert('Upload error: ' + e.message);
    }
  }

  async function listDevices() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach((t) => t.stop());
      const devices = await navigator.mediaDevices.enumerateDevices();
      const inputs = devices.filter((d) => d.kind === 'audioinput');
      const sel = $('#deviceSelect');
      const prev = sel.value;
      sel.innerHTML = '';
      const def = document.createElement('option');
      def.value = 'default';
      def.textContent = 'Default microphone';
      sel.appendChild(def);
      for (const d of inputs) {
        const opt = document.createElement('option');
        opt.value = d.deviceId;
        opt.textContent = d.label || 'Mic ' + d.deviceId.slice(0, 8);
        sel.appendChild(opt);
      }
      if (currentConfig?.audio?.deviceId && [...sel.options].some((o) => o.value === currentConfig.audio.deviceId)) {
        sel.value = currentConfig.audio.deviceId;
      } else if (prev && [...sel.options].some((o) => o.value === prev)) {
        sel.value = prev;
      }
    } catch (e) {
      console.warn('listDevices', e);
    }
  }

  async function pollDebug() {
    try {
      const res = await fetch('/api/debug');
      const state = await res.json();
      const tier = state.tier || 'idle';
      const tierEl = $('#dbgTier');
      if (!tierEl) return;
      tierEl.textContent = tier;
      tierEl.className = 'dbg-tier tier-' + tier;
      $('#dbgKey').textContent = state.key || '—';
      $('#dbgVol').textContent = (state.vol ?? 0) + '%';
      const flags = [];
      if (state.speaking) flags.push('speaking');
      if (state.blinking) flags.push('blink');
      $('#dbgFlags').textContent = flags.join(', ') || 'idle';
      $('#levelBar').style.width = (state.vol ?? 0) + '%';
      const t = state.threshold ?? 18;
      const loud = state.loudThreshold ?? 45;
      const pct = state.vol ?? 0;
      if (pct >= loud) $('#levelBar').style.background = 'linear-gradient(90deg, #5ce1b0, #ffb86c)';
      else if (pct >= t) $('#levelBar').style.background = 'linear-gradient(90deg, #5ce1b0, #6c8cff)';
      else $('#levelBar').style.background = 'linear-gradient(90deg, #3a4250, #5a6478)';
    } catch (_) {}
  }

  // wire controls
  $('#threshold').addEventListener('input', (e) => {
    $('#threshVal').textContent = e.target.value;
    updateMarks();
  });
  $('#loudThreshold').addEventListener('input', (e) => {
    $('#loudVal').textContent = e.target.value;
    updateMarks();
  });
  $('#smoothing').addEventListener('input', (e) => {
    $('#smoothVal').textContent = Number(e.target.value).toFixed(2);
  });
  $('#bounceStrength').addEventListener('input', (e) => { $('#bounceVal').textContent = e.target.value; });
  $('#bounceStrengthMax').addEventListener('input', (e) => { $('#bounceMaxVal').textContent = e.target.value; });
  $('#liveIntensityMax').addEventListener('input', (e) => { $('#liveMaxVal').textContent = e.target.value; });
  $('#crossfade').addEventListener('input', (e) => { $('#fadeVal').textContent = e.target.value; });
  $('#dimOpacity').addEventListener('input', (e) => { $('#dimVal').textContent = Number(e.target.value).toFixed(2); });

  $('#bgColor').addEventListener('input', (e) => { $('#bgColorText').value = e.target.value; });
  $('#bgColorText').addEventListener('change', (e) => {
    const v = e.target.value.trim();
    if (/^#[0-9a-fA-F]{6}$/.test(v)) $('#bgColor').value = v;
  });
  $$('.swatch').forEach((btn) => {
    btn.addEventListener('click', () => {
      $('#bgColor').value = btn.dataset.bg;
      $('#bgColorText').value = btn.dataset.bg;
    });
  });

  $('#btnRefreshDevices').addEventListener('click', listDevices);
  $('#btnSave').addEventListener('click', saveSettings);

  buildSlots();
  loadConfig().then(listDevices);
  setInterval(pollDebug, 200);
})();
