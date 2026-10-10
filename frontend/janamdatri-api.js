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

  async function refresh() {
    if (!session || !session.refresh) return false;
    var r = await fetch(BASE + '/auth/refresh', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: session.refresh })
    });
    if (!r.ok) { setSession(null); return false; }
    setSession(await r.json());
    return true;
  }

  async function req(method, path, body, opts) {
    opts = opts || {};
    var headers = {};
    var payload;
    if (body instanceof FormData) payload = body;
    else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
    if (session && session.access && opts.auth !== false) headers.Authorization = 'Bearer ' + session.access;
    var r = await fetch(BASE + path, { method: method, headers: headers, body: payload });
    if (r.status === 401 && opts.auth !== false && !opts.retried && await refresh()) {
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
  function toggleRecording() {
    if (recorder) {
      var r = recorder; recorder = null;
      return new Promise(function (resolve) { r._resolve = resolve; r.stop(); });
    }
    if (!navigator.mediaDevices || !window.MediaRecorder) return Promise.reject(new Error('Voice input is not supported in this browser'));
    return navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      var chunks = [];
      var mr = new MediaRecorder(stream);
      mr.ondataavailable = function (e) { if (e.data.size) chunks.push(e.data); };
      mr.onstop = function () {
        stream.getTracks().forEach(function (t) { t.stop(); });
        var type = (mr.mimeType || 'audio/webm').split(';')[0];
        mr._resolve && mr._resolve(new Blob(chunks, { type: type }));
      };
      mr.start();
      recorder = mr;
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
