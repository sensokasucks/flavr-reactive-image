/**
 * Avatar page – audio reactive engine + debug HUD
 *
 * ARCHIVED Node version (see legacy/README). Runs in renderer/avatar.html, the window
 * captured in XSplit/OBS. Reads settings from GET /api/config (polled every 2.5 s), listens
 * to the mic, picks one of the uploaded state images by loudness and crossfades between
 * two <img> elements (imgA/imgB). Volume is 0..100; thresholds come from config.audio.
 */
(() => {
  const imgA = document.getElementById('imgA');
  const imgB = document.getElementById('imgB');
  const hud = document.getElementById('hud');
  const hudTier = document.getElementById('hudTier');
  const hudBar = document.getElementById('hudBar');
  const hudVol = document.getElementById('hudVol');
  const hudDetail = document.getElementById('hudDetail');
  const hudMarkSpeak = document.getElementById('hudMarkSpeak');
  const hudMarkLoud = document.getElementById('hudMarkLoud');
  const btnSettings = document.getElementById('btnSettings');

  let config = null;
  let audioCtx = null;
  let analyser = null;
  let micStream = null;
  let dataArray = null;

  let activeIsA = true;
  let currentKey = null;
  let transitioning = false;

  let smoothVol = 0;
  let isSpeaking = false;
  let isBlinking = false;
  let blinkTimeout = null;
  let nextBlinkAt = 0;
  let lastBounceAt = 0;
  let wasSpeaking = false;
  let lastTier = 'idle';

  // Pushes config.display/effects values into CSS variables used by avatar.html's styles.
  function applyDisplay() {
    if (!config) return;
    const bg = config.display?.backgroundColor || '#00FF00';
    document.documentElement.style.setProperty('--bg', bg);
    document.body.style.background = bg;
    document.documentElement.style.setProperty('--max-w', (config.display.maxWidth || 480) + 'px');
    document.documentElement.style.setProperty('--max-h', (config.display.maxHeight || 480) + 'px');
    document.documentElement.style.setProperty('--fade', (config.effects.crossfadeMs || 120) + 'ms');
    document.documentElement.style.setProperty('--dim', String(config.effects.dimOpacity ?? 0.72));
    document.documentElement.style.setProperty('--bounce-dur', (config.effects.bounceDuration || 280) + 'ms');

    if (config.display.showDebugHud) hud.classList.remove('hidden');
    else hud.classList.add('hidden');

    hudMarkSpeak.style.left = (config.audio?.threshold ?? 18) + '%';
    hudMarkLoud.style.left = (config.audio?.loudThreshold ?? 45) + '%';
  }

  function imageUrl(slot) {
    const name = config?.images?.[slot];
    return name ? '/uploads/' + name : null;
  }

  // 0 at the speak threshold, 1 at the loud threshold or above.
  function volumeIntensity(vol) {
    const t = config?.audio?.threshold ?? 18;
    const loud = Math.max(t + 1, config?.audio?.loudThreshold ?? 45);
    if (vol <= t) return 0;
    return Math.min(1, (vol - t) / (loud - t));
  }

  // Picks the image slot for this volume. Optional slots (soft, loud, blink, muted) are
  // used only if an image was uploaded; otherwise it falls back to the nearest basic one.
  // Keep the tier rules in sync with tierName() below.
  function resolveKey(vol, blinking, muted) {
    if (muted && imageUrl('muted')) return 'muted';
    const t = config?.audio?.threshold ?? 18;
    const loud = Math.max(t + 1, config?.audio?.loudThreshold ?? 45);
    const softRatio = config?.audio?.softRatio ?? 0.45;

    if (vol < t) {
      if (blinking && imageUrl('idleBlink')) return 'idleBlink';
      return imageUrl('idle') || imageUrl('speaking') || null;
    }
    if (vol >= loud && imageUrl('speakingLoud')) return 'speakingLoud';
    if (imageUrl('speakingSoft')) {
      const softEnd = t + (loud - t) * softRatio;
      if (vol < softEnd) return 'speakingSoft';
    }
    if (blinking && imageUrl('speakingBlink')) return 'speakingBlink';
    return imageUrl('speaking') || imageUrl('speakingSoft') || imageUrl('speakingLoud') || imageUrl('idle') || null;
  }

  // Label shown in the HUD and sent to /api/debug: idle, soft, speak, loud, blink or muted.
  function tierName(vol, key) {
    if (key === 'muted') return 'muted';
    if (key === 'idleBlink' || key === 'speakingBlink') return 'blink';
    const t = config?.audio?.threshold ?? 18;
    const loud = Math.max(t + 1, config?.audio?.loudThreshold ?? 45);
    if (vol < t) return 'idle';
    if (vol >= loud && config?.images?.speakingLoud) return 'loud';
    if (config?.images?.speakingSoft) {
      const softEnd = t + (loud - t) * (config?.audio?.softRatio ?? 0.45);
      if (vol < softEnd) return 'soft';
    }
    return 'speak';
  }

  // Bounce height in px; grows from bounceStrength to bounceStrengthMax with volume if enabled.
  function bouncePxForVolume(vol) {
    const base = config?.effects?.bounceStrength ?? 12;
    const max = config?.effects?.bounceStrengthMax ?? 28;
    if (!config?.effects?.bounceScaleWithVolume) return base;
    return base + (max - base) * volumeIntensity(vol);
  }

  function finishTransitionImmediate() {
    const active = activeIsA ? imgA : imgB;
    const inactive = activeIsA ? imgB : imgA;
    active.classList.add('visible');
    inactive.classList.remove('visible', 'dim', 'bounce');
    active.style.zIndex = '';
    inactive.style.zIndex = '';
    active.style.opacity = '';
    inactive.style.opacity = '';
    transitioning = false;
  }

  // Crossfade to slot `key`: load it into the hidden <img>, then fade it in over the old one.
  // The hidden image is decoded before showing so the swap does not flash blank.
  function showImage(key, force, volForBounce) {
    if (!key || (key === currentKey && !force)) return;
    const url = imageUrl(key);
    if (!url) return;

    if (transitioning) finishTransitionImmediate();

    const incoming = activeIsA ? imgB : imgA;
    const outgoing = activeIsA ? imgA : imgB;
    const wantsDim = !!(config?.effects?.dimIdle && (key === 'idle' || key === 'idleBlink'));
    const enteringSpeak = ['speaking', 'speakingSoft', 'speakingLoud', 'speakingBlink'].includes(key);
    const wasSpeak = ['speaking', 'speakingSoft', 'speakingLoud', 'speakingBlink'].includes(currentKey);
    const shouldBounce = config?.effects?.bounce && enteringSpeak && !wasSpeak;

    const applyIncomingClasses = () => {
      incoming.classList.remove('visible', 'dim', 'bounce');
      incoming.style.opacity = '0';
      incoming.style.zIndex = '2';
      outgoing.style.zIndex = '1';
      // Reading offsetWidth forces a reflow so the class changes below restart the CSS transition.
      void incoming.offsetWidth;

      incoming.classList.add('visible');
      if (wantsDim) incoming.classList.add('dim');
      else incoming.classList.remove('dim');
      incoming.style.opacity = '';

      if (shouldBounce) {
        const now = performance.now();
        if (now - lastBounceAt > 160) {
          document.documentElement.style.setProperty('--bounce-y', `-${bouncePxForVolume(volForBounce ?? smoothVol)}px`);
          incoming.classList.remove('bounce');
          void incoming.offsetWidth;
          incoming.classList.add('bounce');
          lastBounceAt = now;
          setTimeout(() => incoming.classList.remove('bounce'), (config.effects.bounceDuration || 280) + 20);
        }
      }

      transitioning = true;
      activeIsA = !activeIsA;
      currentKey = key;

      const fadeMs = config?.effects?.crossfadeMs ?? 120;
      const onEnd = (e) => {
        if (e && e.propertyName && e.propertyName !== 'opacity') return;
        incoming.removeEventListener('transitionend', onEnd);
        outgoing.classList.remove('visible', 'dim', 'bounce');
        outgoing.style.zIndex = '';
        incoming.style.zIndex = '';
        transitioning = false;
      };
      // Fallback in case transitionend never fires, so the swap always finishes.
      const safety = setTimeout(() => {
        incoming.removeEventListener('transitionend', onEnd);
        onEnd();
      }, fadeMs + 80);
      if (fadeMs <= 0) {
        clearTimeout(safety);
        onEnd();
      } else {
        incoming.addEventListener('transitionend', (e) => {
          clearTimeout(safety);
          onEnd(e);
        }, { once: true });
      }
    };

    const currentSrc = incoming.getAttribute('src') || '';
    if (currentSrc.endsWith(url) || currentSrc === url) {
      applyIncomingClasses();
      return;
    }
    incoming.onload = () => {
      incoming.onload = null;
      if (incoming.decode) {
        incoming.decode().then(applyIncomingClasses).catch(applyIncomingClasses);
      } else {
        requestAnimationFrame(() => requestAnimationFrame(applyIncomingClasses));
      }
    };
    incoming.onerror = () => {
      incoming.onerror = null;
      transitioning = false;
    };
    incoming.src = url;
  }

  function updateVisual(force) {
    if (!config) return;
    const key = resolveKey(smoothVol, isBlinking, !!config.forceMuted);
    showImage(key, force, smoothVol);
  }

  // While speaking, lifts the image up to liveIntensityMax px in proportion to volume.
  function applyLiveIntensity(vol) {
    const active = activeIsA ? imgA : imgB;
    if (!config?.effects?.liveIntensity || !isSpeaking) {
      if (!active.classList.contains('bounce')) active.style.transform = '';
      return;
    }
    const y = -((config.effects.liveIntensityMax ?? 8) * volumeIntensity(vol));
    if (!active.classList.contains('bounce')) active.style.transform = `translateY(${y}px)`;
  }

  // Updates the on-avatar HUD and posts the same numbers to the server so the
  // settings page meter can show them (it polls GET /api/debug). Runs every frame.
  function updateHud(vol, key) {
    const tier = tierName(vol, key);
    hudTier.textContent = tier;
    hudTier.className = 'hud-value tier-' + tier;
    const pct = Math.round(vol);
    hudVol.textContent = pct + '%';
    hudBar.style.width = pct + '%';
    const t = config?.audio?.threshold ?? 18;
    const loud = config?.audio?.loudThreshold ?? 45;
    if (pct >= loud) hudBar.style.background = 'linear-gradient(90deg, #5ce1b0, #ffb86c)';
    else if (pct >= t) hudBar.style.background = 'linear-gradient(90deg, #5ce1b0, #6c8cff)';
    else hudBar.style.background = 'linear-gradient(90deg, #3a4250, #5a6478)';

    const parts = [];
    if (key) parts.push(key);
    if (isBlinking) parts.push('blink');
    if (config?.forceMuted) parts.push('force-mute');
    hudDetail.textContent = parts.join(' · ') || '—';

    // push to server for settings panel
    fetch('/api/debug', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        vol: pct,
        tier,
        key: key || null,
        speaking: isSpeaking,
        blinking: isBlinking,
        threshold: t,
        loudThreshold: loud
      })
    }).catch(() => {});
  }

  // Next blink at a random time between blinkMinInterval and blinkMaxInterval seconds.
  function scheduleBlink() {
    if (!config?.effects?.blinkEnabled) return;
    const min = (config.effects.blinkMinInterval || 2.5) * 1000;
    const max = (config.effects.blinkMaxInterval || 6.5) * 1000;
    nextBlinkAt = performance.now() + min + Math.random() * (max - min);
  }

  function tickBlink(now) {
    if (!config?.effects?.blinkEnabled) {
      isBlinking = false;
      return;
    }
    if (!nextBlinkAt) scheduleBlink();
    if (now >= nextBlinkAt && !isBlinking) {
      isBlinking = true;
      updateVisual();
      clearTimeout(blinkTimeout);
      blinkTimeout = setTimeout(() => {
        isBlinking = false;
        updateVisual();
        scheduleBlink();
      }, config.effects.blinkDuration || 160);
    }
  }

  // Opens the mic chosen in settings with browser processing off so the level is raw.
  // Called again whenever the config changes, to switch device.
  async function startAudio() {
    try {
      if (micStream) micStream.getTracks().forEach((t) => t.stop());
      const deviceId = config?.audio?.deviceId;
      const constraints =
        deviceId && deviceId !== 'default'
          ? { audio: { deviceId: { exact: deviceId }, echoCancellation: false, noiseSuppression: false, autoGainControl: false } }
          : { audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } };
      micStream = await navigator.mediaDevices.getUserMedia(constraints);
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === 'suspended') await audioCtx.resume();
      const source = audioCtx.createMediaStreamSource(micStream);
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = 0.3;
      source.connect(analyser);
      dataArray = new Uint8Array(analyser.fftSize);
      requestAnimationFrame(audioLoop);
      return true;
    } catch (e) {
      console.error('mic failed', e);
      hudDetail.textContent = 'mic denied – allow mic access';
      return false;
    }
  }

  // Per-frame loop: RMS level of the mic, scaled to 0..100 by sensitivity, then smoothed
  // (config.audio.smoothing, 0..1, higher is slower). The image only changes when the tier does.
  function audioLoop(now) {
    if (!analyser || !dataArray) {
      requestAnimationFrame(audioLoop);
      return;
    }
    analyser.getByteTimeDomainData(dataArray);
    let sum = 0;
    for (let i = 0; i < dataArray.length; i++) {
      const v = (dataArray[i] - 128) / 128;
      sum += v * v;
    }
    const rms = Math.sqrt(sum / dataArray.length);
    const vol = Math.min(100, rms * 190 * (config?.audio?.sensitivity || 1));
    const smooth = config?.audio?.smoothing ?? 0.55;
    smoothVol = smoothVol * smooth + vol * (1 - smooth);

    const thresh = config?.audio?.threshold ?? 18;
    isSpeaking = smoothVol >= thresh;
    const tier = tierName(smoothVol, currentKey);

    if (isSpeaking !== wasSpeaking || tier !== lastTier) {
      wasSpeaking = isSpeaking;
      lastTier = tier;
      updateVisual();
    }

    applyLiveIntensity(smoothVol);
    tickBlink(now);
    updateHud(smoothVol, currentKey);
    requestAnimationFrame(audioLoop);
  }

  async function fetchConfig() {
    const res = await fetch('/api/config?t=' + Date.now());
    config = await res.json();
    applyDisplay();
  }

  btnSettings.addEventListener('click', () => {
    window.open('/settings.html', 'reactive-settings');
  });

  // click anywhere first time to unlock audio if browser blocked it
  document.body.addEventListener(
    'click',
    () => {
      if (audioCtx && audioCtx.state === 'suspended') audioCtx.resume();
      if (!micStream) startAudio();
    },
    { once: true }
  );

  async function boot() {
    await fetchConfig();
    updateVisual(true);
    await startAudio();
    // No push channel from the server, so poll the config and redraw when it changes.
    setInterval(async () => {
      try {
        const prev = JSON.stringify(config);
        await fetchConfig();
        if (JSON.stringify(config) !== prev) {
          currentKey = null;
          updateVisual(true);
          startAudio();
        }
      } catch (_) {}
    }, 2500);
  }

  if (document.readyState === 'complete') boot();
  else window.addEventListener('load', boot);
})();
