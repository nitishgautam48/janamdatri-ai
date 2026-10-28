/* Janamdatri-AI API client used by app.dc.html.
 * Same-origin by default (the backend serves /api/v1); set window.JD_API_BASE to point elsewhere. */
(function () {
  var BASE = (window.JD_API_BASE || '') + '/api/v1';
  var KEY = 'jd-session';

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY) || 'null'); } catch (e) { return null; }
  }
  function save(s) {
    try { s ? localStorage.setItem(KEY, JSON.stringify(s)) : localStorage.removeItem(KEY); } catch (e) {}
  }

  var session = load();

  function setSession(tokens) {
    session = tokens ? {
      access: tokens.access_token, refresh: tokens.refresh_token || null,
      role: tokens.role, user: tokens.user || null
    } : null;
    save(session);
    return session;
  }

  // One refresh at a time. Each refresh token works once, so two requests that both find the access token
  // expired must share one refresh; a second refresh with the same token would be refused and log her out.
  var refreshing = null;
  function sameUser(a, b) {
    return a && b && a.role === b.role && (!a.user || !b.user || a.user.id === b.user.id);
  }
  async function doRefresh() {
    if (!session || !session.refresh) return false;
    var stored = load();
    // Another tab of the app may already have used this refresh token: take its new tokens.
    if (stored && stored.refresh && stored.refresh !== session.refresh && sameUser(stored, session)) {
      session = stored;
      return true;
    }
    var mine = session.refresh, r;
    try {
      r = await fetch(BASE + '/auth/refresh', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: mine })
      });
    } catch (e) { return false; }  // offline: keep the login, the request just fails
    if (!r.ok) {
      stored = load();
      if (stored && stored.refresh && stored.refresh !== mine && sameUser(stored, session)) { session = stored; return true; }
      if (r.status === 401 || r.status === 403) {
        setSession(null);
        try { window.dispatchEvent(new CustomEvent('jd-session-expired')); } catch (e) {}
      }
      return false;
    }
    setSession(await r.json());
    return true;
  }
  function refresh() {
    if (!refreshing) refreshing = doRefresh().finally(function () { refreshing = null; });
    return refreshing;
  }
  // Keep tabs in step: a login refreshed in another tab replaces this tab's tokens (same person only).
  window.addEventListener('storage', function (e) {
    if (e.key !== KEY) return;
    var stored = load();
    if (stored && sameUser(stored, session)) session = stored;
  });

  async function req(method, path, body, opts) {
    opts = opts || {};
    var headers = {};
    var payload;
    if (body instanceof FormData) payload = body;
    else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
    var used = session && session.access;
    if (used && opts.auth !== false) headers.Authorization = 'Bearer ' + used;
    var r = await fetch(BASE + path, { method: method, headers: headers, body: payload });
    if (r.status === 401 && opts.auth !== false && !opts.retried && used &&
        ((session && session.access !== used) || await refresh())) {
      return req(method, path, body, Object.assign({}, opts, { retried: true }));
    }
    var data = null;
    var text = await r.text();
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
    if (!r.ok) {
      var err = new Error((data && data.error && data.error.message) || ('Request failed (' + r.status + ')'));
      err.status = r.status;
      err.code = data && data.error && data.error.code;
      err.details = data && data.error && data.error.details;
      throw err;
    }
    return data;
  }

  function pickFile(accept, capture) {
    return new Promise(function (resolve) {
      var input = document.createElement('input');
      input.type = 'file';
      input.accept = accept || '';
      if (capture) input.setAttribute('capture', capture);
      input.onchange = function () { resolve(input.files && input.files[0] || null); };
      input.click();
    });
  }

  /* Where is the phone? Resolves { lat, lon, accuracy } or { error } where error is one of
   * denied | unavailable | timeout | insecure | unsupported. Tries a quick network-based fix first
   * (works on laptops via Wi-Fi), then a precise GPS fix. */
  var lastFix = null;
  function locate(opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      if (!window.isSecureContext) return resolve({ error: 'insecure' });
      if (!navigator.geolocation) return resolve({ error: 'unsupported' });
      if (lastFix && Date.now() - lastFix.at < 120000 && !opts.fresh) return resolve(lastFix);
      var attempts = [
        { enableHighAccuracy: false, timeout: opts.quickTimeout || 8000, maximumAge: 300000 },
        { enableHighAccuracy: true, timeout: opts.timeout || 15000, maximumAge: 0 }
      ];
      var firstError = null;
      (function next(i) {
        if (i >= attempts.length) return resolve({ error: firstError || 'unavailable' });
        navigator.geolocation.getCurrentPosition(function (p) {
          lastFix = { lat: p.coords.latitude, lon: p.coords.longitude, accuracy: Math.round(p.coords.accuracy || 0), at: Date.now() };
          resolve(lastFix);
        }, function (err) {
          var code = err && err.code === 1 ? 'denied' : err && err.code === 3 ? 'timeout' : 'unavailable';
          firstError = firstError || code;
          if (code === 'denied') return resolve({ error: 'denied' });
          next(i + 1);
        }, attempts[i]);
      })(0);
    });
  }
  /* Older helper: coordinates or null. */
  function position(timeoutMs) {
    return locate({ quickTimeout: timeoutMs || 8000 }).then(function (r) { return r && r.lat != null ? r : null; });
  }

  /* Tap once to start recording, tap again to stop; resolves with an audio Blob. */
  var recorder = null;
  // Voice recording. Tap once to start, tap again to stop. With opts.onAutoStop it also stops by itself once she
  // pauses after speaking (like phone voice typing): the level of the microphone is measured every 100 ms; after
  // speech, ~1.6 s of quiet ends the recording. A short tone plays when listening starts and when it stops.
  // The start tone plays BEFORE recording begins, so the beep is not in the audio (Whisper turns a beep into
  // made-up words) and the room's noise level is measured on quiet, not on the beep.
  var SILENCE_MS = 1600, NO_SPEECH_MS = 8000, MAX_MS = 45000, TONE_MS = 120, TONE_GAP = 220;
  var MIC = { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 };
  function pickType() {
    var types = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4', 'audio/webm'];
    for (var i = 0; i < types.length; i++) {
      try { if (window.MediaRecorder.isTypeSupported(types[i])) return types[i]; } catch (e) { /* old browser */ }
    }
    return '';
  }
  function tone(ctx, freq, ms) {
    try {
      var o = ctx.createOscillator(), g = ctx.createGain();
      o.type = 'sine'; o.frequency.value = freq;
      g.gain.setValueAtTime(0.0001, ctx.currentTime);
      g.gain.exponentialRampToValueAtTime(0.18, ctx.currentTime + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + ms / 1000);
      o.connect(g); g.connect(ctx.destination); o.start(); o.stop(ctx.currentTime + ms / 1000 + 0.02);
    } catch (e) { /* no sound is fine */ }
  }
  function toggleRecording(opts) {
    if (recorder) {
      var r = recorder; recorder = null;
      if (r.state === 'inactive') { r._finish(); return Promise.resolve(new Blob([])); }  // tapped again during the tone
      return new Promise(function (resolve) { r._resolve = resolve; r.stop(); });
    }
    opts = opts || {};
    if (!navigator.mediaDevices || !window.MediaRecorder) return Promise.reject(new Error('Voice input is not supported in this browser'));
    return navigator.mediaDevices.getUserMedia({ audio: MIC }).catch(function () {
      return navigator.mediaDevices.getUserMedia({ audio: true });
    }).then(function (stream) {
      var chunks = [], ctx = null, timer = null, type = pickType();
      var mr = new MediaRecorder(stream, type ? { mimeType: type, audioBitsPerSecond: 64000 } : undefined);
      var AC = window.AudioContext || window.webkitAudioContext;
      try { ctx = AC ? new AC() : null; } catch (e) { ctx = null; }
      function finish() {
        clearInterval(timer);
        stream.getTracks().forEach(function (t) { t.stop(); });
        if (ctx) { tone(ctx, 620, 140); setTimeout(function () { try { ctx.close(); } catch (e) { /* closed */ } }, 300); }
      }
      mr._finish = finish;
      mr.ondataavailable = function (e) { if (e.data.size) chunks.push(e.data); };
      mr.onstop = function () {
        finish();
        var blob = new Blob(chunks, { type: (mr.mimeType || 'audio/webm').split(';')[0] });
        if (mr._resolve) mr._resolve(blob);
        else if (mr._auto === 'silence' && opts.onAutoStop) opts.onAutoStop(blob);
        else if (mr._auto === 'none' && opts.onNoSpeech) opts.onNoSpeech();
      };
      recorder = mr;
      var begin = function () {
        if (recorder !== mr) return;  // stopped during the tone
        mr.start(250);
        if (ctx && opts.onAutoStop) watch();
      };
      if (ctx) { tone(ctx, 880, TONE_MS); setTimeout(begin, TONE_MS + TONE_GAP); } else begin();
      function watch() {
        var an = ctx.createAnalyser(); an.fftSize = 1024;
        ctx.createMediaStreamSource(stream).connect(an);
        var buf = new Float32Array(an.fftSize), began = Date.now(), floor = null, spoke = 0, quiet = 0;
        timer = setInterval(function () {
          an.getFloatTimeDomainData(buf);
          var sum = 0; for (var i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
          var level = Math.sqrt(sum / buf.length), now = Date.now();
          if (floor === null || (now - began < 400 && level < floor)) floor = level; // room noise in the first moment
          var talking = level > Math.max(0.015, (floor || 0) * 3);
          if (talking) { spoke += 100; quiet = 0; } else quiet += 100;
          var why = spoke >= 300 && quiet >= (opts.silenceMs || SILENCE_MS) ? 'silence' : spoke < 300 && now - began > NO_SPEECH_MS ? 'none'
            : now - began > (opts.maxMs || MAX_MS) ? 'silence' : null;
          if (why && recorder === mr) { recorder = null; mr._auto = why; mr.stop(); }
        }, 100);
      }
      return null; // recording started
    });
  }

  window.JD = {
    base: BASE,
    get session() { return session; },
    get role() { return session && session.role; },
    setSession: setSession,
    logout: function () {
      var s = session;
      setSession(null);
      if (s && s.access) {
        fetch(BASE + '/auth/logout', { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + s.access },
          body: JSON.stringify({ refresh_token: s.refresh }) }).catch(function () {});
      }
    },
    get: function (p, o) { return req('GET', p, undefined, o); },
    post: function (p, b, o) { return req('POST', p, b === undefined ? {} : b, o); },
    put: function (p, b, o) { return req('PUT', p, b, o); },
    patch: function (p, b, o) { return req('PATCH', p, b, o); },
    del: function (p, o) { return req('DELETE', p, undefined, o); },
    pickFile: pickFile,
    position: position,
    locate: locate,
    toggleRecording: toggleRecording,
    get recording() { return !!recorder; }
  };
})();
