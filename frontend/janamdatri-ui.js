/* Janamdatri-AI · language (हिंदी / English) and theme (dark / light) for the app and landing page.
 *
 * The design files are bilingual ("Verify · आगे बढ़ें", an English title over a Hindi subtitle). This script
 * shows them in ONE language: bilingual labels are split, Hindi subtitles under English titles become the
 * main text (Hindi) or are hidden (English), and remaining English strings use the dictionary in
 * janamdatri-i18n.js. The light theme recolours the dark design with the palette of the design's light variant.
 * Both choices are remembered on this device; window.JDUI exposes them to the app logic.
 */
(function () {
  var KEY_LANG = 'jd-lang', KEY_THEME = 'jd-theme';
  var SHOW_PILL = !!(document.currentScript && document.currentScript.hasAttribute('data-lang-pill'));
  var DEV = /[ऀ-ॿ]/;
  var LATIN_WORD = /[A-Za-z]{2,}/g;
  var ACRONYM = /^(BP|SOS|OTP|ASHA|PHC|CHC|ANC|IFA|EDD|LMP|Hb|CBC|PIN|SMS|GPS|PDF|RCH|ANM|DH|SC|JSY|PMMVY|JSSK|PMSMA|ID|kg|wk|AM|PM|OK)$/;

  function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }
  function initialLang() {
    var s = store(KEY_LANG);
    if (s === 'hi' || s === 'en') return s;
    return /^hi/i.test(navigator.language || '') ? 'hi' : 'en';
  }
  var state = { lang: initialLang(), theme: store(KEY_THEME) === 'light' ? 'light' : 'dark' };
  var listeners = [];

  // ------------------------------------------------------------------ classification
  function isHindi(t) {
    if (!DEV.test(t)) return false;
    var words = t.match(LATIN_WORD) || [];
    return words.every(function (w) { return ACRONYM.test(w); });
  }
  function isEnglish(t) { return !DEV.test(t) && /[A-Za-z]/.test(t); }

  function dict() { return window.JD_I18N || { hi: {}, en: {}, patterns: [] }; }

  function translateCore(core, lang) {
    if (!core) return core;
    // bilingual label: "English · हिंदी" (either order, any number of parts)
    if (core.indexOf(' · ') > -1) {
      var parts = core.split(' · ');
      var hi = parts.filter(isHindi), en = parts.filter(function (p) { return !isHindi(p); });
      if (hi.length && en.some(isEnglish)) {
        var pick = lang === 'hi' ? hi : en;
        return pick.map(function (p) { return lang === 'hi' ? p : translateCore(p, lang); }).join(' · ');
      }
    }
    var D = dict();
    if (lang === 'hi' && D.hi[core]) return D.hi[core];
    if (lang === 'en' && D.en[core]) return D.en[core];
    if (lang === 'hi' && isEnglish(core)) {
      for (var i = 0; i < (D.patterns || []).length; i++) {
        var m = core.match(D.patterns[i][0]);
        if (m) return core.replace(D.patterns[i][0], D.patterns[i][1]);
      }
    }
    if (lang === 'en' && isHindi(core) && D.en[core]) return D.en[core];
    return core;
  }
  function translate(text, lang) {
    var m = /^(\s*)([\s\S]*?)(\s*)$/.exec(text);
    var out = translateCore(m[2], lang);
    return out === m[2] ? text : m[1] + out + m[3];
  }

  // ------------------------------------------------------------------ DOM pass
  var memo = new WeakMap();   // text node -> { orig, applied }
  var phMemo = new WeakMap(); // input -> { orig, applied }
  var observer = null, scheduled = false;

  function skip(el) {
    for (var e = el; e && e !== document.body; e = e.parentElement) {
      // X-DC holds the template source the runtime compiles; only the rendered copy (#dc-root) is translated.
      if (e.nodeType === 1 && (e.classList.contains('jd-keep') || e.tagName === 'X-DC' || e.tagName === 'SCRIPT' || e.tagName === 'STYLE' ||
          e.tagName === 'TEXTAREA' || e.isContentEditable)) return true;
    }
    return false;
  }
  function origOf(node) {
    var r = memo.get(node);
    if (!r || node.nodeValue !== r.applied) { r = { orig: node.nodeValue, applied: node.nodeValue }; memo.set(node, r); }
    return r;
  }
  function setText(node, value) {
    var r = origOf(node);
    if (node.nodeValue !== value) node.nodeValue = value;
    r.applied = value;
  }
  // the only text of an element made of a single text node (ignoring whitespace); the runtime wraps
  // {{ bindings }} in <span class="sc-interp">, which is looked through
  function soleText(el) {
    var t = null;
    for (var c = el.firstChild; c; c = c.nextSibling) {
      var found = null;
      if (c.nodeType === 3) { if (c.nodeValue.trim()) found = c; }
      else if (c.nodeType === 1 && c.classList.contains('sc-interp')) { found = soleText(c); if (!found && c.textContent.trim()) return null; }
      else if (c.nodeType === 1 && c.tagName !== 'BR') return null;
      if (found) { if (t) return null; t = found; }
    }
    return t;
  }
  // the element that represents a text node visually (outside any sc-interp wrappers)
  function boxOf(node) {
    var el = node.parentElement;
    while (el && el.classList.contains('sc-interp') && el.parentElement && soleText(el.parentElement) === node) el = el.parentElement;
    return el;
  }
  function prevElement(el) {
    var p = el.previousSibling;
    while (p && ((p.nodeType === 3 && !p.nodeValue.trim()) || (p.nodeType === 1 && p.tagName === 'BR'))) p = p.previousSibling;
    return p && p.nodeType === 1 ? p : null;
  }

  function pass() {
    scheduled = false;
    var root = document.body;
    if (!root) return;
    if (observer) observer.disconnect();
    var lang = state.lang;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    var nodes = [];
    for (var n = walker.nextNode(); n; n = walker.nextNode()) if (n.nodeValue.trim()) nodes.push(n);

    // 1. per-node translation from the original text
    nodes.forEach(function (node) {
      if (skip(node.parentElement)) return;
      var r = origOf(node);
      setText(node, translate(r.orig, lang));
    });

    // 2. title + subtitle twins: an English element followed by a Hindi-only element
    var hidden = root.querySelectorAll('[data-jd-twin]');
    for (var i = 0; i < hidden.length; i++) hidden[i].removeAttribute('data-jd-twin');
    nodes.forEach(function (node) {
      var el = boxOf(node);
      if (!el || skip(el) || soleText(el) !== node) return;
      var r = memo.get(node);
      if (!r || !isHindi(r.orig.trim())) return;
      var prev = prevElement(el);
      if (!prev || skip(prev)) return;
      var pt = soleText(prev);
      if (!pt) return;
      var pr = memo.get(pt);
      if (!pr || !isEnglish(pr.orig.trim()) || pr.orig.indexOf(' · ') > -1) return;
      if (lang === 'en') { el.setAttribute('data-jd-twin', ''); return; }
      // Hindi: the Hindi line becomes the title unless the dictionary already translated it
      if (pt.nodeValue === pr.orig) setText(pt, pr.orig.replace(pr.orig.trim(), r.orig.trim()));
      el.setAttribute('data-jd-twin', '');
    });

    // 3. placeholders
    var inputs = root.querySelectorAll('input[placeholder], textarea[placeholder]');
    for (var j = 0; j < inputs.length; j++) {
      var inp = inputs[j];
      if (skip(inp.parentElement)) continue;
      var pm = phMemo.get(inp), cur = inp.getAttribute('placeholder');
      if (!pm || cur !== pm.applied) { pm = { orig: cur, applied: cur }; phMemo.set(inp, pm); }
      var v = translate(pm.orig, lang);
      if (v !== cur) inp.setAttribute('placeholder', v);
      pm.applied = v;
    }
    document.documentElement.lang = lang === 'hi' ? 'hi' : 'en';
    if (observer) observer.observe(root, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['placeholder'] });
  }
  function schedule() { if (!scheduled) { scheduled = true; requestAnimationFrame(pass); } }

  // ------------------------------------------------------------------ theme
  var LIGHT = {
    bg: [['#0B1020', '#FAF8F5'], ['#111827', '#FFFFFF'], ['#1F2937', '#EFEAE3'], ['#0D1326', '#FFFFFF'], ['#0F172A', '#E7E2DA'],
         ['#1A0A0D', '#FFF5F5'], ['#120E1F', '#FBF6FC'], ['#1F1830', '#FFFFFF'], ['#3B1D4A', '#C026D3'], ['#F8FAFC', '#1C1917'],
         ['#E879F9', '#C026D3'], ['#334155', '#D6D3D1'], ['#2A1216', '#FFFFFF'], ['#24111A', '#FFFFFF']],
    color: [['#F8FAFC', '#1C1917'], ['#94A3B8', '#57534E'], ['#64748B', '#78716C'], ['#CBD5E1', '#44403C'], ['#475569', '#A8A29E'],
            ['#5EEAD4', '#0F766E'], ['#99F6E4', '#0F766E'], ['#86EFAC', '#15803D'], ['#FCA5A5', '#B91C1C'], ['#FECACA', '#991B1B'],
            ['#FACC15', '#A16207'], ['#FDE68A', '#854D0E'], ['#FDBA74', '#C2410C'], ['#E879F9', '#86198F'], ['#F472B6', '#BE185D'],
            ['#8B7A99', '#7E5A8C'], ['#0B1020', '#FAF8F5'], ['#F87171', '#DC2626'], ['#2A0B33', '#FFFFFF']],
    border: [['#334155', '#D6D3D1'], ['#475569', '#A8A29E'], ['#1F2937', '#E7E2DA']]
  };
  function rgb(hex) {
    var n = parseInt(hex.slice(1), 16);
    return 'rgb(' + (n >> 16 & 255) + ', ' + (n >> 8 & 255) + ', ' + (n & 255) + ')';
  }
  function themeCss() {
    var P = 'html[data-theme="light"] ';
    var S = ':is(.jd-phone, .jd-phone *)';
    var css = [];
    LIGHT.bg.forEach(function (p) { css.push(P + S + '[style*="background: ' + rgb(p[0]) + '"]{background:' + p[1] + '!important}'); });
    LIGHT.color.forEach(function (p) {
      var c = rgb(p[0]);
      css.push(P + S + '[style^="color: ' + c + '"],' + P + S + '[style*="; color: ' + c + '"]{color:' + p[1] + '!important}');
    });
    LIGHT.border.forEach(function (p) { css.push(P + S + '[style*="solid ' + rgb(p[0]) + '"]{border-color:' + p[1] + '!important}'); });
    var onDark = ['#0F766E', '#14B8A6', '#DC2626', '#C026D3', '#16A34A'].map(function (h) { return P + S + '[style*="background: ' + rgb(h) + '"]'; }).join(',');
    css.push(onDark + '{color:#FFFFFF!important}');
    css.push(P + S + '[style*="background: ' + rgb('#0F766E') + '"] *:not([style*="background:"]){color:inherit!important}');
    css.push(P + S + '[style*="background: ' + rgb('#F8FAFC') + '"]{color:#FAF8F5!important}');
    // white elements inside teal cards (progress fills, chips) stay white
    css.push(P + '.jd-phone [style*="background: ' + rgb('#0F766E') + '"] [style*="background: ' + rgb('#F8FAFC') + '"]{background:#F8FAFC!important}');
    // icons that keep their own colour on a white circle inside a teal card (the Plan My Day mic), in both themes
    css.push('.jd-map-dark .leaflet-tile-pane{filter:invert(1) hue-rotate(180deg) brightness(.92) contrast(.88) saturate(.7)}');
    css.push('.jd-phone i.jd-ink-teal.jd-ink-teal.jd-ink-teal.jd-ink-teal{color:#0F766E!important}');
    css.push('.jd-phone i.jd-ink-red.jd-ink-red.jd-ink-red.jd-ink-red{color:#DC2626!important}');
    css.push(P + '.jd-phone{color:#1C1917;box-shadow:0 30px 60px -30px rgba(0,0,0,.25)!important}');
    css.push(P + '.jd-phone [style*="background: rgb(17, 24, 39)"],' + P + '.jd-phone [style*="background: rgb(31, 24, 48)"]{box-shadow:inset 0 0 0 1px #ECE6DD}');
    css.push(P + '.jd-phone input,' + P + '.jd-phone textarea{color:#1C1917!important;color-scheme:light}');
    css.push(P + '.jd-phone [style*="rgba(11, 16, 32, 0.94)"]{background:rgba(250,248,245,.97)!important}');
    css.push(P + '.jd-phone .jd-paper{box-shadow:inset 0 0 0 1px #E7E2DA}');
    css.push(P + '.jd-phone .jd-paper, ' + P + '.jd-phone .jd-paper *{color:#0B1020}');
    css.push(P + '.jd-phone .jd-paper{background:#FFFFFF!important}');
    css.push('@media (max-width:600px){html[data-theme="light"] body{background:#FAF8F5!important}}');
    css.push('[data-jd-twin]{display:none!important}');
    return css.join('\n');
  }
  function applyTheme() {
    document.documentElement.setAttribute('data-theme', state.theme);
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', state.theme === 'light' ? '#FAF8F5' : '#0B1020');
  }

  // ------------------------------------------------------------------ landing-page language pill
  function landingPill() {
    if (!SHOW_PILL || document.getElementById('jd-lang-pill')) return;
    var pill = document.createElement('div');
    pill.id = 'jd-lang-pill';
    pill.className = 'jd-keep';
    pill.setAttribute('style', 'position:fixed;left:16px;bottom:16px;z-index:9999;display:flex;padding:4px;border-radius:999px;background:#0B1020;box-shadow:0 8px 24px rgba(0,0,0,.25);font:600 14px Mukta,system-ui,sans-serif');
    [['hi', 'हिंदी'], ['en', 'English']].forEach(function (o) {
      var b = document.createElement('span');
      b.textContent = o[1];
      b.setAttribute('role', 'button');
      b.style.cssText = 'cursor:pointer;padding:6px 12px;border-radius:999px';
      b.onclick = function () { api.setLang(o[0]); };
      b.dataset.lang = o[0];
      pill.appendChild(b);
    });
    document.body.appendChild(pill);
    paintPill();
  }
  function paintPill() {
    var pill = document.getElementById('jd-lang-pill');
    if (!pill) return;
    Array.prototype.forEach.call(pill.children, function (b) {
      var on = b.dataset.lang === state.lang;
      b.style.background = on ? '#0F766E' : 'transparent';
      b.style.color = on ? '#F8FAFC' : '#94A3B8';
    });
  }

  var api = {
    get lang() { return state.lang; },
    get theme() { return state.theme; },
    t: function (text) { return translate(text, state.lang); },
    pick: function (en, hi) { return state.lang === 'hi' && hi ? hi : en; },
    setLang: function (l) {
      if (l !== 'hi' && l !== 'en') return;
      state.lang = l; store(KEY_LANG, l); paintPill(); schedule();
      listeners.forEach(function (f) { try { f(state); } catch (e) {} });
    },
    setTheme: function (t) {
      state.theme = t === 'light' ? 'light' : 'dark'; store(KEY_THEME, state.theme); applyTheme();
      listeners.forEach(function (f) { try { f(state); } catch (e) {} });
    },
    onChange: function (f) { listeners.push(f); },
    refresh: schedule
  };
  window.JDUI = api;

  applyTheme();
  var style = document.createElement('style');
  style.textContent = themeCss();
  (document.head || document.documentElement).appendChild(style);

  function start() {
    observer = new MutationObserver(schedule);
    landingPill();
    pass();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
