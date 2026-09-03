// toolAssisted.run shared client runtime and compatibility entrypoint.
// A real file the generator ships verbatim, except two build-time
// substitutions (the accepted video platforms, host list and display
// names, both from archivist/providers.py).
// It talks to the backend (the archivist) only through its JSON API,
// and reads page data from embedded application/json blobs.
window.TARApp = window.TARApp || {};
  var T = window.TAR || {};
  export var api = T.api, rel = T.rel || '';
  export var versionQuery = '?v=' + (T.v || '0');
  export var mePromise = fetch(api + '/api/me', {credentials: 'include'})
    .then(function(r){ return r.json(); })
    .catch(function(){ return {loggedIn: false, unreachable: true}; });
  // ---- view as: a Steering Committee member borrowing lesser eyes ----
  // Presentation only, per tab, gone when the tab closes: the page reveals
  // itself as if you held the chosen role and nothing more, while the
  // archivist keeps treating every request as you. Modes: '' (yourself),
  // expert (site-wide), editor, member, out (signed out).
  function viewAsMode(){
    try { return sessionStorage.getItem('tar-viewas') || ''; } catch (e) { return ''; }
  }
  var viewAsHonored = false;   // the wrap below decides whether the key is honored
  export function viewAsActive(){ return viewAsHonored ? viewAsMode() : ''; }
  // a page's covering-experts list, seen through the chosen eyes
  export function viewAsCoverage(list, who){
    var m = viewAsActive();
    if (!m) return list || [];
    list = (list || []).filter(function(n){ return n !== who; });
    if (m === 'expert') list = list.concat([who]);   // site scope covers everything
    return list;
  }
  // resolve who I am, then apply the view-as mask to the page's role lists
  mePromise = mePromise.then(function(d){
    var T = window.TAR || {};
    if (!d.loggedIn) return d;
    var who = (d.user || '').toLowerCase();
    T.viewasEligible = (T.committee || []).map(function(x){ return x.toLowerCase(); })
                       .indexOf(who) >= 0;
    var m = viewAsMode();
    if (!m) return d;
    if (!T.viewasEligible) {          // a stale key on lesser accounts is noise
      try { sessionStorage.removeItem('tar-viewas'); } catch (e) {}
      return d;
    }
    viewAsHonored = true;
    if (m === 'out') return {loggedIn: false, viewingAs: m};
    ['experts', 'editors', 'committee', 'founders'].forEach(function(k){
      T[k] = (T[k] || []).filter(function(n){ return n.toLowerCase() !== who; });
    });
    if (m === 'expert') T.experts = T.experts.concat([who]);
    if (m === 'editor') T.editors = T.editors.concat([who]);
    d.viewingAs = m;
    return d;
  });
  // the pill that says whose eyes these are, and the way back to your own
  mePromise.then(function(){
    var m = viewAsActive();
    if (!m) return;
    var labels = {expert: 'a site-wide expert', editor: 'an editor',
                  member: 'a plain member', out: 'signed out'};
    var pill = el('button', 'viewaspill',
                  'Viewing as ' + (labels[m] || m) + ' · back to yourself');
    pill.type = 'button';
    pill.addEventListener('click', function(){
      try { sessionStorage.removeItem('tar-viewas'); } catch (e) {}
      location.reload();
    });
    document.body.appendChild(pill);
  });

  // shared by every page: the submit preview, the news feed, anything that
  // puts text it did not write into the DOM
  export function escapeHtml(s){
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;').replace(/'/g, '&#x27;');
  }
  // build an element with a class and text content
  export function el(tag, cls, text){
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text) e.textContent = text;
    return e;
  }
  // ---- the mark beside a button: spinner while the archivist works, a
  // green check when it is done, a red cross (with the error as tooltip)
  // when it is not. Outcomes live next to the button that was pressed,
  // never in a box somewhere else on the page. ----
  function markOf(btn){
    if (!btn) return null;
    var m = btn.nextElementSibling;
    if (!m || !m.classList.contains('bmark')) {
      m = el('span', 'bmark');
      m.setAttribute('role', 'status');
      btn.insertAdjacentElement('afterend', m);
    }
    return m;
  }
  export function setMark(btn, state, title){
    var m = markOf(btn);
    if (!m) return false;
    m.className = 'bmark ' + state;
    m.title = title || '';
    m.setAttribute('aria-label', title || state);
    return true;
  }
  var lastBtn = null;   // the button whose call is being answered
  // a moved-out page module (the run page, the submit page) sets this
  // when it starts a call of its own, so note()/noteBuilt() still land
  // the outcome beside the button that was actually pressed
  export function setLastBtn(btn){ lastBtn = btn; }
  export var fileRowsOf = {};  // form id -> its file-rows widget (validity check)
  // a plain status line in a message box: for outcomes that carry more
  // than yes or no (an id, a link); everything else goes on the mark
  function noteText(box, text, good){
    box.hidden = false;
    box.textContent = text;
    box.className = 'actmsg ' + (good ? 'good' : 'bad');
  }
  export function noteHtml(box, good, lines){
    if (!box) return;
    box.hidden = false;
    box.innerHTML = (lines || []).filter(Boolean).join('<br>');
    box.className = 'actmsg ' + (good ? 'good' : 'bad');
  }
  function siteBaseUrl(){
    const defaultBaseUrl = 'https://toolassisted.run/';
    var href = (window.location && window.location.href) || (document.baseURI || defaultBaseUrl);
    try {
      return new URL(T.rel || '.', href).href;
    } catch (e) {
      return defaultBaseUrl;
    }
  }
  export function runPageUrl(runId){
    return new URL('runs/' + String(runId) + '/', siteBaseUrl()).href;
  }
  // an outcome: on the mark beside the button that asked, when there is
  // one; in the box otherwise
  export function note(box, text, good){
    if (lastBtn && setMark(lastBtn, good ? 'ok' : 'bad', text)) {
      // success is the green check alone; a refusal also says why, in
      // words a reader sees without hovering (issue #62)
      if (good) { if (box) box.hidden = true; return; }
    }
    if (box) noteText(box, text, good);
  }
  // A write is only done, for the reader, once the site serves it. Every
  // successful write answers with the archive revision it produced
  // (serial); assets/buildstamp.json carries the revision the served site
  // was built from. Poll the stamp until it catches up, then say so; if
  // the slow standby is serving instead, give up quietly after 30 s.
  export function waitBuilt(serial, cb){
    if (!serial) { cb(false); return; }
    const t0 = Date.now();
    (function poll(){
      fetch(rel + 'assets/buildstamp.json', {cache: 'no-store'})
        .then(function(r){ return r.ok ? r.json() : null; })
        .then(function(j){
          if (j && j.serial && j.serial >= serial) { cb(true); return; }
          if (Date.now() - t0 > 30000) { cb(false); return; }
          setTimeout(poll, 400);
        })
        .catch(function(){
          if (Date.now() - t0 > 30000) cb(false);
          else setTimeout(poll, 900);
        });
    })();
  }
  // say a write is done, then say when the rebuilt site is serving it:
  // the mark keeps spinning until the site serves the change, then checks;
  // keepText also writes the sentence (for outcomes that carry a link)
  export function noteBuilt(box, doneText, serial, liveText, keepText){
    var btn = lastBtn;
    var onMark = btn && setMark(btn, 'spin', doneText + ' Publishing to the site…');
    if (!onMark || keepText) noteText(box, doneText + ' Publishing to the site…', true);
    else if (box) box.hidden = true;
    waitBuilt(serial, function(live){
      var tail = live ? (liveText || 'It is live on the site now.') : 'It will appear on the site shortly.';
      if (onMark) setMark(btn, 'ok', doneText + ' ' + tail);
      if (!onMark || keepText) noteText(box, doneText + ' ' + tail, true);
    });
  }
  // the busy state of the button that started an archivist call
  export function busy(btn, on){
    // archivist work takes time (git pushes, mail): the button that started
    // it goes flat and grey and cannot be pressed again, and the mark
    // beside it spins until the answer comes
    if (!btn) return;
    btn.disabled = on;
    btn.classList.toggle('busy', on);
    if (on) setMark(btn, 'spin', 'Working…');
  }
  // the form's real submit button (for the busy spinner)
  // a checkbox that mirrors into a hidden field: forms whose answer must be
  // yes or no even when the box is left empty (a plain checkbox sends nothing)
  document.querySelectorAll('input.mirror').forEach(function(box){
    var hidden = box.form && box.form.querySelector('input[type=hidden][name=value]');
    if (!hidden) return;
    box.addEventListener('change', function(){
      hidden.value = box.checked ? box.dataset.yes : box.dataset.no;
    });
  });
  export function actionBtn(form){
    // the form's real submit button: never a chip's × or a helper button,
    // so the busy spinner lands on the button the member actually pressed
    return form.querySelector('button:not([type=button])') ||
           form.querySelector('button');
  }
  // POST a form to the archivist; resolves {ok, j} and never rejects
  export function post(path, fd, btn){
    busy(btn, true);
    return fetch(api + path, {method: 'POST', body: fd, credentials: 'include'})
      .then(function(r){ return r.json().then(function(j){ return {ok: r.ok, j: j}; }); })
      .catch(function(){ return {ok: false, j: {error: 'network error; the archivist may be unreachable'}}; })
      .then(function(res){
        busy(btn, false);
        lastBtn = btn;
        // the answer itself, on the mark; the caller's note() refines the
        // wording, and noteBuilt() keeps it spinning until the site serves it
        if (btn) setMark(btn, res.ok && res.j && res.j.ok ? 'ok' : 'bad',
                         res.ok && res.j && res.j.ok ? 'Done' : (res.j && res.j.error) || 'something went wrong');
        return res;
      });
  }

  // ---- freshness: Pages caches every page for 10 minutes ----
  // The site rebuilds ~40s after an act, but the browser keeps serving the
  // copy it has. Each page carries the build it came from (T.v); a tiny
  // uncached beacon says what is actually deployed, and a pill offers the
  // refresh when they differ. Never automatic: a reload is the reader's.
  setTimeout(function(){
    if (!T.v || T.v === '0') return;
    fetch(rel + 'assets/buildstamp.json', {cache: 'no-store'})
      .then(function(r){ return r.ok ? r.json() : null; })
      .then(function(j){
        if (!j || !j.v || j.v === T.v) return;
        var pill = el('button', 'freshpill',
                      'This page has been updated · Refresh');
        pill.type = 'button';
        pill.addEventListener('click', function(){ location.reload(); });
        document.body.appendChild(pill);
      }).catch(function(){});
  }, 1500);

  // ---- nav auth: account menu (avatar dropdown) ----
  function setTheme(t){
    try {
      if (t === 'system') { localStorage.removeItem('tar-theme'); delete document.documentElement.dataset.theme; }
      else { localStorage.setItem('tar-theme', t); document.documentElement.dataset.theme = t; }
    } catch (e) {}
    document.querySelectorAll('.am-theme button').forEach(function(b){
      b.classList.toggle('on', b.dataset.theme === t);
    });
  }
  // the nav: offline retry, the login link, the bell, and the account menu
  mePromise.then(function(d){
    // An archivist we cannot reach used to leave the nav simply empty, which
    // looks exactly like a broken page: say so instead, and offer the retry.
    var offlineNote = document.getElementById('navoffline');
    if (offlineNote && d.unreachable) {
      offlineNote.hidden = false;
      offlineNote.addEventListener('click', function(){
        offlineNote.textContent = 'retrying…';
        fetch(api + '/api/me', {credentials: 'include'})
          .then(function(r){ return r.json(); })
          .then(function(){ location.reload(); })
          .catch(function(){ offlineNote.textContent = 'still unreachable'; });
      });
    }
    var box = document.getElementById('navauth');
    if (!box || d.unreachable) return;
    if (!d.loggedIn) {
      var a = el('a', 'nl', 'Log in');
      a.href = api + '/login';
      box.appendChild(a);
      return;
    }
    // notification bell: news on your runs since your last profile visit
    var profileHref = rel + 'authors/' + encodeURIComponent(d.user.toLowerCase()) + '/';
    var bell = el('a', 'bell');
    bell.href = profileHref + '#news';
    bell.title = 'News on your runs';
    bell.innerHTML = '<svg viewBox="0 0 24 24"><path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6v-5c0-3.07-1.63-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.64 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z"/></svg>';
    box.appendChild(bell);
    var seenKey = 'tar-news-seen-' + d.user.toLowerCase();
    var slug = d.user.toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^[-.]+|[-.]+$/g, '');
    if (location.pathname.endsWith('/authors/' + slug + '/')) {
      try { localStorage.setItem(seenKey, new Date().toISOString().slice(0, 10)); } catch (e) {}
    } else {
      fetch(rel + 'assets/news.json' + versionQuery).then(function(r){ return r.json(); })
        .then(function(all){
          var dates = all[d.user.toLowerCase()] || [];
          var seen = '';
          try { seen = localStorage.getItem(seenKey) || ''; } catch (e) {}
          var fresh = dates.filter(function(dt){ return dt > seen; }).length;
          if (fresh > 0) {
            var badge = el('span', 'bellbadge', fresh > 9 ? '9+' : String(fresh));
            bell.appendChild(badge);
          }
        }).catch(function(){});
    }
    var menuWrap = el('span', 'acctmenu');
    var avatarBtn = el('button', 'avatarbtn');
    avatarBtn.setAttribute('aria-label', 'Account menu');
    if (d.avatar) {
      var img = el('img', 'avatar');
      img.src = d.avatar;
      img.alt = d.user;
      avatarBtn.appendChild(img);
    } else {
      avatarBtn.appendChild(el('span', 'avatar avatar-fallback', d.user.charAt(0).toUpperCase()));
    }
    var menu = el('div', 'am-drop');
    menu.hidden = true;
    function item(label, href){
      var a = el('a', 'am-item', label);
      a.href = href;
      return a;
    }
    menu.appendChild(el('div', 'am-user', d.user));
    var statsRow = el('div', 'am-stats', 'loading…');
    menu.appendChild(statsRow);
    fetch(rel + 'assets/authorstats.json' + versionQuery).then(function(r){ return r.json(); })
      .then(function(all){
        var s = all[d.user.toLowerCase()] || {runs: 0, author: 0, contrib: 0};
        statsRow.innerHTML = '';
        [[String(s.runs), 'runs', ''], ['<span class="starglyph">★</span>' + s.author, 'author score', ''],
         [String(s.contrib), 'contributor score', '']]
          .forEach(function(pair){
            var box = el('span', 'am-stat');
            var b = el('b', pair[2]);
            b.innerHTML = pair[0];
            box.appendChild(b);
            box.appendChild(el('span', '', pair[1]));
            statsRow.appendChild(box);
          });
      }).catch(function(){ statsRow.remove(); });
    menu.appendChild(item('My profile', rel + 'authors/' + encodeURIComponent(d.user.toLowerCase()) + '/'));
    menu.appendChild(item('Submit a run', rel + 'submit/'));
    menu.appendChild(item('Account settings', 'https://forum.toolassisted.run/my/preferences'));
    var theme = el('div', 'am-theme');
    theme.appendChild(el('span', 'am-label', 'Color scheme'));
    ['system', 'light', 'dark'].forEach(function(t){
      var b = el('button', '', t === 'system' ? 'auto' : t);
      b.dataset.theme = t;
      b.addEventListener('click', function(){ setTheme(t); });
      theme.appendChild(b);
    });
    menu.appendChild(theme);
    if (T.viewasEligible) {
      var viewAsBox = el('div', 'am-theme am-viewas');
      viewAsBox.appendChild(el('span', 'am-label', 'View as'));
      var viewAsSelect = document.createElement('select');
      [['', 'yourself'], ['expert', 'site-wide expert'], ['editor', 'editor'],
       ['member', 'plain member'], ['out', 'signed out']].forEach(function(p){
        var o = document.createElement('option');
        o.value = p[0];
        o.textContent = p[1];
        viewAsSelect.appendChild(o);
      });
      viewAsSelect.value = viewAsMode();
      viewAsSelect.addEventListener('click', function(ev){ ev.stopPropagation(); });
      viewAsSelect.addEventListener('change', function(){
        try {
          if (viewAsSelect.value) sessionStorage.setItem('tar-viewas', viewAsSelect.value);
          else sessionStorage.removeItem('tar-viewas');
        } catch (e) {}
        location.reload();
      });
      viewAsBox.appendChild(viewAsSelect);
      menu.appendChild(viewAsBox);
    }
    if ((T.experts || []).indexOf((d.user || '').toLowerCase()) >= 0) {
      menu.appendChild(item('Expert panel', rel + 'expert/'));
    }
    if ((T.committee || []).indexOf((d.user || '').toLowerCase()) >= 0) {
      menu.appendChild(item('Steering Committee', rel + 'committee/'));
    }
    if ((T.founders || []).indexOf((d.user || '').toLowerCase()) >= 0) {
      menu.appendChild(item('Founder panel', rel + 'founder/'));
    }
    menu.appendChild(item('Log out', api + '/logout'));
    avatarBtn.addEventListener('click', function(ev){
      ev.stopPropagation();
      menu.hidden = !menu.hidden;
    });
    document.addEventListener('click', function(){ menu.hidden = true; });
    menuWrap.appendChild(avatarBtn);
    menuWrap.appendChild(menu);
    box.appendChild(menuWrap);
    var currentTheme = 'system';
    try { currentTheme = localStorage.getItem('tar-theme') || 'system'; } catch (e) {}
    theme.querySelectorAll('button').forEach(function(b){
      b.classList.toggle('on', b.dataset.theme === currentTheme);
    });
  });

  // ---- multi-pick: several registered things, chosen one at a time ----
  // The pattern everywhere: a registered game/member/group is picked from a
  // list you can type into, never typed blind. For multi-valued fields a
  // datalist alone cannot do it (picking replaces the whole value), so the
  // real input goes hidden and chips carry the choices.
  // ---- the type-to-find picker (issue #56) ----
  // Replaces a <select> or an <input> in place with a search box that asks
  // the archivist as you type (debounced), or a local list, and shows the
  // matches to click. The original element's name travels on a hidden
  // input, so forms post exactly what they did. `opts.source(q)` returns a
  // promise of items ({value, label}) or an array; `opts.filter(item)`
  // drops what the page knows would be refused; `opts.onPick(item)`.
  var searchCache = {};
  export function searchArchive(kind, q){
    var key = kind + '\u0000' + q.toLowerCase();
    if (searchCache[key]) return Promise.resolve(searchCache[key]);
    return fetch(api + '/api/search?kind=' + kind + '&q=' + encodeURIComponent(q) + '&limit=40',
                 {credentials: 'include'})
      .then(function(r){ return r.json(); })
      .then(function(j){
        var items = (j && j.ok ? j.items : []).map(function(it){
          return typeof it === 'string' ? {value: it, label: it}
                                        : {value: it.key, label: it.title + ' (' + it.key + ')', item: it};
        });
        searchCache[key] = items;
        return items;
      })
      .catch(function(){ return []; });
  }
  function localSource(items){
    return function(q){
      var low = q.toLowerCase();
      return items.filter(function(it){ return it.label.toLowerCase().indexOf(low) >= 0 || String(it.value).toLowerCase().indexOf(low) >= 0; });
    };
  }
  export function armPicker(field, opts){
    if (!field || !field.parentNode || field.dataset.picker) return null;
    field.dataset.picker = '1';
    var hidden = document.createElement('input');
    hidden.type = 'hidden';
    hidden.name = field.name;
    hidden.required = field.required;
    var wrap = el('div', 'gamepick pickwrap');
    var search = document.createElement('input');
    search.className = 'gamesearch';
    search.autocomplete = 'off';
    search.placeholder = opts.placeholder || 'Type to find…';
    if (field.required) search.required = true;
    var list = el('div', 'gamelist');
    list.hidden = true;
    wrap.appendChild(search); wrap.appendChild(list); wrap.appendChild(hidden);
    field.parentNode.replaceChild(wrap, field);
    var timer = null, serial = 0, picked = null;
    function choose(it){
      picked = it;
      hidden.value = it.value;
      search.value = it.label;
      list.hidden = true;
      if (opts.onPick) opts.onPick(it);
    }
    function show(items){
      list.innerHTML = '';
      items = items.filter(function(it){ return !opts.filter || opts.filter(it); }).slice(0, 12);
      if (!items.length) {
        list.appendChild(el('div', 'authopt authnone', opts.empty || 'nothing matches'));
      }
      items.forEach(function(it){
        var row = el('div', 'authopt', it.label);
        row.addEventListener('mousedown', function(ev){ ev.preventDefault(); choose(it); });
        list.appendChild(row);
      });
      list.hidden = false;
    }
    function lookup(){
      var q = search.value.trim();
      if (picked && search.value !== picked.label) { picked = null; hidden.value = ''; }
      if (!q) { list.hidden = true; return; }
      var mine = ++serial;
      Promise.resolve(opts.source(q)).then(function(items){ if (mine === serial) show(items); });
    }
    search.addEventListener('input', function(){
      clearTimeout(timer);
      timer = setTimeout(lookup, 180);
    });
    search.addEventListener('focus', lookup);
    search.addEventListener('blur', function(){ setTimeout(function(){ list.hidden = true; }, 150); });
    search.addEventListener('keydown', function(e){
      if (e.key === 'Enter' && !list.hidden) {
        var first = list.querySelector('.authopt:not(.authnone)');
        if (first && !picked) { e.preventDefault(); first.dispatchEvent(new MouseEvent('mousedown')); }
      }
    });
    var form = wrap.closest('form');
    if (form) form.addEventListener('reset', function(){ picked = null; hidden.value = ''; search.value = ''; });
    return {
      input: hidden, search: search,
      value: function(){ return hidden.value; },
      picked: function(){ return picked; },
      set: choose,
      clear: function(){ picked = null; hidden.value = ''; search.value = ''; }
    };
  }

  export function armMultiPick(form, name, listId, allowed, fill){
    var input = form && form.querySelector('[name=' + name + ']');
    if (!input || !input.parentNode) return null;
    input.type = 'hidden';
    var pickInput = el('input', 'pickbox');
    pickInput.setAttribute('list', listId);
    pickInput.placeholder = 'type to find; picking adds it';
    if (fill) {
      var fillTimer = null;
      pickInput.addEventListener('input', function(){
        clearTimeout(fillTimer);
        var q = pickInput.value.trim();
        if (q) fillTimer = setTimeout(function(){ fill(q); }, 180);
      });
    }
    var chipsBox = el('span', 'picked');
    var chosen = [];
    function sync(){
      input.value = chosen.join(' ');
      chipsBox.innerHTML = '';
      chosen.forEach(function(k){
        var c = el('span', 'chip', k + ' ');
        var x = el('button', 'chipx', '×');
        x.type = 'button';
        x.setAttribute('aria-label', 'remove ' + k);
        x.addEventListener('click', function(){
          chosen.splice(chosen.indexOf(k), 1);
          sync();
        });
        c.appendChild(x);
        chipsBox.appendChild(c);
      });
    }
    function add(){
      var v = pickInput.value.trim();
      if (!v) return;
      var ok = allowed ? allowed().indexOf(v) >= 0 : true;
      if (ok && chosen.indexOf(v) < 0) chosen.push(v);
      if (ok) pickInput.value = '';
      sync();
    }
    pickInput.addEventListener('change', add);
    pickInput.addEventListener('keydown', function(e){
      if (e.key === 'Enter') { e.preventDefault(); add(); }
    });
    input.parentNode.insertBefore(pickInput, input);
    input.parentNode.insertBefore(chipsBox, input);
    form.addEventListener('reset', function(){ chosen.length = 0; sync(); });
    return {reset: function(){ chosen.length = 0; sync(); },
            set: function(vs){ chosen.length = 0; (vs || []).forEach(function(v){ chosen.push(v); }); sync(); }};
  }
  // any form input marked data-pick becomes one, with that datalist
  document.querySelectorAll('input[data-pick]').forEach(function(inp){
    armMultiPick(inp.form, inp.name, inp.getAttribute('data-pick'), null);
  });

  // ---- file rows: the files a movie was made against (ROMs, discs,
  // sources), one row each with a picker that hashes locally ----
  function sha1Hex(file){
    return file.arrayBuffer().then(function(buf){ return crypto.subtle.digest('SHA-1', buf); })
      .then(function(hash){
        return Array.prototype.map.call(new Uint8Array(hash), function(b){ return b.toString(16).padStart(2, '0'); }).join('');
      });
  }
  function initFileRows(box){
    if (!box || box.dataset.armed) return null;
    box.dataset.armed = '1';
    var list = box.querySelector('.filerow-list');
    var tpl = box.querySelector('.filerow-tpl');
    function addRow(entry){
      var row = tpl.content.firstElementChild.cloneNode(true);
      var nameIn = row.querySelector('input[name=file_name]');
      var shaIn = row.querySelector('input[name=file_sha1]');
      var picker = row.querySelector('input[type=file]');
      if (entry) { nameIn.value = entry.name || ''; shaIn.value = entry.sha1 || ''; }
      picker.addEventListener('change', function(){
        var file = picker.files && picker.files[0];
        if (!file) return;
        nameIn.value = file.name;
        shaIn.value = '';
        shaIn.placeholder = 'hashing…';
        sha1Hex(file).then(function(hex){ shaIn.value = hex; shaIn.placeholder = 'SHA1 (40 hex), optional'; checkSha(); })
          .catch(function(){ shaIn.placeholder = 'could not hash; type the SHA1'; });
      });
      function checkSha(){
        var v = shaIn.value.trim();
        var ok = v === '' || /^[0-9a-fA-F]{40}$/.test(v);
        shaIn.classList.toggle('bad', !ok);
        shaIn.title = ok ? '' : 'a SHA1 is exactly 40 hexadecimal characters (' + v.length + ' so far)';
        return ok;
      }
      shaIn.addEventListener('input', checkSha);
      row.querySelector('.rmfile').addEventListener('click', function(){ row.remove(); });
      list.appendChild(row);
      return row;
    }
    var seed = [];
    try { seed = JSON.parse(box.dataset.files || '[]'); } catch (e) {}
    seed.forEach(addRow);
    box.querySelector('.addfile').addEventListener('click', function(){
      var row = addRow();
      row.querySelector('input[name=file_name]').focus();
    });
    // every row's sha1 must be well-formed, or the form does not go
    return {
      valid: function(){
        var bad = list.querySelector('input[name=file_sha1].bad');
        if (bad) { bad.focus(); return false; }
        return true;
      }
    };
  }
  document.querySelectorAll('.filerows').forEach(function(box){
    var form = box.closest('form');
    var widget = initFileRows(box);
    if (form && widget) fileRowsOf[form.id] = widget;
  });

  // ---- create-game / create-category pages ----
  // The metrics editor both forms share: up to 4 rows, order = tie-break
  // hierarchy; time is a row like any other (a row labeled Time is the
  // run's main time).
  export function initMetricsEd(root, initial){
    var rowsEl = root.querySelector('.mrows');
    var addBtn = root.querySelector('.med-add');
    var metricsField = root.querySelector('[name=metrics]');
    var rows = [];   // {label,type,better,unit}; time is a metric like any other
    function serialize(){
      var arr = rows.map(function(row){
        return {label: row.label.value.trim(), type: row.type.value,
                better: row.better.value,
                unit: row.type.value === 'number' && row.unit.value.trim()
                      ? row.unit.value.trim() : undefined};
      }).filter(function(m){ return m.label; });
      metricsField.value = arr.length ? JSON.stringify(arr) : '';
    }
    function paint(){
      rowsEl.innerHTML = '';
      rows.forEach(function(row, i){
        var div = el('div', 'mrow');
        div.appendChild(row.label); div.appendChild(row.type);
        div.appendChild(row.better); div.appendChild(row.unit);
        row.unit.hidden = row.type.value === 'time';
        [['↑', -1], ['↓', 1]].forEach(function(move){
          var b = el('button', 'btn quiet mmove', move[0]);
          b.type = 'button';
          b.disabled = (i + move[1] < 0) || (i + move[1] >= rows.length);
          b.addEventListener('click', function(){
            rows.splice(i + move[1], 0, rows.splice(i, 1)[0]);
            paint();
          });
          div.appendChild(b);
        });
        var removeBtn = el('button', 'btn quiet mmove', '×');
        removeBtn.type = 'button';
        removeBtn.addEventListener('click', function(){ rows.splice(i, 1); paint(); });
        div.appendChild(removeBtn);
        rowsEl.appendChild(div);
      });
      addBtn.disabled = rows.length >= 4;
      serialize();
    }
    function makeRow(def){
      var row = {
        label: el('input', 'mlabel'), type: el('select', 'mselect mtype'),
        better: el('select', 'mselect mbetter'), unit: el('input', 'munit')
      };
      row.label.placeholder = 'Metric name, e.g. Score';
      [['number', 'number'], ['time', 'time (h:mm:ss)']].forEach(function(o){
        var op = document.createElement('option');
        op.value = o[0]; op.textContent = o[1]; row.type.appendChild(op);
      });
      [['higher', 'higher is better'], ['lower', 'lower is better']].forEach(function(o){
        var op = document.createElement('option');
        op.value = o[0]; op.textContent = o[1]; row.better.appendChild(op);
      });
      row.unit.placeholder = 'unit, e.g. pts';
      if (def) {
        row.label.value = def.label || '';
        row.type.value = def.type || 'number';
        row.better.value = def.better || 'lower';
        row.unit.value = def.unit || '';
      }
      [row.label, row.type, row.better, row.unit].forEach(function(inp){
        inp.addEventListener('input', serialize);
        inp.addEventListener('change', function(){ paint(); });
      });
      return row;
    }
    addBtn.addEventListener('click', function(){
      if (rows.length >= 4) return;
      rows.push(makeRow(null));
      paint();
    });
    (initial || []).forEach(function(def){
      // the stored reserved form {key:'time'} is just a Time row here: the
      // label Time slugifies back to the same key on save
      if (def.key === 'time') rows.push(makeRow({label: def.label || 'Time', type: 'time', better: def.better || 'lower'}));
      else rows.push(makeRow(def));
    });
    paint();
    return {value: function(){ return metricsField.value; }};
  }
  // ---- mobile navigation ----
  var navToggle = document.getElementById('navtoggle');
  if (navToggle) {
    var navEl = navToggle.closest('.nav');
    navToggle.addEventListener('click', function(){
      var open = navEl.classList.toggle('open');
      navToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      navToggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
    });
    // a tap outside, or following a link, closes it again
    document.addEventListener('click', function(e){
      if (navEl.classList.contains('open') && !navEl.contains(e.target)) {
        navEl.classList.remove('open');
        navToggle.setAttribute('aria-expanded', 'false');
      }
    });
  }

  // ---- sortable tables: click any column header to re-sort ----
  function cellValue(td){
    if (!td) return { type: 'empty', val: null };
    if (td.dataset && td.dataset.sort !== undefined) {
      var sv = td.dataset.sort;
      var num = Number(sv);
      return isNaN(num) ? { type: 'text', val: sv.toLowerCase() } : { type: 'num', val: num };
    }
    var raw = td.textContent.trim();
    if (raw === '' || raw === '—' || raw === '·' || raw === '-') {
      return { type: 'empty', val: null };
    }
    var mainChild = td.querySelector ? td.querySelector('b, a') : null;
    var primaryText = (mainChild && td.children && td.children[0] === mainChild) ? mainChild.textContent.trim() : raw;

    var timeMatch = raw.match(/^(?:(\d+):)?(\d{1,2}):(\d{2}(?:\.\d+)?)$/);
    if (timeMatch) {
      var h = timeMatch[1] ? parseFloat(timeMatch[1]) : 0;
      var m = parseFloat(timeMatch[2]);
      var s = parseFloat(timeMatch[3]);
      return { type: 'num', val: h * 3600 + m * 60 + s };
    }
    var numStr = raw.replace(/[★+,\s]/g, '').replace(/f$/i, '');
    if (/^-?\d+(?:\.\d+)?$/.test(numStr)) {
      return { type: 'num', val: parseFloat(numStr) };
    }
    return { type: 'text', val: primaryText.toLowerCase() };
  }

  function compareCells(aTd, bTd, asc){
    var a = cellValue(aTd);
    var b = cellValue(bTd);

    if (a.type === 'empty' && b.type === 'empty') return 0;
    if (a.type === 'empty') return 1;
    if (b.type === 'empty') return -1;

    var res = 0;
    if (a.type === 'num' && b.type === 'num') {
      res = a.val - b.val;
    } else {
      var aStr = a.val !== null ? String(a.val) : '';
      var bStr = b.val !== null ? String(b.val) : '';
      res = aStr.localeCompare(bStr, undefined, { numeric: true, sensitivity: 'base' });
    }
    return asc ? res : -res;
  }

  export function armSortableTable(table){
    if (!table || table.dataset.sortArmed) return;
    table.dataset.sortArmed = '1';
    var thead = table.tHead || table.querySelector('thead');
    if (!thead) return;
    var ths = Array.prototype.slice.call(thead.querySelectorAll('th'));
    if (!ths.length) return;
    var tbody = table.tBodies && table.tBodies[0] ? table.tBodies[0] : table.querySelector('tbody');
    if (!tbody) return;

    ths.forEach(function(th, colIdx){
      th.classList.add('sorth');
      if (!th.getAttribute('role')) th.setAttribute('role', 'columnheader');
      if (!th.getAttribute('tabindex')) th.setAttribute('tabindex', '0');

      var isAsc = th.classList.contains('sort-asc');
      var isDesc = th.classList.contains('sort-desc');
      if (isAsc) th.setAttribute('aria-sort', 'ascending');
      else if (isDesc) th.setAttribute('aria-sort', 'descending');
      else th.setAttribute('aria-sort', 'none');

      function sortCol(){
        var asc;
        if (th.classList.contains('sort-desc')) {
          asc = true;
        } else if (th.classList.contains('sort-asc')) {
          asc = false;
        } else {
          asc = !th.classList.contains('num') && th.dataset.type !== 'num';
        }

        ths.forEach(function(otherTh){
          otherTh.classList.remove('sort-asc');
          otherTh.classList.remove('sort-desc');
          otherTh.setAttribute('aria-sort', 'none');
        });
        th.classList.add(asc ? 'sort-asc' : 'sort-desc');
        th.setAttribute('aria-sort', asc ? 'ascending' : 'descending');

        var rows = Array.prototype.slice.call(tbody.rows);
        rows.sort(function(rowA, rowB){
          return compareCells(rowA.cells[colIdx], rowB.cells[colIdx], asc);
        });
        rows.forEach(function(row){ tbody.appendChild(row); });
      }

      th.addEventListener('click', sortCol);
      th.addEventListener('keydown', function(ev){
        if (ev.key === 'Enter' || ev.key === ' ') {
          ev.preventDefault();
          sortCol();
        }
      });
    });
  }

  export function initSortableTables(){
    document.querySelectorAll('table.sortable').forEach(armSortableTable);
  }
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', initSortableTables);
    } else {
      initSortableTables();
    }
  }
