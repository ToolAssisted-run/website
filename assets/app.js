// toolAssisted.run client script — the whole frontend behavior.
// A real file the generator ships verbatim, except two build-time
// substitutions (the accepted video platforms, host list and display
// names, both from archivist/providers.py).
// It talks to the backend (the archivist) only through its JSON API,
// and reads page data from embedded application/json blobs.
(function(){
  var T = window.TAR || {};
  var api = T.api, rel = T.rel || '';
  var versionQuery = '?v=' + (T.v || '0');
  var mePromise = fetch(api + '/api/me', {credentials: 'include'})
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
  function viewAsActive(){ return viewAsHonored ? viewAsMode() : ''; }
  // a page's covering-experts list, seen through the chosen eyes
  function viewAsCoverage(list, who){
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
  function escapeHtml(s){
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;').replace(/'/g, '&#x27;');
  }
  // build an element with a class and text content
  function el(tag, cls, text){
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
  function setMark(btn, state, title){
    var m = markOf(btn);
    if (!m) return false;
    m.className = 'bmark ' + state;
    m.title = title || '';
    m.setAttribute('aria-label', title || state);
    return true;
  }
  var lastBtn = null;   // the button whose call is being answered
  var fileRowsOf = {};  // form id -> its file-rows widget (validity check)
  // a plain status line in a message box: for outcomes that carry more
  // than yes or no (an id, a link); everything else goes on the mark
  function noteText(box, text, good){
    box.hidden = false;
    box.textContent = text;
    box.className = 'actmsg ' + (good ? 'good' : 'bad');
  }
  function noteHtml(box, good, lines){
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
  function runPageUrl(runId){
    return new URL('runs/' + String(runId) + '/', siteBaseUrl()).href;
  }
  // an outcome: on the mark beside the button that asked, when there is
  // one; in the box otherwise
  function note(box, text, good){
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
  function waitBuilt(serial, cb){
    if (!serial) { cb(false); return; }
    var t0 = Date.now();
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
  function noteBuilt(box, doneText, serial, liveText, keepText){
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
  var authorNamesPromise = null;
  // the registered member names, fetched once and shared
  function authorNames(){
    authorNamesPromise = authorNamesPromise || fetch(rel + 'assets/authornames.json' + versionQuery)
      .then(function(r){ return r.json(); }).catch(function(){ return []; });
    return authorNamesPromise;
  }
  // the author chips picker: type to find a member, add the unknown as new
  function initAuthorPick(pick, initial){
    if (!pick || pick.dataset.armed) return;
    pick.dataset.armed = '1';
    var chipsBox = pick.querySelector('.authchips');
    var search = pick.querySelector('.authsearch');
    var list = pick.querySelector('.authlist');
    var field = pick.querySelector('input[name=authors]');
    var selected = (initial || []).slice();
    var KNOWN = [];
    authorNames().then(function(n){ KNOWN = n; });
    // the co-author policy link beside the picker appears with the second author
    var coauthNote = pick.parentNode && pick.parentNode.querySelector('.coauthnote');
    function sync(){
      field.value = selected.join(',');
      if (coauthNote) coauthNote.hidden = selected.length < 2;
      chipsBox.innerHTML = '';
      selected.forEach(function(name){
        var c = el('span', 'authchip', name);
        var x = el('button', 'authx', '×');
        x.type = 'button';
        x.addEventListener('click', function(){
          selected = selected.filter(function(n){ return n !== name; });
          sync();
        });
        c.appendChild(x);
        chipsBox.appendChild(c);
      });
    }
    function add(name){
      if (name && selected.indexOf(name) < 0) selected.push(name);
      search.value = '';
      list.hidden = true;
      sync();
    }
    // a restored draft sets the whole list at once
    pick.setAuthors = function(names){ selected = names.slice(); sync(); };
    // the hidden field changes without a browser event: say so for listeners
    var sync0 = sync;
    sync = function(){ sync0(); field.dispatchEvent(new Event('change', {bubbles: true})); };
    function fill(){
      var q = search.value.trim().toLowerCase();
      list.innerHTML = '';
      KNOWN.filter(function(n){
        return n.toLowerCase().indexOf(q) >= 0 && selected.indexOf(n) < 0;
      }).slice(0, 8).forEach(function(n){
        var o = el('div', 'authopt', n);
        o.addEventListener('click', function(){ add(n); });
        list.appendChild(o);
      });
      if (q && !KNOWN.some(function(n){ return n.toLowerCase() === q; })) {
        var o = el('div', 'authopt authnew', 'add “' + search.value.trim() + '”');
        o.addEventListener('click', function(){ add(search.value.trim()); });
        list.appendChild(o);
      }
      list.hidden = list.children.length === 0;
    }
    search.addEventListener('input', fill);
    search.addEventListener('focus', fill);
    // Enter picks the first match (or adds the typed name); it never submits
    // the form the picker sits in
    search.addEventListener('keydown', function(e){
      if (e.key !== 'Enter') return;
      e.preventDefault();
      var first = list.querySelector('.authopt');
      if (first) first.click();
    });
    document.addEventListener('click', function(ev){
      if (!pick.contains(ev.target)) list.hidden = true;
    });
    sync();
  }

  // the busy state of the button that started an archivist call
  function busy(btn, on){
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
  function actionBtn(form){
    // the form's real submit button: never a chip's × or a helper button,
    // so the busy spinner lands on the button the member actually pressed
    return form.querySelector('button:not([type=button])') ||
           form.querySelector('button');
  }
  // POST a form to the archivist; resolves {ok, j} and never rejects
  function post(path, fd, btn){
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
  function searchArchive(kind, q){
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
  function armPicker(field, opts){
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

  function armMultiPick(form, name, listId, allowed, fill){
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
    form.addEventListener('reset', function(){ chosen = []; sync(); });
    return {reset: function(){ chosen = []; sync(); }};
  }
  // any form input marked data-pick becomes one, with that datalist
  document.querySelectorAll('input[data-pick]').forEach(function(inp){
    armMultiPick(inp.form, inp.name, inp.getAttribute('data-pick'), null);
  });

  // ---- acts that live on the page they are about ----
  // A game page, a group page and the games index each carry one small zone
  // that only opens for the people who may use it. The archivist decides all
  // of this again; showing it here just keeps a form from being a trap.
  function armZone(dataId, zoneId, msgId, forms){
    var dataEl = document.getElementById(dataId);
    if (!dataEl) return;
    var zoneData = JSON.parse(dataEl.textContent);
    mePromise.then(function(d){
      if (d.unreachable || !d.loggedIn) return;
      var who = d.user.toLowerCase();
      var isExpertHere = viewAsCoverage(zoneData.experts || zoneData.siteExperts, who).indexOf(who) >= 0;
      // an editor shapes the library: zones that are library shape open for
      // them too, minus the forms marked as the experts' alone
      var isEditorHere = zoneData.editorZone
        && ((window.TAR || {}).editors || []).indexOf(who) >= 0;
      if (!isExpertHere && !isEditorHere) return;
      var zone = document.getElementById(zoneId);
      var msg = document.getElementById(msgId);
      zone.hidden = false;
      if (!isExpertHere && isEditorHere) {
        var zoneHeading = zone.querySelector('h2');
        if (zoneHeading && /Expert menu/.test(zoneHeading.textContent)) zoneHeading.textContent = 'Editor menu';
      }
      var zoneBtn = document.getElementById(dataId + '-btn');
      if (zoneBtn) zoneBtn.hidden = false;
      var groupMoveForm = document.getElementById('f-groupmove');
      if (groupMoveForm && zoneData.movable) {
        var moveList = groupMoveForm.querySelector('.gmovelist');
        var moveField = groupMoveForm.querySelector('[name=move]');
        var syncMoveField = function(){
          var picked = [];
          moveList.querySelectorAll('input:checked').forEach(function(c){ picked.push(c.value); });
          moveField.value = picked.join(' ');
        };
        var moveRows = zoneData.movable.map(function(g){
          var labelEl = document.createElement('label');
          labelEl.className = 'gmrow';
          var checkbox = document.createElement('input');
          checkbox.type = 'checkbox';
          checkbox.value = g.key;
          checkbox.addEventListener('change', syncMoveField);
          labelEl.appendChild(checkbox);
          labelEl.appendChild(document.createTextNode(' ' + g.title + ' (' + g.key + ')'
            + (g.group ? ' · now in ' + g.group : '')));
          labelEl.dataset.hay = (g.title + ' ' + g.key).toLowerCase();
          moveList.appendChild(labelEl);
          return labelEl;
        });
        var moveFilter = groupMoveForm.querySelector('.gmfilter');
        if (moveFilter) moveFilter.addEventListener('input', function(){
          var n = moveFilter.value.toLowerCase();
          moveRows.forEach(function(r){ r.hidden = !!n && r.dataset.hay.indexOf(n) === -1; });
        });
      }
      forms.forEach(function(spec){
        var form = document.getElementById(spec.id);
        if (!form) return;
        if (spec.expertOnly && !isExpertHere) {
          var fold = form.closest('details');
          (fold || form).hidden = true;
          return;
        }
        form.addEventListener('submit', function(ev){
          ev.preventDefault();
          if (spec.confirm && !window.confirm(spec.confirm)) return;
          post(spec.path, new FormData(form), actionBtn(form))
            .then(function(res){
              if (res.ok && res.j.ok) {
                noteBuilt(msg, spec.done(res.j), res.j.serial);
                form.reset();
              } else note(msg, res.j.error || 'something went wrong', false);
            });
        });
      });
    });
  }
  armZone('gameactdata', 'f-gameremove-wrap', 'gameact-msg', [
    {id: 'f-gamerename', path: '/api/expert/edit',
     done: function(j){ return 'Renamed to ' + j.to + '.'; }},
    {id: 'f-gamethumb', path: '/api/expert/edit',
     done: function(){ return 'Thumbnail set.'; }},
    {id: 'f-gamecat', path: '/api/expert/edit',
     done: function(j){ return 'Changed to ' + j.to + '.'; }},
    {id: 'f-gamedelete', path: '/api/game/delete', expertOnly: true,
     confirm: 'Delete this game outright, WITH every run in it? ' +
              'This cannot be undone.',
     done: function(j){ return 'Deleted. ' + (j.runs_deleted && j.runs_deleted.length
       ? j.runs_deleted.length + ' run(s) deleted with it.' : 'It held no runs.'); }}]);
  armZone('groupactdata', 'groupacts', 'groupact-msg', [
    {id: 'f-groupmove', path: '/api/group/edit',
     done: function(j){ return 'Moved. This group now holds ' + j.games.length +
       ' game' + (j.games.length === 1 ? '' : 's') + '.'; }},
    {id: 'f-groupdelete', path: '/api/group/delete',
     confirm: 'Delete this group outright? Its games become ungrouped. ' +
              'This cannot be undone.',
     done: function(j){ return 'Deleted; ' + j.released.length +
       ' game(s) are ungrouped.'; }}]);
  armZone('gamesactdata', 'gamesacts', 'gamesact-msg', [
    {id: 'f-newgroup', path: '/api/group/create',
     done: function(j){ return 'The ' + j.group + ' group exists.'; }}]);

  // ---- open name claims, wherever they are answered ----
  // The Steering Committee decides these, and so may a site-wide expert, so
  // the same board is mounted in both panels rather than written twice. It is
  // fetched live: the masked address in each row exists for as long as the
  // page is open and is stored nowhere.
  function mountClaimsBoard(ids, msg){
    var list = document.getElementById(ids.list);
    var form = document.getElementById(ids.form);
    if (!list || !form) return;
    function load(){
      list.innerHTML = '';
      post('/api/claim/pending', new FormData()).then(function(res){
        if (!res.ok || !res.j.ok || !res.j.pending) {
          note(msg, res.j.error || 'could not read the open claims', false);
          return;
        }
        if (!res.j.pending.length) {
          list.appendChild(el('p', 'emptynote', 'No claim is waiting.'));
          return;
        }
        res.j.pending.forEach(function(claim){
          var line = el('p', 'statline');
          line.appendChild(el('b', '', claim.member));
          line.appendChild(el('span', '', ' claims '));
          line.appendChild(el('b', '', claim.identity));
          line.appendChild(el('span', 'actmeta', ' ' + (claim.email || 'no address on file') +
                              ' · ' + claim.date));
          line.appendChild(el('p', 'actnote', claim.evidence));
          var answerBtn = el('button', 'btn quiet', 'Answer');
          answerBtn.addEventListener('click', function(){
            document.getElementById(ids.identity).value = claim.identity;
            document.getElementById(ids.what).textContent =
              'Answering the claim by ' + claim.member + ' to the name ' + claim.identity;
            form.hidden = false;
          });
          line.appendChild(answerBtn);
          list.appendChild(line);
        });
      });
    }
    function answer(approve){
      var fd = new FormData(form);
      fd.append('action', approve ? 'approved' : 'denied');
      if (approve) fd.delete('note');
      post('/api/claim/decide', fd, actionBtn(form)).then(function(res){
        if (res.ok && res.j.ok) {
          note(msg, (approve ? 'Approved. ' : 'Denied. ') + (res.j.told || '') +
                    ' The site rebuilds from the archive; it shows there in about a ' +
                    'minute.', true);
          form.hidden = true;
          form.reset();
          load();
        } else note(msg, res.j.error || 'something went wrong', false);
      });
    }
    document.getElementById(ids.yes)
            .addEventListener('click', function(){ answer(true); });
    document.getElementById(ids.no).addEventListener('click', function(){
      if ((form.querySelector('[name=note]').value || '').trim().length < 8) {
        note(msg, 'Denying needs a reason: the person is told it.', false);
        return;
      }
      answer(false);
    });
    document.getElementById(ids.cancel)
            .addEventListener('click', function(){ form.hidden = true; });
    load();
  }

  // ---- founder panel ----
  var founderPanelEl = document.getElementById('fpaneldata');
  if (founderPanelEl) {
    var founderData = JSON.parse(founderPanelEl.textContent);
    mePromise.then(function(d){
      var gate = document.getElementById('fpanel-gate');
      if (d.unreachable) {
        gate.textContent = 'The archivist is unreachable, so who you are cannot be ' +
          'checked right now.';
        return;
      }
      if (!d.loggedIn || viewAsActive() || founderData.founders.indexOf(d.user.toLowerCase()) < 0) {
        gate.textContent = 'This panel is the Founder’s.';
        return;
      }
      gate.hidden = true;
      document.getElementById('fpanel').hidden = false;
      var msg = document.getElementById('fpanel-msg');
      var seated = (founderData.committee || []).map(function(x){ return x.toLowerCase(); });
      armPicker(document.querySelector('#f-seat [name=target]'), {
        source: function(q){ return searchArchive('members', q); },
        filter: function(it){ return seated.indexOf(it.value.toLowerCase()) < 0; },
        placeholder: 'type to find a member', empty: 'no such member, or already seated'});
      ['f-seat', 'f-unseat'].forEach(function(id){
        var form = document.getElementById(id);
        form.addEventListener('submit', function(ev){
          ev.preventDefault();
          post('/api/founder/committee', new FormData(form),
               actionBtn(form)).then(function(res){
            if (res.ok && res.j.ok) {
              noteBuilt(msg, (res.j.action === 'granted' ? 'Seated. ' : 'Unseated. ') +
                        (res.j.told || ''), res.j.serial);
              form.reset();
            } else note(msg, res.j.error || 'something went wrong', false);
          });
        });
      });
    });
  }

  // ---- steering committee panel ----
  var committeePanelEl = document.getElementById('cpaneldata');
  if (committeePanelEl) {
    var committeeData = JSON.parse(committeePanelEl.textContent);
    mePromise.then(function(d){
      var gate = document.getElementById('cpanel-gate');
      if (d.unreachable) {
        gate.textContent = 'The archivist is unreachable, so who you are cannot be ' +
          'checked right now.';
        return;
      }
      if (!d.loggedIn) {
        gate.innerHTML = 'This panel is for the Steering Committee. <a href="' + api +
          '/login">Log in</a> to see whether it is yours.';
        return;
      }
      if (viewAsActive() || committeeData.committee.indexOf(d.user.toLowerCase()) < 0) {
        gate.textContent = 'This panel is for the Steering Committee, and you are not on it.';
        return;
      }
      gate.hidden = true;
      document.getElementById('cpanel').hidden = false;
      var committeeMsg = document.getElementById('cpanel-msg');
      mountClaimsBoard({list: 'cpanel-list', form: 'f-claimdecide',
                        identity: 'cdecide-identity', what: 'cdecide-what',
                        yes: 'cdecide-yes', no: 'cdecide-no', cancel: 'cdecide-cancel'},
                       committeeMsg);

      // recording a Committee decision (moved here from the members page:
      // governance tools live in panels, the members page is about the runs)
      var roleForm = document.getElementById('f-role');
      var roleMsg = document.getElementById('role-msg');
      // who can be granted a role is whoever lacks it; who can lose one is
      // whoever holds it. The list follows the two selects, so the box never
      // offers a name the archivist would refuse.
      var roleSelect = roleForm.querySelector('[name=role]');
      var actionSelect = roleForm.querySelector('[name=action]');
      function roleHolders(){
        return (roleSelect.value === 'committee' ? committeeData.committeeNames
                : roleSelect.value === 'editor' ? (committeeData.editors || [])
                : committeeData.moderators).map(function(x){ return x.toLowerCase(); });
      }
      var rolePicker = armPicker(roleForm.querySelector('[name=target]'), {
        source: function(q){ return searchArchive('members', q); },
        filter: function(it){
          var holds = roleHolders().indexOf(it.value.toLowerCase()) >= 0;
          return actionSelect.value === 'granted' ? !holds : holds;
        },
        placeholder: 'type to find a member', empty: 'nobody matches for this role and decision'});
      roleSelect.addEventListener('change', function(){ rolePicker.clear(); });
      actionSelect.addEventListener('change', function(){ rolePicker.clear(); });
      roleForm.addEventListener('submit', function(ev){
        ev.preventDefault();
        post('/api/role/decide', new FormData(roleForm), actionBtn(roleForm))
          .then(function(res){
            if (res.ok && res.j.ok) {
              noteBuilt(roleMsg, 'Recorded: ' + res.j.votes + ' of the ' + (res.j.cast || res.j.committee) +
                        ' votes cast went for it.', res.j.serial);
              roleForm.reset();
            } else note(roleMsg, res.j.error || 'something went wrong', false);
          });
      });
      // the Committee's delete (#61): spam and test accounts, picked here
      // rather than on the member's own page. Seated members are the
      // Founder's alone, and the Founder is nobody's
      var deleteForm = document.getElementById('f-memberdelete');
      var deleteMsg = document.getElementById('memberdelete-msg');
      var me = d.user.toLowerCase(), founders = (committeeData.founders || []).map(function(x){ return x.toLowerCase(); });
      var seatedNames = committeeData.committeeNames.map(function(x){ return x.toLowerCase(); });
      armPicker(deleteForm.querySelector('[name=target]'), {
        source: function(q){ return searchArchive('members', q); },
        filter: function(it){
          var low = it.value.toLowerCase();
          if (founders.indexOf(low) >= 0) return false;
          return !(seatedNames.indexOf(low) >= 0 && founders.indexOf(me) < 0);
        },
        placeholder: 'type to find a member', empty: 'no such member, or not yours to delete'});
      deleteForm.addEventListener('submit', function(ev){
        ev.preventDefault();
        var target = deleteForm.querySelector('[name=target]').value.trim();
        if (!window.confirm('Delete the member ' + target + ' outright? This cannot be undone.')) return;
        post('/api/member/delete', new FormData(deleteForm), actionBtn(deleteForm))
          .then(function(res){
            if (res.ok && res.j.ok) {
              noteBuilt(deleteMsg, 'Deleted.', res.j.serial, 'Their page is gone from the site now.');
              deleteForm.reset();
            } else note(deleteMsg, res.j.error || 'something went wrong', false);
          });
      });
      // the whole-site appointment: everyone who does not already hold it
      armPicker(document.getElementById('siteexpert-user'), {
        source: function(q){ return searchArchive('members', q); },
        filter: function(it){ return committeeData.siteExperts.indexOf(it.value.toLowerCase()) < 0; },
        placeholder: 'type to find a member', empty: 'no such member, or already a whole-site expert'});
      var siteExpertForm = document.getElementById('f-siteexpert');
      siteExpertForm.addEventListener('submit', function(ev){
        ev.preventDefault();
        post('/api/expert/appoint', new FormData(siteExpertForm),
             actionBtn(siteExpertForm)).then(function(res){
          if (res.ok && res.j.ok) {
            noteBuilt(committeeMsg, res.j.user + ' is now an expert for the whole site.',
                      res.j.serial);
            siteExpertForm.reset();
          } else note(committeeMsg, res.j.error || 'something went wrong', false);
        });
      });
      // the editor seat: everyone who does not already hold it
      var editorNames = (committeeData.editors || []).map(function(x){ return x.toLowerCase(); });
      armPicker(document.getElementById('editorrole-user'), {
        source: function(q){ return searchArchive('members', q); },
        filter: function(it){ return editorNames.indexOf(it.value.toLowerCase()) < 0; },
        placeholder: 'type to find a member', empty: 'no such member, or already an editor'});
      var editorForm = document.getElementById('f-editorrole');
      editorForm.addEventListener('submit', function(ev){
        ev.preventDefault();
        post('/api/editor/appoint', new FormData(editorForm),
             actionBtn(editorForm)).then(function(res){
          if (res.ok && res.j.ok) {
            noteBuilt(committeeMsg, res.j.user + ' is now an editor.', res.j.serial);
            editorForm.reset();
          } else note(committeeMsg, res.j.error || 'something went wrong', false);
        });
      });
    });
  }

  // ---- expert panel ----
  var expertPanelEl = document.getElementById('paneldata');
  if (expertPanelEl) {
    var panelData = JSON.parse(expertPanelEl.textContent);
    mePromise.then(function(d){
      var gate = document.getElementById('panel-gate');
      if (d.unreachable) {
        gate.textContent = 'The archivist is unreachable, so who you are cannot be ' +
          'checked right now. Nothing here is lost; try again in a moment.';
        return;
      }
      if (!d.loggedIn) {
        gate.innerHTML = 'This panel is for experts. <a href="' + api +
          '/login">Log in</a> to see whether it is yours.';
        return;
      }
      var myName = d.user.toLowerCase();
      var myRoster = panelData.roster.filter(function(e){ return e.user.toLowerCase() === myName; });
      var viewAs = viewAsActive();
      if (viewAs === 'expert') myRoster = [{user: d.user, scope: 'site'}];
      else if (viewAs) myRoster = [];
      var amCommittee = !viewAs && panelData.committee.indexOf(myName) >= 0;
      // a Committee seat opens the panel too: any single Committee member may
      // appoint an expert at any scope (Principles 2.5.3), so the appointment
      // forms are theirs even when they hold no expert scope of their own
      if (!myRoster.length && !amCommittee) {
        gate.textContent = 'This panel is for experts, and you hold no scope. Experts ' +
          'are appointed by an expert whose scope already covers the one being given, ' +
          'or by a Steering Committee member.';
        return;
      }
      gate.hidden = true;
      document.getElementById('panel').hidden = false;
      var msg = document.getElementById('panel-msg');

      // what you hold, and where it applies
      var box = document.getElementById('panel-scopes');
      myRoster.forEach(function(e){
        var line = el('p', 'statline');
        var chip = el('span', 'rolechip role-expert', 'Expert');
        line.appendChild(chip);
        line.appendChild(document.createTextNode(' '));
        if (e.href) {
          var a = el('a', '', e.label);
          a.href = rel + e.href;
          line.appendChild(a);
        } else line.appendChild(document.createTextNode(e.label));
        line.appendChild(el('span', 'actmeta', ' since ' + e.date +
                            (e.by ? ', appointed by ' + e.by : '')));
        box.appendChild(line);
      });

      // ---- what each of my scopes reaches, so the page can answer two
      // questions locally: may I act on this, and does that member already
      // speak for it? Both are decided again by the archivist; doing it here
      // as well is what keeps the lists short and honest rather than offering
      // a name that would come back refused.
      var groupOf = {}, gamesIn = {};
      panelData.groups.forEach(function(gr){
        gamesIn[gr.key] = gr.games || [];
        (gr.games || []).forEach(function(k){ groupOf[k] = gr.key; });
      });
      function coversGame(scopes, key){
        return scopes.indexOf('site') >= 0 || scopes.indexOf(key) >= 0 ||
               scopes.indexOf(key.split('/')[0]) >= 0 ||
               (groupOf[key] && scopes.indexOf('group:' + groupOf[key]) >= 0);
      }
      function coversGroup(scopes, key){
        if (scopes.indexOf('site') >= 0 || scopes.indexOf('group:' + key) >= 0) return true;
        var inside = gamesIn[key] || [];
        return inside.length > 0 && inside.every(function(k){ return coversGame(scopes, k); });
      }
      function scopesOf(name){
        var low = name.toLowerCase();
        return panelData.roster.filter(function(e){ return e.user.toLowerCase() === low; })
                       .map(function(e){ return e.scope; });
      }
      var myScopes = myRoster.map(function(e){ return e.scope; });
      var amSite = myScopes.indexOf('site') >= 0;

      function fillSelect(sel, items, empty){
        if (!sel) return;
        sel.innerHTML = '';
        items.forEach(function(it){
          var o = document.createElement('option');
          o.value = it.value;
          o.textContent = it.label;
          sel.appendChild(o);
        });
        if (!items.length) {
          var none = document.createElement('option');
          none.value = '';
          none.textContent = empty;
          sel.appendChild(none);
        }
      }

      // the games and groups I may hand out authority over. A Committee seat
      // reaches everything for appointment (2.5.3), and appointment only: the
      // group forms and the pending list stay derived from expert scope.
      var myGroups = panelData.groups.filter(function(gr){ return coversGroup(myScopes, gr.key); });
      var apptGroups = amCommittee ? panelData.groups : myGroups;
      // games are searched as you type (#56): the page carries no list of
      // them; what comes back is filtered down to what is mine to hand out
      var gamePicker = armPicker(document.getElementById('appoint-game'), {
        source: function(q){ return searchArchive('games', q); },
        filter: function(it){ return amCommittee || coversGame(myScopes, it.value); },
        placeholder: 'type to find a game', empty: 'no such game, or not yours to hand out',
        onPick: function(){ refreshCandidates(gamePicker.input, userPickers['appoint-game-user']); }});
      fillSelect(document.getElementById('appoint-group'),
           apptGroups.map(function(gr){ return {value: 'group:' + gr.key, label: gr.title}; }),
           'no group is yours to hand out');
      if (amSite || amCommittee) {
        document.getElementById('appoint-wide-wrap').hidden = false;
        fillSelect(document.getElementById('appoint-wide'),
             panelData.scopes.filter(function(s){ return s.key !== 'site' && s.key.indexOf('/') < 0
                                                 && s.key.indexOf('group:') < 0; })
                     .map(function(s){ return {value: s.key, label: s.label}; }),
             'nothing');
      }

      // members who do not already speak for the chosen scope: the member
      // box searches as you type and drops whoever the archivist would refuse
      function speaksFor(name, scope){
        var s = scopesOf(name);
        if (!s.length) return false;
        if (s.indexOf('site') >= 0) return true;
        if (scope.indexOf('group:') === 0) return coversGroup(s, scope.slice(6));
        if (scope.indexOf('/') > 0) return coversGame(s, scope);
        return s.indexOf(scope) >= 0;
      }
      var userPickers = {};
      function refreshCandidates(scopeField, picker){
        if (picker) picker.clear();
      }
      [['appoint-game', 'appoint-game-user'], ['appoint-group', 'appoint-group-user'],
       ['appoint-wide', 'appoint-wide-user']].forEach(function(pair){
        var userField = document.getElementById(pair[1]);
        if (!userField) return;
        var scopeOf = function(){
          var f = document.getElementById(pair[0]);
          return f ? f.value : (pair[0] === 'appoint-game' ? gamePicker.value() : '');
        };
        userPickers[pair[1]] = armPicker(userField, {
          source: function(q){ return searchArchive('members', q); },
          filter: function(it){ var sc = scopeOf(); return !!sc && !speaksFor(it.value, sc); },
          placeholder: 'type to find a member', empty: 'pick the scope first, or everybody matching already speaks for it'});
        var scopeSelect = document.getElementById(pair[0]);
        if (scopeSelect && scopeSelect.tagName === 'SELECT') {
          scopeSelect.addEventListener('change', function(){ refreshCandidates(scopeSelect, userPickers[pair[1]]); });
        }
      });


      // the lists the remaining forms run on: what I may step down from, the
      // group I may change, and the games I may name in one
      fillSelect(document.getElementById('resign-scope'),
           [{value: '', label: 'every scope I hold'}].concat(
             myRoster.map(function(e){ return {value: e.scope, label: e.label}; })),
           'nothing to step down from');
      fillSelect(document.getElementById('groupedit-key'),
           myGroups.map(function(gr){
             return {value: gr.key,
                     label: gr.title}; }),
           'no group is yours to change');
      // the group pickers offer only games a group could actually take: a
      // game already in one would be refused, since a game belongs to one.
      // The list fills from the archivist as you type (#56): what it holds
      // is what has been seen to qualify, and only that is accepted
      var gameList = document.getElementById('panel-gamelist');
      var groupableKeys = [];
      function groupableFill(q){
        return searchArchive('games', q).then(function(items){
          gameList.innerHTML = '';
          items.filter(function(it){ return it.item && !it.item.group && coversGame(myScopes, it.value); })
               .forEach(function(it){
            if (groupableKeys.indexOf(it.value) < 0) groupableKeys.push(it.value);
            var o = document.createElement('option');
            o.value = it.value;
            o.label = it.item.title;
            gameList.appendChild(o);
          });
        });
      }
      armMultiPick(document.getElementById('f-groupnew'), 'games',
                   'panel-gamelist', function(){ return groupableKeys; }, groupableFill);
      var groupEditSelect = document.getElementById('groupedit-key');
      armMultiPick(document.getElementById('f-groupedit'), 'add',
                   'panel-gamelist', function(){ return groupableKeys; }, groupableFill);
      // removing offers only what the chosen group actually holds
      var removeList = document.createElement('datalist');
      removeList.id = 'groupedit-removelist';
      document.body.appendChild(removeList);
      function refreshRemoveList(){
        removeList.innerHTML = '';
        (gamesIn[groupEditSelect.value] || []).forEach(function(k){
          var o = document.createElement('option');
          o.value = k;
          removeList.appendChild(o);
        });
      }
      groupEditSelect.addEventListener('change', refreshRemoveList);
      refreshRemoveList();
      armMultiPick(document.getElementById('f-groupedit'), 'remove',
                   'groupedit-removelist', function(){
        return gamesIn[groupEditSelect.value] || [];
      });
      // annulment names an expert, so the experts are the list
      var expertList = document.getElementById('panel-expertlist');
      if (expertList) {
        var seen = {};
        panelData.roster.forEach(function(e){
          if (seen[e.user]) return;
          seen[e.user] = 1;
          var o = document.createElement('option');
          o.value = e.user;
          expertList.appendChild(o);
        });
      }
      // annulment names any scope, since the Committee decides it, not me
      var scopeList = document.getElementById('panel-scopelist');
      panelData.scopes.forEach(function(s){
        var o = document.createElement('option');
        o.value = s.key;
        o.label = s.label;
        scopeList.appendChild(o);
      });

      if (panelData.committee.indexOf(myName) >= 0) {
        document.getElementById('panel-annul-wrap').hidden = false;
      }

      function armPanel(id, path, done){
        var form = document.getElementById(id);
        if (!form) return;
        form.addEventListener('submit', function(ev){
          ev.preventDefault();
          post(path, new FormData(form), actionBtn(form)).then(function(res){
            if (res.ok && res.j.ok) {
              noteBuilt(msg, done(res.j), res.j.serial);
              form.reset();
            } else note(msg, res.j.error || 'something went wrong', false);
          });
        });
      }
      ['f-appoint-game', 'f-appoint-group', 'f-appoint-wide'].forEach(function(id){
        armPanel(id, '/api/expert/appoint', function(j){
          return j.user + ' is now an expert for ' + j.scope + '.';
        });
      });
      armPanel('f-resign', '/api/expert/resign', function(j){
        return 'You stepped down from ' + j.dropped + ' scope' +
               (j.dropped === 1 ? '' : 's') + '.';
      });
      armPanel('f-groupnew', '/api/group/create', function(j){
        return 'The ' + j.group + ' group exists, with ' +
               j.games.length + ' game' + (j.games.length === 1 ? '' : 's') + '.';
      });
      armPanel('f-groupedit', '/api/group/edit', function(j){
        return 'The ' + j.title + ' group now holds ' + j.games.length + ' game' +
               (j.games.length === 1 ? '' : 's') + '.';
      });
      armPanel('f-annul', '/api/expert/annul', function(j){
        return 'Applied: ' + j.votes + ' of ' + j.committee + ' voted for it, and ' +
               j.target + ' lost ' + j.dropped + ' scope' + (j.dropped === 1 ? '' : 's') + '.';
      });
    });
  }

  // ---- run-page act zone ----
  var actDataEl = document.getElementById('actdata');
  if (actDataEl) {
    var runData = JSON.parse(actDataEl.textContent);
    // the visit tally: counted here so plain crawlers stay out of it
    if (api) {
      var visitForm = new FormData();
      visitForm.append('run', runData.run);
      fetch(api + '/api/visit', {method: 'POST', body: visitForm})
        .then(function(r){ return r.json(); })
        .then(function(j){
          if (!j.ok) return;
          document.getElementById('visitnum').textContent = j.visits.toLocaleString();
          document.getElementById('visitbadge').hidden = false;
        }).catch(function(){});
    }
    mePromise.then(function(d){
      var zone = document.getElementById('actzone');
      if (!zone || d.unreachable) return;
      var msg = document.getElementById('act-msg');
      var myName = d.loggedIn ? d.user.toLowerCase() : null;
      var isAuthor = myName !== null && runData.authors.indexOf(myName) >= 0;
      var isExpert = myName !== null && viewAsCoverage(runData.experts, myName).indexOf(myName) >= 0;
      var anything = false;
      function arm(id, path, prefill){
        var form = document.getElementById(id);
        if (!form) return;
        form.hidden = false;
        var wrap = document.getElementById(id + '-wrap');
        if (wrap) wrap.hidden = false;
        // a form housed in the bottom Expert menu opens the box with it, and
        // answers into the box's own message line
        var menu = wrap && wrap.closest ? wrap.closest('.expertmenu') : null;
        if (menu) menu.hidden = false;
        var msgBox = (menu && document.getElementById('expert-msg')) || msg;
        anything = true;
        if (prefill) prefill(form);
        form.addEventListener('submit', function(ev){
          ev.preventDefault();
          if (fileRowsOf[form.id] && !fileRowsOf[form.id].valid()) return;
          var fd = new FormData(form);
          fd.append('run', runData.run);
          function send(){
            post(path, fd, actionBtn(form)).then(function(res){
              if (res.ok && res.j.ok) {
                var voided = (res.j.voided || []).length
                  ? ' Its ' + res.j.voided.join(' and ') + ' were invalidated by this change.' : '';
                // the act is done: the form folds shut, the check moves onto
                // its summary, and the message stands on its own (#68)
                var det = form.closest('details');
                if (det) {
                  det.open = false;
                  var sum = det.querySelector('summary');
                  if (sum && !sum.querySelector('.sumdone')) sum.appendChild(el('span', 'sumdone', '\u2713 recorded'));
                }
                noteBuilt(msgBox, 'Recorded, thank you: your act is archived.' + voided +
                          ' You can leave this page whenever you like.', res.j.serial, null, true);
              } else note(msgBox, res.j.error || 'something went wrong', false);
            });
          }
          send();
        });
      }
      if (!d.loggedIn) {
        zone.hidden = false;
        document.getElementById('act-login').hidden = false;
        return;
      }
      if (isAuthor) arm('f-withdraw', '/api/withdraw');
      var isEditor = myName !== null && ((window.TAR || {}).editors || []).indexOf(myName) >= 0;
      if (isExpert || isEditor) {
        // move the run to another category or subcategory: the selects feed
        // the hidden value ("option" or "option/sub") the archivist reads
        arm('f-move', '/api/expert/edit', function(form){
          var goalSel = document.getElementById('mv-goal'), subSel = document.getElementById('mv-sub');
          var subWrap = document.getElementById('mv-subwrap'), valueField = form.querySelector('[name=value]');
          runData.categories.forEach(function(o){
            var opt = document.createElement('option'); opt.value = o.key; opt.textContent = o.label;
            goalSel.appendChild(opt);
          });
          var uncl = document.createElement('option'); uncl.value = 'unclassified'; uncl.textContent = 'Unclassified';
          goalSel.appendChild(uncl);
          function paintMove(){
            var picked = runData.categories.filter(function(o){ return o.key === goalSel.value; })[0];
            var subs = (picked && picked.subcategories) || [];
            subSel.innerHTML = '';
            subs.forEach(function(x){
              var opt = document.createElement('option'); opt.value = x.key; opt.textContent = x.label;
              subSel.appendChild(opt);
            });
            subWrap.hidden = !subs.length;
            if (subs.length && runData.goal === goalSel.value && runData.sub) subSel.value = runData.sub;
            valueField.value = goalSel.value + (subs.length ? '/' + subSel.value : '');
          }
          goalSel.value = runData.goal || 'unclassified';
          goalSel.addEventListener('change', paintMove);
          subSel.addEventListener('change', function(){ valueField.value = goalSel.value + '/' + subSel.value; });
          paintMove();
        });
      }
      if (isAuthor || isExpert || isEditor) {
        // editing lives on the submit page, in edit mode: reveal the link
        var editWrap = document.getElementById('f-edit-wrap');
        if (editWrap) editWrap.hidden = false;  // sits under the notes, not in the zone
      }
      if (!isAuthor && !runData.imported) {
        if (!runData.videoOnly) {
          if (runData.reproduced.indexOf(myName) < 0) arm('f-repro', '/api/reproduce');
        }
        if (runData.hasEncode && runData.verified.indexOf(myName) < 0) arm('f-verify', '/api/verify');
        if (!runData.videoOnly && document.getElementById('f-console') && (runData.consoled || []).indexOf(myName) < 0) arm('f-console', '/api/console-verify');
        if (runData.openCase) {
          if (runData.openCase.verifiers.indexOf(myName) >= 0 && runData.openCase.voted.indexOf(myName) < 0) {
            var voteForm = document.getElementById('f-vote');
            voteForm.hidden = false;
            anything = true;
            voteForm.querySelectorAll('button[data-reaffirm]').forEach(function(b){
              b.addEventListener('click', function(ev){
                ev.preventDefault();
                var fd = new FormData(voteForm);
                fd.append('run', runData.run);
                fd.append('case', runData.openCase.id);
                fd.append('reaffirm', b.dataset.reaffirm);
                post('/api/case/vote', fd, b).then(function(res){
                  if (res.ok && res.j.ok) {
                    voteForm.hidden = true;
                    note(msg, 'Vote recorded. The case is now ' + res.j.case_status + '.', true);
                  } else note(msg, res.j.error || 'something went wrong', false);
                });
              });
            });
          }
        } else if (runData.liveVerifs > 0) {
          document.getElementById('f-case-wrap').hidden = false;
          arm('f-case', '/api/case/open');
        }
      }
      if (isExpert) {
        arm('f-rundelete', '/api/run/delete');
        var deleteForm = document.getElementById('f-rundelete');
        if (deleteForm) deleteForm.addEventListener('submit', function(ev){
          if (!window.confirm('Delete this run outright? This cannot be undone, ' +
                              'and only your reason remains.')) {
            ev.stopImmediatePropagation();
            ev.preventDefault();
          }
        }, true);
        // invalidating: the target is a live act on this run, so offer exactly those
        var invalidateSelect = document.getElementById('inv-target');
        var kinds = [['reproduction', runData.reproducedNames || []],
                     ['verification', runData.verifiedNames || []],
                     ['console', runData.consoledNames || []]];
        var options = [];
        kinds.forEach(function(pair){
          pair[1].forEach(function(name){
            options.push({kind: pair[0], name: name});
          });
        });
        if (invalidateSelect && options.length) {
          options.forEach(function(o, i){
            var opt = document.createElement('option');
            opt.value = o.name;
            opt.dataset.kind = o.kind;
            opt.textContent = o.kind + ' by ' + o.name;
            invalidateSelect.appendChild(opt);
          });
          var kindField = document.getElementById('inv-kind');
          function syncKind(){
            var opt = invalidateSelect.options[invalidateSelect.selectedIndex];
            kindField.value = opt ? opt.dataset.kind : '';
          }
          invalidateSelect.addEventListener('change', syncKind);
          syncKind();
          arm('f-invalidate', '/api/invalidate');
        }
        // closing a report: only the open ones on this run
        var reportSelect = document.getElementById('res-report');
        if (reportSelect && (runData.openReports || []).length) {
          runData.openReports.forEach(function(rep){
            var opt = document.createElement('option');
            opt.value = rep.id;
            opt.textContent = 'R' + rep.id + ' · ' + rep.kind + ' · by ' + rep.by;
            reportSelect.appendChild(opt);
          });
          arm('f-resolve', '/api/report/resolve');
        }
      }
      if (anything) zone.hidden = false;
    });
  }

  // ---- the game editor (covering experts): one local draft, one Save ----
  // The page edits a copy of the game's record. Nothing is written until
  // Save, which turns the draft's differences into the ordered sequence of
  // logged edits the archivist knows (title, properties, thumbnail, new
  // categories and subcategories, renames and rules, metrics, deletions,
  // orders), all under one public reason. The first failure stops the
  // sequence; what went through is the new baseline, the rest stays pending.
  var gameEditEl = document.getElementById('gameeditdata');
  if (gameEditEl) {
    var gameEditData = JSON.parse(gameEditEl.textContent);
    mePromise.then(function(d){
      var gate = document.getElementById('ge-gate');
      if (d.unreachable) {
        gate.textContent = 'The archivist is unreachable, so who you are cannot ' +
          'be checked right now. Reading works; editing will not.';
        return;
      }
      if (!d.loggedIn) {
        gate.innerHTML = 'This page is for the experts covering this game. ' +
          '<a href="' + api + '/login">Log in</a> to see whether it is yours.';
        return;
      }
      if (viewAsCoverage(gameEditData.experts, d.user.toLowerCase()).indexOf(d.user.toLowerCase()) < 0
          && ((window.TAR || {}).editors || []).indexOf(d.user.toLowerCase()) < 0) {
        gate.textContent = 'This page is for the experts covering this game ' +
          'and for editors, and you are neither.';
        return;
      }
      gate.hidden = true;
      var editor = document.getElementById('geditor');
      editor.hidden = false;
      var msg = document.getElementById('ge-msg');
      var saveBtn = document.getElementById('ge-save');
      var whyIn = document.getElementById('ge-why');
      var pendingEl = document.getElementById('ge-pending');
      var byId = function(id){ return document.getElementById(id); };

      // ---- the baseline (what the archive holds) and the draft ----
      var base = {
        title: gameEditData.title,
        props: {released: byId('ge-released').value, unofficial: byId('ge-unofficial').checked ? 'yes' : 'no',
                discord: byId('ge-discord').value, website: byId('ge-website').value, rta: byId('ge-rta').value,
                rules: byId('ge-rules').value},
        selector: gameEditData.selector || 'buttons',
        cats: (gameEditData.options || []).map(function(o){
          return {key: o.key, label: o.label, rule: o.rule || '', runs: o.runs,
                  metrics: JSON.stringify(o.metrics || []), subSelector: o.subSelector || 'buttons',
                  subs: (o.subcategories || []).map(function(x){ return {key: x.key, label: x.label, rule: x.rule || '', runs: x.runs}; })};
        })
      };
      // the draft: categories as live objects the cards edit in place
      var draft = {cats: JSON.parse(JSON.stringify(base.cats))};
      var selectorRadios = document.querySelectorAll('input[name=ge-selector]');
      selectorRadios.forEach(function(r){ r.checked = r.value === base.selector; r.addEventListener('change', refresh); });
      function selectorChoice(){
        var on = Array.prototype.filter.call(selectorRadios, function(r){ return r.checked; })[0];
        return on ? on.value : 'buttons';
      }
      var newSeq = 0;

      // ---- the cards ----
      var box = byId('ge-cats');
      // up/down arrows for one item of a list: only the moves that exist
      // are offered (nothing up for the first, nothing down for the last)
      function orderArrows(list, item, render){
        var wrap = el('span', 'orderbtns');
        var i = list.indexOf(item);
        [['up', -1, 'Move up (earlier)'], ['down', 1, 'Move down (later)']].forEach(function(spec){
          var j = i + spec[1];
          if (j < 0 || j >= list.length) return;
          var b = el('button', 'orderbtn ' + spec[0]); b.type = 'button'; b.title = spec[2];
          b.setAttribute('aria-label', spec[2]);
          b.addEventListener('click', function(){
            list.splice(i, 1); list.splice(j, 0, item);
            render();
          });
          wrap.appendChild(b);
        });
        return wrap;
      }
      function card(c){
        var el_ = el('div', 'gecard');
        c.el = el_;
        var head = el('div', 'gehead');
        head.appendChild(el('b', '', c.key || '(new)'));
        head.appendChild(el('span', 'actmeta', ' ' + (c.runs || 0) + ' run' + (c.runs === 1 ? '' : 's')));
        head.appendChild(orderArrows(draft.cats, c, renderCards));
        if (!c.runs) {
          var del = el('button', 'btn danger', c.deleted ? 'Keep' : 'Delete'); del.type = 'button';
          del.addEventListener('click', function(){
            if (c.isNew) { draft.cats.splice(draft.cats.indexOf(c), 1); renderCards(); return; }
            c.deleted = !c.deleted; renderCards();
          });
          head.appendChild(del);
        }
        el_.appendChild(head);
        if (c.deleted) { el_.classList.add('deleted'); el_.appendChild(el('p', 'rules', 'Marked for deletion; Save removes it.')); return el_; }
        function field(labelText, tag){
          var lab = el('label', '', labelText + ' ');
          var inp = el(tag === 'textarea' ? 'textarea' : 'input');
          lab.appendChild(inp);
          el_.appendChild(lab);
          return inp;
        }
        var labelIn = field('Label'); labelIn.value = c.label; labelIn.maxLength = 80;
        labelIn.addEventListener('input', function(){ c.label = labelIn.value; refresh(); });
        if (!c.isNew) {
          c.newKey = c.newKey || c.key;
          var keyIn = field('Key (lowercase-with-hyphens: the address rankings and links use; runs follow a rename)');
          keyIn.value = c.newKey; keyIn.maxLength = 60; keyIn.pattern = '[a-z0-9]+(-[a-z0-9]+)*';
          keyIn.addEventListener('input', function(){ c.newKey = keyIn.value.trim(); refresh(); });
        }
        var ruleIn = field('Rule (markdown)', 'textarea'); ruleIn.value = c.rule; ruleIn.rows = 4; ruleIn.maxLength = 2000;
        ruleIn.addEventListener('input', function(){ c.rule = ruleIn.value; refresh(); });
        var metricsRoot = el('div');
        metricsRoot.innerHTML = byId('med-skeleton').innerHTML;
        var metricsBox = metricsRoot.firstElementChild;
        el_.appendChild(metricsBox);
        var metricsEd = initMetricsEd(metricsBox, JSON.parse(c.metrics || '[]'));
        c.metricsEd = metricsEd;
        // the baseline takes the editor's own spelling of the same metrics,
        // so an untouched category never reads as changed
        if (!c.isNew) base.cats.forEach(function(b){ if (b.key === c.key && !b.metricsNormalized) { b.metrics = metricsEd.value(); b.metricsNormalized = true; } });
        c.metrics = metricsEd.value();
        metricsBox.addEventListener('input', refresh);
        metricsBox.addEventListener('click', function(){ setTimeout(refresh, 0); });
        // subcategories
        var subBox = el('div', 'subcats');
        subBox.appendChild(el('h4', '', 'Subcategories'));
        var choice = el('div', 'selchoice');
        choice.appendChild(el('span', 'dimname', 'Show them as'));
        ['buttons', 'dropdown'].forEach(function(v){
          var lab = el('label', 'check');
          var r = el('input'); r.type = 'radio'; r.name = 'subsel-' + (c.key || 'new' + c.tmp); r.value = v;
          r.checked = (c.subSelector || 'buttons') === v;
          r.addEventListener('change', function(){ c.subSelector = v; refresh(); });
          lab.appendChild(r); lab.appendChild(el('span', '', v === 'buttons' ? 'one button each (default)' : 'a dropdown (for long lists)'));
          choice.appendChild(lab);
        });
        subBox.appendChild(choice);
        var subList = el('div', 'sublist');
        function subRow(sc){
          var row = el('div', 'subrowed');
          row.appendChild(orderArrows(c.subs, sc, renderSubs));
          row.appendChild(el('code', 'subkey', sc.key || '(new)'));
          if (sc.deleted) {
            row.appendChild(el('span', 'rules', sc.label + ': marked for deletion'));
          } else {
            var l = el('input'); l.value = sc.label; l.maxLength = 80; l.placeholder = 'label';
            l.addEventListener('input', function(){ sc.label = l.value; refresh(); });
            var r = el('textarea'); r.value = sc.rule; r.maxLength = 2000; r.rows = 2; r.placeholder = 'rule fragment, markdown (optional)';
            r.addEventListener('input', function(){ sc.rule = r.value; refresh(); });
            row.appendChild(l); row.appendChild(r);
          }
          row.appendChild(el('span', 'actmeta', (sc.runs || 0) + ' run' + (sc.runs === 1 ? '' : 's')));
          var live = c.subs.filter(function(x){ return !x.deleted; });
          if (!sc.runs || live.length === 1) {
            var b = el('button', 'btn danger', sc.deleted ? 'Keep' : 'Delete'); b.type = 'button';
            b.addEventListener('click', function(){
              if (sc.isNew) { c.subs.splice(c.subs.indexOf(sc), 1); renderSubs(); return; }
              sc.deleted = !sc.deleted; renderSubs();
            });
            row.appendChild(b);
          }
          subList.appendChild(row);
        }
        function renderSubs(){ subList.innerHTML = ''; c.subs.forEach(subRow); refresh(); }
        renderSubs();
        subBox.appendChild(subList);
        var addRow = el('div', 'subrowed subadd');
        var addLabel = el('input'); addLabel.placeholder = 'new subcategory label, e.g. any%'; addLabel.maxLength = 80;
        var addBtn = el('button', 'btn leave', '+ Add a subcategory'); addBtn.type = 'button';
        addBtn.addEventListener('click', function(){
          if (!addLabel.value.trim()) { addLabel.focus(); return; }
          c.subs.push({key: '', label: addLabel.value.trim(), rule: '', runs: 0, isNew: true, tmp: ++newSeq});
          addLabel.value = '';
          renderSubs();
        });
        addRow.appendChild(addLabel); addRow.appendChild(addBtn);
        subBox.appendChild(addRow);
        el_.appendChild(subBox);
        return el_;
      }
      function renderCards(){
        box.innerHTML = '';
        draft.cats.forEach(function(c){ box.appendChild(card(c)); });
        refresh();
      }
      byId('ge-addcat').addEventListener('click', function(){
        var label = window.prompt('Label of the new category (e.g. 100% completion):');
        if (!label || !label.trim()) return;
        draft.cats.push({key: '', label: label.trim(), rule: '', runs: 0, metrics: '[]', subs: [], isNew: true, tmp: ++newSeq});
        renderCards();
      });

      // ---- the diff: what Save would do, in order ----
      function props(){
        return {released: byId('ge-released').value.trim(), unofficial: byId('ge-unofficial').checked ? 'yes' : 'no',
                discord: byId('ge-discord').value.trim(), website: byId('ge-website').value.trim(), rta: byId('ge-rta').value.trim(),
                rules: byId('ge-rules').value.trim()};
      }
      function plan(){
        var ops = [];
        var title = byId('ge-title').value.trim();
        if (title && title !== base.title) ops.push({what: 'title', run: function(){ return edit('game', gameEditData.game, 'title', title); }, done: function(){ base.title = title; }});
        var pv = props();
        ['released', 'unofficial', 'discord', 'website', 'rta', 'rules'].forEach(function(f){
          if (pv[f] !== base.props[f]) ops.push({what: f, run: function(){ return edit('game', gameEditData.game, f, pv[f]); }, done: function(){ base.props[f] = pv[f]; }});
        });
        var thumb = byId('ge-thumb');
        if (thumb.files && thumb.files[0]) ops.push({what: 'thumbnail', run: function(){
          var fd = form('game', gameEditData.game, 'thumbnail', ''); fd.append('thumbnail', thumb.files[0]);
          return post('/api/expert/edit', fd, saveBtn);
        }, done: function(){ thumb.value = ''; }});
        var selv = selectorChoice();
        if (selv !== base.selector) ops.push({what: 'category selector', run: function(){ return edit('category', gameEditData.game + ':*', 'selector', selv); }, done: function(){ base.selector = selv; }});
        var baseByKey = {}; base.cats.forEach(function(c){ baseByKey[c.key] = c; });
        // new categories first (the rest may refer to them)
        draft.cats.filter(function(c){ return c.isNew && !c.deleted; }).forEach(function(c){
          ops.push({what: 'add ' + c.label, run: function(){
            var fd = new FormData(); fd.append('game', gameEditData.game); fd.append('label', c.label);
            fd.append('rule', c.rule); fd.append('metrics', c.metricsEd ? c.metricsEd.value() : '[]');
            fd.append('reason', whyIn.value.trim());
            return post('/api/category/add', fd, saveBtn);
          }, done: function(res){ c.key = res.j.key; c.isNew = false; c.metrics = c.metricsEd ? c.metricsEd.value() : '[]';
                                  base.cats.push({key: c.key, label: c.label, rule: c.rule, runs: 0, metrics: c.metrics, subs: [], subSelector: 'buttons'}); }});
        });
        draft.cats.filter(function(c){ return !c.deleted; }).forEach(function(c){
          var b = baseByKey[c.key];
          if (b) {
            if (c.label !== b.label) ops.push({what: c.key + ' label', run: function(){ return edit('category', gameEditData.game + ':' + c.key, 'label', c.label); }, done: function(){ b.label = c.label; }});
            if (c.rule !== b.rule) ops.push({what: c.key + ' rule', run: function(){ return edit('category', gameEditData.game + ':' + c.key, 'rule', c.rule); }, done: function(){ b.rule = c.rule; }});
            var mv = c.metricsEd ? c.metricsEd.value() : c.metrics;
            if (mv !== b.metrics) ops.push({what: c.key + ' metrics', run: function(){ return edit('category', gameEditData.game + ':' + c.key, 'metrics', mv); }, done: function(){ b.metrics = mv; }});
            var ssv = c.subSelector || 'buttons';
            if (ssv !== (b.subSelector || 'buttons')) ops.push({what: c.key + ' subcategory selector', run: function(){ return edit('category', gameEditData.game + ':' + c.key, 'subSelector', ssv); }, done: function(){ b.subSelector = ssv; }});
          }
          // subcategories of this category
          c.subs.filter(function(x){ return x.isNew && !x.deleted; }).forEach(function(x){
            ops.push({what: c.key + '/' + x.label + ' add', run: function(){
              var fd = new FormData(); fd.append('game', gameEditData.game); fd.append('parent', c.key);
              fd.append('label', x.label); fd.append('rule', x.rule); fd.append('reason', whyIn.value.trim());
              return post('/api/category/add', fd, saveBtn);
            }, done: function(res){ x.key = res.j.key; x.isNew = false; x.runs = res.j.runs_moved || 0;
                                    var bb = baseByKey[c.key] || base.cats.filter(function(z){ return z.key === c.key; })[0];
                                    if (bb) bb.subs.push({key: x.key, label: x.label, rule: x.rule, runs: x.runs}); }});
          });
          var bsubs = {}; ((b && b.subs) || []).forEach(function(x){ bsubs[x.key] = x; });
          c.subs.filter(function(x){ return !x.isNew && !x.deleted; }).forEach(function(x){
            var bx = bsubs[x.key]; if (!bx) return;
            if (x.label !== bx.label) ops.push({what: c.key + '/' + x.key + ' label', run: function(){ return edit('category', gameEditData.game + ':' + c.key + '/' + x.key, 'label', x.label); }, done: function(){ bx.label = x.label; }});
            if (x.rule !== bx.rule && x.rule) ops.push({what: c.key + '/' + x.key + ' rule', run: function(){ return edit('category', gameEditData.game + ':' + c.key + '/' + x.key, 'rule', x.rule); }, done: function(){ bx.rule = x.rule; }});
          });
          c.subs.filter(function(x){ return x.deleted && !x.isNew; }).forEach(function(x){
            ops.push({what: c.key + '/' + x.key + ' delete', run: function(){
              var fd = new FormData(); fd.append('game', gameEditData.game); fd.append('option', c.key); fd.append('sub', x.key); fd.append('reason', whyIn.value.trim());
              return post('/api/category/delete', fd, saveBtn);
            }, done: function(){ c.subs.splice(c.subs.indexOf(x), 1); if (b) b.subs = b.subs.filter(function(z){ return z.key !== x.key; }); }});
          });
          // the key rename runs LAST for this category, so every op above
          // still finds it under the old address; runs follow on the server
          if (b && c.newKey && c.newKey !== c.key && /^[a-z0-9]+(-[a-z0-9]+)*$/.test(c.newKey)) {
            ops.push({what: c.key + ' key \u2192 ' + c.newKey, run: function(){ return edit('category', gameEditData.game + ':' + c.key, 'key', c.newKey); },
                      done: function(){ b.key = c.newKey; c.key = c.newKey; }});
          }
        });
        draft.cats.filter(function(c){ return c.deleted && !c.isNew; }).forEach(function(c){
          ops.push({what: c.key + ' delete', run: function(){
            var fd = new FormData(); fd.append('game', gameEditData.game); fd.append('option', c.key); fd.append('reason', whyIn.value.trim());
            return post('/api/category/delete', fd, saveBtn);
          }, done: function(){ draft.cats.splice(draft.cats.indexOf(c), 1); base.cats = base.cats.filter(function(z){ return z.key !== c.key; }); }});
        });
        // orders last, once the sets agree
        var wantOrder = draft.cats.filter(function(c){ return !c.deleted; }).map(function(c){ return c.key || ('new:' + c.tmp); });
        var haveOrder = base.cats.map(function(c){ return c.key; });
        if (wantOrder.join(',') !== haveOrder.join(',') && wantOrder.length === haveOrder.length) {
          ops.push({what: 'category order', run: function(){
            var fd = new FormData(); fd.append('game', gameEditData.game);
            fd.append('order', draft.cats.filter(function(c){ return !c.deleted; }).map(function(c){ return c.key; }).join(','));
            fd.append('reason', whyIn.value.trim());
            return post('/api/category/reorder', fd, saveBtn);
          }, done: function(){ var k = {}; base.cats.forEach(function(c){ k[c.key] = c; }); base.cats = draft.cats.filter(function(c){ return !c.deleted; }).map(function(c){ return k[c.key]; }); }});
        } else if (wantOrder.length !== haveOrder.length) {
          // adds or deletes pending: the order is settled on the next save
          ops.orderLater = true;
        }
        draft.cats.filter(function(c){ return !c.deleted && !c.isNew; }).forEach(function(c){
          var b = baseByKey[c.key]; if (!b) return;
          var want = c.subs.filter(function(x){ return !x.deleted; }).map(function(x){ return x.key || ('new:' + x.tmp); });
          var have = b.subs.map(function(x){ return x.key; });
          if (want.length === have.length && want.join(',') !== have.join(',')) {
            ops.push({what: c.key + ' subcategory order', run: function(){
              var fd = new FormData(); fd.append('game', gameEditData.game); fd.append('option', c.key);
              fd.append('order', c.subs.filter(function(x){ return !x.deleted; }).map(function(x){ return x.key; }).join(','));
              fd.append('reason', whyIn.value.trim());
              return post('/api/category/reorder', fd, saveBtn);
            }, done: function(){ var k = {}; b.subs.forEach(function(x){ k[x.key] = x; }); b.subs = c.subs.filter(function(x){ return !x.deleted; }).map(function(x){ return k[x.key]; }); }});
          }
        });
        return ops;
      }
      function form(kind, target, field, value){
        var fd = new FormData();
        fd.append('kind', kind); fd.append('target', target); fd.append('field', field);
        fd.append('value', value); fd.append('reason', whyIn.value.trim());
        return fd;
      }
      function edit(kind, target, field, value){
        return post('/api/expert/edit', form(kind, target, field, value), saveBtn);
      }
      var dirty = false;
      function refresh(){
        var ops = plan();
        dirty = ops.length > 0;
        pendingEl.textContent = ops.length ? ops.length + ' change' + (ops.length === 1 ? '' : 's') + ' pending: ' +
          ops.map(function(o){ return o.what; }).join(', ') : 'No changes yet';
        saveBtn.disabled = !ops.length;
      }
      ['ge-title', 'ge-released', 'ge-unofficial', 'ge-discord', 'ge-website', 'ge-rta', 'ge-rules', 'ge-thumb'].forEach(function(id){
        byId(id).addEventListener('input', refresh);
        byId(id).addEventListener('change', refresh);
      });
      window.addEventListener('beforeunload', function(ev){
        if (!dirty) return;
        ev.preventDefault(); ev.returnValue = '';
      });

      // ---- Save: the sequence, stopped by the first failure ----
      saveBtn.addEventListener('click', function(){
        var ops = plan();
        if (!ops.length) return;
        if (whyIn.value.trim().length < 8) { whyIn.focus(); note(msg, 'Say why, publicly: at least 8 characters.', false); return; }
        msg.hidden = true;
        var n = 0, lastSerial = null;
        (function step(){
          if (!ops.length) {
            lastBtn = saveBtn;
            renderCards();
            noteBuilt(msg, 'Saved ' + n + ' change' + (n === 1 ? '' : 's') + '.', lastSerial);
            return;
          }
          var op = ops.shift();
          op.run().then(function(res){
            if (res.ok && res.j.ok) { op.done(res); n++; lastSerial = res.j.serial || lastSerial; step(); }
            else {
              lastBtn = saveBtn;
              note(msg, op.what + ': ' + (res.j.error || 'something went wrong') +
                (n ? ' (' + n + ' change' + (n === 1 ? '' : 's') + ' before it went through)' : ''), false);
              renderCards();
            }
          });
        })();
      });
      renderCards();
    });
  }

  // ---- likes (any run page) ----
  var likeDataEl = document.getElementById('likedata');
  if (likeDataEl) {
    var likeData = JSON.parse(likeDataEl.textContent);
    var likeBtn = document.getElementById('likebtn');
    mePromise.then(function(d){
      if (!likeBtn) return;
      if (d.unreachable) { likeBtn.disabled = true; return; }
      if (!d.loggedIn) {
        likeBtn.addEventListener('click', function(){ location.href = api + '/login'; });
        likeBtn.title = 'Log in to like this run';
        return;
      }
      var myName = d.user.toLowerCase();
      if (likeData.authors.indexOf(myName) >= 0) {
        likeBtn.disabled = true;
        likeBtn.title = 'Authors cannot like their own run';
        return;
      }
      // the same star both ways: press to like, press again to take it back
      function paint(liked){
        likeBtn.classList.toggle('on', liked);
        likeBtn.title = liked ? 'You like this run; press again to take it back'
                           : 'Like this run';
      }
      paint(likeData.likes.indexOf(myName) >= 0);
      likeBtn.addEventListener('click', function(){
        var fd = new FormData();
        fd.append('run', likeData.run);
        post('/api/like', fd, likeBtn).then(function(res){
          if (res.ok && res.j.ok) {
            document.getElementById('likecount').textContent = res.j.likes;
            paint(res.j.liked);
          }
        });
      });
    });
    mePromise.then(function(d){
      if (!d.loggedIn || d.unreachable) return;
      var reportBox = document.getElementById('reportbox');
      if (!reportBox) return;
      reportBox.hidden = false;
      var reportForm = document.getElementById('f-report');
      reportForm.addEventListener('submit', function(ev){
        ev.preventDefault();
        var fd = new FormData(reportForm);
        fd.append('run', likeData.run);
        var msg = document.getElementById('report-msg');
        post('/api/report', fd, actionBtn(reportForm)).then(function(res){
          if (res.ok && res.j.ok) {
            reportBox.hidden = true;
            note(msg, 'Report ' + res.j.report + ' filed. ' + res.j.note, true);
          } else note(msg, res.j.error || 'something went wrong', false);
        });
      });
    });
  }

  // ---- 18+ gate for sexual-content flags ----
  function adultOk(){
    try { return sessionStorage.getItem('tar-adult') === '1'; } catch (e) { return false; }
  }
  // drop the blur and the 18+ buttons once the reader has said yes
  function unblurAll(){
    document.querySelectorAll('.nsfwblur').forEach(function(i){ i.classList.remove('nsfwblur'); });
    document.querySelectorAll('.nsfw18').forEach(function(b){ b.remove(); });
  }
  // the gate box is swapped for the real content it stands in front of
  var nsfwGate = document.getElementById('nsfwgate');
  function revealGate(){
    var realContent = document.getElementById('nsfwreal');
    if (nsfwGate && realContent) nsfwGate.replaceWith(realContent.content.cloneNode(true));
  }
  // the page-level gate: the page is blurred and inert behind the
  // declaration until the reader says yes; no JS, it simply stays shut
  var ageGate = document.getElementById('agegate');
  function liftAgeGate(){
    if (ageGate) ageGate.remove();   // the blur is keyed on its presence (CSS :has)
  }
  if (adultOk()) { unblurAll(); revealGate(); liftAgeGate(); }
  // the 18+ yes buttons: remembered for the tab
  function adultYes(){
    try { sessionStorage.setItem('tar-adult', '1'); } catch (e) {}
    unblurAll(); revealGate(); liftAgeGate();
  }
  var adultOkBtn = document.getElementById('nsfwok');
  if (adultOkBtn) adultOkBtn.addEventListener('click', adultYes);
  var ageGateYes = document.getElementById('agegate-yes');
  if (ageGateYes) ageGateYes.addEventListener('click', adultYes);

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

  // ---- submit page ----
  var submitForm = document.getElementById('submitform');
  // the same page edits a run: /submit/?edit=M1234
  var editRunId = null;
  try { var _e = new URLSearchParams(location.search).get('edit'); if (_e && /^M\d+$/.test(_e)) editRunId = _e; } catch (e) {}
  if (submitForm) {
    var gameData = JSON.parse(document.getElementById('gamedata').textContent);
    var gameTitles = gameData.games;
    mePromise.then(function(d){
      var msg = document.getElementById('s-msg');
      if (d.unreachable) { note(msg, 'The archivist is not reachable right now; try again later.', false); return; }
      if (!d.loggedIn) { document.getElementById('s-login').hidden = false; return; }
      submitForm.hidden = false;
      // half-written submissions are easy to lose to a stray click: once
      // anything in the form changes, leaving asks the standard are-you-sure
      var submitFormDirty = false;
      // Checks input elements of Step 1. If all of them are empty, don't show "Leave page?" browser warning, if user leaves 
      function noteMeaningfulFormChange(ev){
        var t = ev && ev.target;
        if (ev && ev.isTrusted === false) return;
        if (!t) { submitFormDirty = true; return; }
        if (t.id === 's-gamesearch' || (t.closest && t.closest('#s-uncldesc'))) {
          if (!(t.value || '').trim()) return;
        }
        submitFormDirty = true;
      }
      submitForm.addEventListener('input', noteMeaningfulFormChange);
      submitForm.addEventListener('change', noteMeaningfulFormChange);
      window.addEventListener('beforeunload', function(ev){
        if (!submitFormDirty) return;
        ev.preventDefault();
        ev.returnValue = '';
      });
      // the movie file is optional; without one the run is video-only

      var movieWrap = document.getElementById('s-moviewrap');
      var timeWrap = document.getElementById('s-timewrap');
      // the stated time is four number boxes (h m s ms): a format mistake is
      // impossible, and the canonical [h:]mm:ss.mmm value is composed here
      var timeSegments = ['t-h', 't-m', 't-s', 't-ms'].map(function(id){
        return document.getElementById(id);
      });
      var timeField = document.getElementById('s-time');
      // a restored draft hands back seconds through the hidden field; the
      // segments have to show them, or composeTime wipes the value
      timeField.fill = function(v){
        var m = /^(?:(\d+):)?(\d+):(\d+)(?:\.(\d+))?$/.exec(String(v || '').trim());
        if (!m) return;
        var sec = (+(m[1] || 0)) * 3600 + (+m[2]) * 60 + (+m[3]) + (+((m[4] || '').padEnd(3, '0')) / 1000 || 0);
        byIdS('t-h').value = Math.floor(sec / 3600) || '';
        byIdS('t-m').value = Math.floor(sec / 60) % 60;
        byIdS('t-s').value = Math.floor(sec) % 60;
        byIdS('t-ms').value = Math.round((sec % 1) * 1000);
      };
      function pad2(n){ return (n < 10 ? '0' : '') + n; }
      function composeTime(){
        if (!timeField) return;
        var v = timeSegments.map(function(seg){
          if (!seg || seg.value === '') return 0;
          var n = parseInt(seg.value, 10) || 0;
          var max = parseInt(seg.getAttribute('max') || seg.dataset.max || '999', 10);
          n = Math.max(0, Math.min(max, n));
          if (String(n) !== seg.value) seg.value = n;
          return n;
        });
        var h = v[0], m = v[1], s = v[2], ms = v[3];
        var body = pad2(m) + ':' + pad2(s) + '.' + ('00' + ms).slice(-3);
        timeField.value = (h + m + s + ms) === 0 ? '' : (h > 0 ? h + ':' : '') + body;
      }
      timeSegments.forEach(function(seg){
        if (seg) seg.addEventListener('input', composeTime);
      });
      var gameSelect = document.getElementById('s-game'), goalSelect = document.getElementById('s-goal');
      // the category's stated metrics: fields appear on category pick, in
      // the dashed box; every value is the author's to state, time included
      var metricsBox = document.getElementById('s-metrics');
      var metricFields = document.getElementById('s-mfields');
      var curMetrics = null;   // null = classic (real time, lower is better)
      function wantsTime(){
        if (goalSelect.value === 'unclassified') return true;
        return !curMetrics || curMetrics.some(function(m){ return m.key === 'time'; });
      }
      function statedDefs(){
        if (goalSelect.value === 'unclassified') return [];
        return (curMetrics || []).filter(function(m){ return m.key !== 'time'; });
      }
      function secsOf(v){ return v === '' ? 0 : (parseFloat(v) || 0); }
      function buildMetricFields(){
        metricFields.innerHTML = '';
        statedDefs().forEach(function(m){
          var lab = document.createElement('label');
          lab.textContent = m.label + (m.unit ? ' (' + m.unit + ')' : '')
            + ' · ' + (m.better === 'higher' ? 'higher' : 'lower') + ' is better';
          metricFields.appendChild(lab);
          var hiddenField = document.createElement('input');
          hiddenField.type = 'hidden'; hiddenField.name = 'metric_' + m.key;
          if (m.type === 'time') {
            // segmented h/m/s/ms, composed into seconds
            var wrap = document.createElement('div');
            wrap.className = 'timepick';
            var segs = [['h', 999], ['m', 59], ['s', 59], ['ms', 999]].map(function(segSpec){
              var span = document.createElement('span'); span.className = 'tseg';
              var inp = document.createElement('input');
              inp.type = 'number'; inp.inputMode = 'numeric';
              inp.min = 0; inp.max = segSpec[1]; inp.placeholder = segSpec[0] === 'h' ? '0' : '00';
              var segLabel = document.createElement('label'); segLabel.textContent = segSpec[0];
              span.appendChild(inp); span.appendChild(segLabel); wrap.appendChild(span);
              return inp;
            });
            function compose(){
              var v = segs.map(function(seg){ return Math.max(0, parseInt(seg.value, 10) || 0); });
              hiddenField.value = String(v[0] * 3600 + v[1] * 60 + v[2] + v[3] / 1000);
            }
            segs.forEach(function(seg){ seg.addEventListener('input', compose); });
            segs[2].required = true;
            compose();
            // the record (edit mode) or a draft sets the value in seconds; the
            // segments have to show it, or the empty seconds box blocks Save
            hiddenField.fill = function(secs){
              var t = Math.max(0, parseFloat(secs) || 0);
              var h = Math.floor(t / 3600), mnt = Math.floor((t % 3600) / 60), sec = Math.floor(t % 60);
              var ms = Math.round((t - Math.floor(t)) * 1000);
              segs[0].value = h || ''; segs[1].value = mnt; segs[2].value = sec; segs[3].value = ms || '';
              compose();
            };
            metricFields.appendChild(wrap);
          } else {
            var numberInput = document.createElement('input');
            numberInput.type = 'number'; numberInput.min = 0; numberInput.step = 'any';
            numberInput.required = true; numberInput.inputMode = 'decimal';
            numberInput.addEventListener('input', function(){ hiddenField.value = String(secsOf(numberInput.value)); });
            hiddenField.fill = function(v){ numberInput.value = v; hiddenField.value = String(secsOf(numberInput.value)); };
            // a numeric metric can take the movie's frame or step count (for
            // categories that rank by frames, steps, ticks); never automatic
            var numberRow = document.createElement('div');
            numberRow.className = 'timerow';
            var fromBtn = document.createElement('button');
            fromBtn.type = 'button'; fromBtn.className = 'btn quiet mfrom'; fromBtn.disabled = true;
            fromBtn.textContent = 'From movie';
            fromBtn.addEventListener('click', function(){
              if (!(movieInfo && movieInfo.parsed && movieInfo.frames)) return;
              numberInput.value = movieInfo.frames;
              numberInput.dispatchEvent(new Event('input'));
              paintPanels();
            });
            numberRow.appendChild(numberInput); numberRow.appendChild(fromBtn);
            metricFields.appendChild(numberRow);
          }
          metricFields.appendChild(hiddenField);
        });
        paintTimeImport();
      }
      // what the archivist read from the picked movie (see /api/movie/inspect):
      // null until a file is picked; parsed=false for a known format it
      // cannot read, in which case the time is stated by hand
      var movieInfo = null;
      var movieInput = movieWrap.querySelector('input');
      var movieNote = document.getElementById('s-movienote');
      var movieMark = document.getElementById('s-moviemark');
      function setMovieMark(state, title){
        if (!movieMark) return;
        movieMark.className = 'bmark' + (state ? ' ' + state : '');
        movieMark.title = title || '';
      }
      movieInput.addEventListener('change', function(){
        var file = movieInput.files && movieInput.files[0];
        movieInfo = null;
        if (!file) { movieNote.hidden = true; setMovieMark('', ''); paintKind(); paintPanels(); return; }
        movieNote.hidden = false; movieNote.className = 'rules fullw';
        movieNote.textContent = 'reading ' + file.name + '…';
        setMovieMark('spin', 'reading the movie…');
        var fd = new FormData(); fd.append('movie', file); fd.append('game', gameSelect.value);
        fetch(api + '/api/movie/inspect', {method: 'POST', body: fd, credentials: 'include'})
          .then(function(r){ return r.json(); })
          .then(function(j){
            if (!j.ok) {
              movieInfo = {error: j.error || 'not a movie file'};
              movieNote.textContent = '✗ ' + movieInfo.error;
              movieNote.className = 'rules fullw enc-bad';
              setMovieMark('bad', movieInfo.error);
            } else {
              movieInfo = j;
              // an unreadable or unknown file is a warning, never a stop: the
              // archive keeps the file as it is and the author states the values
              if (j.parsed) {
                movieNote.className = 'rules fullw';
                movieNote.textContent = '✓ .' + j.format + ': ' + (j.frames || 0).toLocaleString() + ' frames' + (j.seconds ? ', ' + secClock(j.seconds) : '') + (j.rerecords ? ', ' + j.rerecords.toLocaleString() + ' rerecords' : '');
                setMovieMark('ok', 'movie read: ' + (j.frames || 0).toLocaleString() + ' frames');
              } else {
                var why = j.known ? '.' + j.format + ' is a known format the archive could not read' + (j.error ? ' (' + j.error + ')' : '')
                                  : '.' + j.format + ' is not a format the archive can read';
                movieNote.className = 'rules fullw notewarn';
                movieNote.textContent = '! ' + why + '. You can continue: the file is archived exactly as it is, '
                  + 'but nothing could be parsed, so nothing is importable from the movie.';
                setMovieMark('warn', why);
              }
            }
            paintKind(); paintPanels();
          })
          .catch(function(){
            movieInfo = {error: 'could not reach the archivist to read the movie'};
            movieNote.textContent = '✗ ' + movieInfo.error;
            movieNote.className = 'rules fullw enc-bad';
            setMovieMark('bad', movieInfo.error);
            paintKind(); paintPanels();
          });
      });
      function secClock(sec){
        var h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, s2 = Math.floor(sec) % 60, ms = Math.round((sec % 1) * 1000);
        var body = String(m).padStart(2, '0') + ':' + String(s2).padStart(2, '0') + '.' + String(ms).padStart(3, '0');
        return h ? h + ':' + body : body;
      }
      var scoreNote = document.getElementById('s-scorenote');
      // the run's time is the one the author states, always: never filled in
      // for them. Import from movie reads it out of a parsed file on demand.
      function timeStatedNeeded(){
        return wantsTime() && goalSelect.value !== 'unclassified';
      }
      // one Import from… selector for the time: its options are the sources
      // the form has actually seen (a parsed movie, a checked encode)
      var timeImportSel = document.getElementById('s-timeimport');
      var encodeSeconds = null;   // from /api/encode/check, when the platform says
      function importSources(){
        return {movie: (movieInfo && movieInfo.parsed && movieInfo.seconds) || null,
                encode: encodeSeconds || null};
      }
      function paintTimeImport(){
        if (timeImportSel) {
          var src = importSources();
          var any = false;
          Array.prototype.forEach.call(timeImportSel.options, function(o){
            if (!o.value) return;
            var sec = src[o.value];
            o.disabled = !sec;
            o.hidden = !sec;   // a removed movie takes its option away entirely
            o.textContent = (o.value === 'movie' ? 'the movie file' : 'the video encode')
                          + (sec ? ' · ' + secClock(sec) : '');
            if (sec) any = true;
          });
          timeImportSel.disabled = !any;
          timeImportSel.title = any ? 'Fill the time from a source the form has checked'
                                    : 'Enabled when the movie file parses, or the encode names its length';
        }
        // the numeric metrics' own import: the movie's frame or step count
        var canFrames = !!(movieInfo && movieInfo.parsed && movieInfo.frames);
        submitForm.querySelectorAll('.mfrom').forEach(function(btn){
          btn.disabled = !canFrames;
          btn.title = canFrames ? 'Fill with the movie\u2019s frame count: ' + movieInfo.frames.toLocaleString()
                                : 'Enabled when the movie file could be parsed';
        });
      }
      if (timeImportSel) timeImportSel.addEventListener('change', function(){
        var sec = importSources()[timeImportSel.value];
        timeImportSel.value = '';
        if (!sec) return;
        byIdS('t-h').value = Math.floor(sec / 3600) || '';
        byIdS('t-m').value = Math.floor(sec / 60) % 60;
        byIdS('t-s').value = Math.floor(sec) % 60;
        byIdS('t-ms').value = Math.round((sec % 1) * 1000);
        composeTime();
        paintPanels();
      });
      function paintKind(){
        // in edit mode the movie stays as it is: only experts see the picker,
        // to replace it; a video-only run has nothing to replace
        movieWrap.hidden = !!(editRunId && (!(editMay && (editMay.expert || editMay.editor))
                                            || (editRecord && editRecord.run.videoOnly)));
        var stated = timeStatedNeeded();
        timeWrap.hidden = !stated;
        var secs = document.getElementById('t-s');
        if (secs) secs.required = stated;
        paintTimeImport();
        var n = statedDefs().length;
        scoreNote.textContent = goalSelect.value === 'unclassified'
          ? 'Unclassified runs rank by likes alone: nothing to score.'
          : (stated ? (n ? 'This category ranks by time, which you state, and by the values below.' : 'This category ranks by time, which you state.')
                    : 'This category ranks by the values below.');
        composeTime();
      }
      paintKind();
      var goalCache = {};
      var pendingDraft = null;   // a restored draft waiting for its game's categories
      function fillGoals(goals, loaded){
        goalSelect.innerHTML = '';
        (goals || []).forEach(function(g){
          var o = document.createElement('option');
          o.value = g.key; o.textContent = g.label;
          goalSelect.appendChild(o);
        });
        var u = document.createElement('option');
        u.value = 'unclassified'; u.textContent = 'Unclassified (no goal; ranked by likes)';
        goalSelect.appendChild(u);
        // a draft is applied once the game's categories are known, even when
        // there are none (Unclassified only): `loaded` says they arrived
        if (pendingDraft && loaded && pendingDraft.game === gameSelect.value) {
          var d = pendingDraft; pendingDraft = null;
          if (d.goal) goalSelect.value = d.goal;
          paintCategory();
          if (d.sub) subSelect.value = d.sub;
          applyDraftFields(d.fields);
          paintPanels();
          return;
        }
        paintCategory();
      }
      function loadGoals(){
        // categories arrive from the archive itself when a game is picked:
        // the page ships no per-game payload up front
        var createCategoryBtn = document.getElementById('s-createcat');
        if (createCategoryBtn) {
          if (gameSelect.value) {
            createCategoryBtn.href = '../create-category/?game=' + encodeURIComponent(gameSelect.value);
            createCategoryBtn.removeAttribute('aria-disabled');
          } else {
            createCategoryBtn.removeAttribute('href');
            createCategoryBtn.setAttribute('aria-disabled', 'true');
          }
        }
        if (!gameSelect.value) { fillGoals([]); return; }
        if (goalCache[gameSelect.value]) { fillGoals(goalCache[gameSelect.value], true); return; }
        var key = gameSelect.value;
        fillGoals([]);
        // the archivist serves its checkout, at most ~20 s old; the raw-file
        // CDN (5-minute cache) is only the fallback when it is unreachable
        fetch(api + '/api/categories?game=' + encodeURIComponent(key))
          .then(function(r){ return r.ok ? r.json() : Promise.reject(); })
          .catch(function(){
            return fetch(gameData.raw + '/games/' + key + '/categories.json')
              .then(function(r){ return r.ok ? r.json() : null; });
          })
          .then(function(cats){
            if (!cats || gameSelect.value !== key) return;
            var goals = [];
            (cats.dimensions || []).forEach(function(dim){
              (dim.options || []).forEach(function(o){
                goals.push({key: o.key, label: o.label, metrics: o.metrics || null,
                            subcategories: o.subcategories || []});
              });
            });
            goalCache[key] = goals;
            fillGoals(goals, true);
          }).catch(function(){});
      }
      // the subcategory select: only when the chosen category has some
      var subWrap = document.getElementById('s-subwrap'), subSelect = document.getElementById('s-sub');
      function paintSub(picked){
        var subs = (picked && picked.subcategories) || [];
        subSelect.innerHTML = '';
        subs.forEach(function(sc){
          var o = document.createElement('option');
          o.value = sc.key; o.textContent = sc.label;
          subSelect.appendChild(o);
        });
        subWrap.hidden = !subs.length;
        subSelect.disabled = !subs.length;   // a disabled field sends nothing
      }
      function paintCategory(){
        if (typeof paintPanels === 'function') setTimeout(paintPanels, 0);   // the goals arrive later than the pick
        if (editRecord) setTimeout(function(){
          // the record's stated values into the fields the category defines
          var mv = editRecord.run.metrics || {};
          Object.keys(mv).forEach(function(k){
            var h = submitForm.querySelector('input[name="metric_' + k + '"]');
            if (!h) return;
            if (h.fill) h.fill(mv[k]); else h.value = mv[k];
          });
          paintPanels();
        }, 0);
        document.getElementById('s-uncldesc').hidden = goalSelect.value !== 'unclassified';
        var goals = goalCache[gameSelect.value] || [];
        var picked = goals.filter(function(g){ return g.key === goalSelect.value; })[0];
        paintSub(picked);
        curMetrics = (picked && picked.metrics) || null;
        buildMetricFields();
        paintKind();
      }
      goalSelect.addEventListener('change', paintCategory);

      // the game picker: type to find, nothing rendered until you type, and
      // the way out is always offered ("Add a new game")
      // ---- the draft: what was typed survives a closed window. Saved to
      // this browser's localStorage on every change (never the movie file,
      // which browsers refuse to restore), restored on the next visit,
      // dropped on a successful submit or on Clear. ----
      var DRAFT_KEY = 'tar-submit-draft';
      var draftNote = document.getElementById('s-draftnote');
      var draftTimer = null;
      function draftFields(){
        // every named control except files, hidden plumbing and the consent
        // box (that one is signed each time); repeated names become arrays
        var out = {};
        Array.prototype.forEach.call(submitForm.elements, function(e){
          if (!e.name || e.type === 'file' || e.type === 'button' || e.type === 'submit') return;
          if (e.name === 'consent' || e.name === 'game' || e.name === 'goal' || e.name === 'sub') return;
          var v = (e.type === 'checkbox') ? (e.checked ? e.value : '') : e.value;
          if (e.name in out) { if (!Array.isArray(out[e.name])) out[e.name] = [out[e.name]]; out[e.name].push(v); }
          else out[e.name] = v;
        });
        return out;
      }
      function applyDraftFields(fields){
        // file rows first, so their inputs exist to be filled
        var rowsBox = submitForm.querySelector('.filerows');
        var names = [].concat(fields.file_name || []), shas = [].concat(fields.file_sha1 || []);
        if (rowsBox && names.length) {
          rowsBox.querySelectorAll('.filerow').forEach(function(r){ r.remove(); });
          names.forEach(function(){ rowsBox.querySelector('.addfile').click(); });
        }
        var seen = {};
        Array.prototype.forEach.call(submitForm.elements, function(e){
          if (!e.name || !(e.name in fields) || e.type === 'file') return;
          var val = fields[e.name];
          if (Array.isArray(val)) { var i = seen[e.name] || 0; seen[e.name] = i + 1; val = val[i]; if (val === undefined) return; }
          if (e.type === 'checkbox') e.checked = (val === e.value);
          else if (e.fill) e.fill(val);
          else e.value = val;
          e.dispatchEvent(new Event('input', {bubbles: false}));
        });
        if (rowsBox && names.length) {
          var shaIns = rowsBox.querySelectorAll('input[name=file_sha1]');
          shas.forEach(function(v, i){ if (shaIns[i]) { shaIns[i].value = v; shaIns[i].dispatchEvent(new Event('input')); } });
        }
        paintKind();
        var pick = submitForm.querySelector('.authpick');
        if (fields.authors && pick && pick.setAuthors) pick.setAuthors(fields.authors.split(',').filter(Boolean));
      }
      function stamp(t){
        var d = new Date(t);
        return d.toLocaleDateString(undefined, {day: 'numeric', month: 'short'}) + ' ' +
               d.toLocaleTimeString(undefined, {hour: '2-digit', minute: '2-digit', second: '2-digit'});
      }
      function saveDraft(){
        if (editRunId) return;   // an edit is not a draft of a new run
        // an untouched form is not a draft: the author picker seeds your own
        // name, which is no reason to claim one was saved
        var fields = draftFields();
        var typed = gameSelect.value || Object.keys(fields).some(function(k){
          return k !== 'authors' && k !== 'goal_description' && [].concat(fields[k]).some(function(v){ return v; });
        });
        if (!typed) return;
        var data = {t: new Date().toISOString(), game: gameSelect.value, goal: goalSelect.value,
                    sub: subSelect.disabled ? '' : subSelect.value, fields: fields};
        try { localStorage.setItem(DRAFT_KEY, JSON.stringify(data)); } catch (e) { return; }
        draftNote.hidden = false;
        draftNote.textContent = 'Draft saved ' + stamp(data.t);
      }
      function dropDraft(){
        try { localStorage.removeItem(DRAFT_KEY); } catch (e) {}
        draftNote.hidden = true;
      }
      // ---- edit mode: the record prefilled, every panel open, the game
      // fixed, category and subcategory the expert's alone, Save instead of
      // Submit, the voiding rules asked before sending ----
      var editRecord = null, editMay = null;
      function enterEditMode(d){
        byIdS('s-title').textContent = 'Edit run';
        byIdS('s-subtitle').textContent = 'Loading ' + editRunId + '…';
        byIdS('s-editback').hidden = false;
        byIdS('s-editback-link').href = '../runs/' + editRunId + '/';
        byIdS('s-clear').textContent = 'Discard changes';
        byIdS('s-submit').textContent = 'Save changes';
        byIdS('s-draftnote').hidden = true;
        fetch(api + '/api/run/record?run=' + encodeURIComponent(editRunId), {credentials: 'include'})
          .then(function(r){ return r.json(); })
          .then(function(j){
            if (!j.ok) { byIdS('s-subtitle').textContent = j.error || 'could not load the run'; submitForm.hidden = true; return; }
            editRecord = j; editMay = j.may;
            var run = j.run;
            var mayEdit = editMay.author || editMay.expert || editMay.editor;
            if (!mayEdit) { byIdS('s-subtitle').textContent = 'Only the authors of ' + editRunId + ', the experts covering ' + j.game.title + ' and editors may edit it.'; submitForm.hidden = true; return; }
            byIdS('s-subtitle').textContent = editRunId + ' · ' + j.game.title + ' · by ' + run.authors.map(function(a){ return a.user; }).join(', ') + (editMay.author ? '' : ' · expert mode');
            // panel 1: the game is fixed; category and subcategory only for experts and editors
            var expertish = editMay.expert || editMay.editor;
            gameTitles[j.game.key] = gameTitles[j.game.key] || j.game.title;
            pendingDraft = {game: j.game.key, goal: run.category.goal, sub: run.category.sub || '', fields: {}};
            pickGame(j.game.key);
            gamePick.hidden = true; gameLocked.hidden = false;
            byIdS('s-gamelockname').textContent = j.game.title;
            gameLocked.innerHTML = 'Game: <b>' + j.game.title + '</b> (a run never changes game)';
            document.querySelectorAll('#p-game .createq').forEach(function(e){ e.hidden = true; });
            if (!expertish) { goalSelect.disabled = true; subSelect.disabled = true; }
            // panel 2 and on: the record
            submitForm.querySelector('[name=encode]').value = ((run.encodes || [])[0] || {}).url || '';
            submitForm.querySelector('[name=encode]').dispatchEvent(new Event('input'));
            var pick = submitForm.querySelector('.authpick');
            if (pick && pick.setAuthors) pick.setAuthors(run.authors.map(function(a){ return a.user; }));
            if (!editMay.author) { pick.querySelector('.authsearch').disabled = true; pick.querySelectorAll('.authx').forEach(function(x){ x.disabled = true; }); }
            submitForm.querySelector('[name=completed]').value = run.completed || '';

            // the movie file stays; an expert may replace it
            movieInput.required = false;
            if (run.videoOnly) { movieWrap.hidden = true; }
            else if (expertish) { byIdS('s-movielabel').textContent = 'Replace the movie file (optional; the current one stays unless you pick another)'; }
            else { movieWrap.hidden = true; }
            movieInfo = run.videoOnly ? null : {parsed: !!(run.movie && run.movie.frames), frames: (run.movie || {}).frames,
              seconds: (run.movie && run.movie.frames && run.movie.fps) ? run.movie.frames / run.movie.fps : null,
              format: (run.movie || {}).format};
            submitForm.querySelector('[name=emulator]').value = (run.contract || {}).emulator || '';
            var files = (run.contract || {}).files || ((run.contract || {}).rom ? [run.contract.rom] : []);
            var rowsBox = submitForm.querySelector('.filerows');
            files.forEach(function(f){ rowsBox.querySelector('.addfile').click(); var rows = rowsBox.querySelectorAll('.filerow'); var row = rows[rows.length - 1]; row.querySelector('[name=file_name]').value = f.name || ''; row.querySelector('[name=file_sha1]').value = f.sha1 || ''; });
            if (!editMay.author) submitForm.querySelector('[name=attachments]').disabled = true;
            (run.contentWarnings || []).forEach(function(w){ var b = submitForm.querySelector('[name=content_warnings][value="' + w + '"]'); if (b) b.checked = true; });
            submitForm.querySelector('[name=notes]').value = j.notes || '';
            if (run.goalDescription) submitForm.querySelector('[name=goal_description]').value = run.goalDescription;
            if (!editMay.author) { byIdS('s-why').hidden = false; submitForm.querySelector('[name=reason]').required = true; }
            if (!editMay.author && !editMay.expert) {
              // an editor's reach is the library's shape: the category and
              // subcategory, nothing of the run itself
              submitForm.querySelectorAll('input, textarea, select').forEach(function(e){
                if (['goal', 'sub', 'reason'].indexOf(e.name) < 0 && e.id !== 's-goal' && e.id !== 's-sub') e.disabled = true;
              });
              byIdS('s-subtitle').textContent += ' · editor: category and subcategory only';
            }
            submitForm.querySelector('[name=consent]').closest('label').hidden = true;
            submitForm.querySelector('[name=consent]').checked = true;
            // every panel open: the record is complete by definition
            for (var i = 1; i <= 6; i++) revealed[i] = true;
            previewed = true;
            editStatedTime(run);
            paintKind(); paintPanels();
          }).catch(function(){ byIdS('s-subtitle').textContent = 'could not reach the archivist'; });
      }
      function editStatedTime(run){
        // the record's time, back into h m s ms: the stated duration, or,
        // for a legacy run that never stated one, the frames-derived value
        var sec = run.duration;
        if (!sec && run.movie && run.movie.frames && run.movie.fps) sec = run.movie.frames / run.movie.fps;
        if (!sec) return;
        byIdS('t-h').value = Math.floor(sec / 3600) || '';
        byIdS('t-m').value = Math.floor(sec / 60) % 60;
        byIdS('t-s').value = Math.floor(sec) % 60;
        byIdS('t-ms').value = Math.round((sec % 1) * 1000);
        composeTime();
      }
      function byIdS(id){ return document.getElementById(id); }

      // ---- the panels: each unfolds once the one before is complete, and
      // stays open from then on; the same rule unfolds a restored draft ----
      var panels = Array.prototype.slice.call(submitForm.querySelectorAll('.panel'));
      var revealed = {1: true};
      var previewed = false;
      // why a step is not done, in a few words (the first broken step is
      // named beside Submit once later panels are open)
      function panelWhy(step){
        if (step === 1) {
          if (!gameSelect.value) return 'pick the game';
          if (!goalSelect.value) return 'pick the category';
          if (!subWrap.hidden && !subSelect.value) return 'pick the subcategory';
          return 'describe what the run does';
        }
        if (step === 2) return document.getElementById('enc-status').className !== 'enc-good' ? 'the encode link is not valid' : 'name at least one author';
        if (step === 3) return 'the movie file could not be checked; pick it again or remove it';
        if (step === 4) return timeStatedNeeded() && !/^(\d+:)?\d{1,2}:\d{2}/.test(document.getElementById('s-time').value) ? 'state the time' : 'every value the category ranks by is needed';
        if (step === 5) return 'preview your notes';
        if (step === 6) return editRunId ? 'say why, publicly (at least 8 characters)' : 'tick the agreement';
        return '';
      }
      var panelNames = {1: 'Game and category', 2: 'The run', 3: 'Reproduction information', 4: 'Scoring', 5: 'Submission notes', 6: 'Agreement'};
      function panelDone(step){
        if (step === 1) {
          if (!gameSelect.value || !goalSelect.value) return false;
          if (!subWrap.hidden && !subSelect.value) return false;
          if (goalSelect.value === 'unclassified' && !submitForm.querySelector('[name=goal_description]').value.trim()) return false;
          return true;
        }
        if (step === 2) {
          return document.getElementById('enc-status').className === 'enc-good'
            && !!submitForm.querySelector('[name=authors]').value;
        }
        if (step === 3) {
          // everything here is optional; a picked movie only counts once the
          // archivist has looked at it (even an unreadable one is kept)
          if (movieInput.files && movieInput.files[0]) return !!(movieInfo && !movieInfo.error);
          return true;
        }
        if (step === 4) {
          // every value the category ranks by: stated metrics, and the time
          // whenever the category ranks by it (typed, or imported on demand)
          var ok = Array.prototype.every.call(submitForm.querySelectorAll('#s-mfields input[type=hidden]'), function(h){ return h.value !== '' && !isNaN(+h.value); });
          if (timeStatedNeeded() && !/^(\d+:)?\d{1,2}:\d{2}/.test(document.getElementById('s-time').value)) ok = false;
          return ok;
        }
        if (step === 5) return previewed;
        if (step === 6) return editRunId ? (editMay && editMay.author ? true : submitForm.querySelector('[name=reason]').value.trim().length >= 8) : submitForm.querySelector('[name=consent]').checked;
        return false;
      }
      function paintPanels(){
        var chain = true;   // a step counts only with every step before it
        var broken = null;  // the first step that is open but not done
        panels.forEach(function(pn){
          var step = +pn.dataset.step;
          var chainBefore = chain;
          var done = chain && panelDone(step);
          if (done && step < 6) revealed[step + 1] = true;
          pn.classList.toggle('folded', !revealed[step]);
          pn.classList.toggle('done', done);
          // the first step undone while later ones are open: say so on its
          // badge (the ones after it are merely waiting on it)
          var later = revealed[step + 1];
          var bad = chainBefore && !done && revealed[step] && later;
          pn.classList.toggle('broken', bad);
          if (bad && !broken) broken = step;
          chain = done;
        });
        // Submit only once every step is done (the movie is optional; a
        // restored draft cannot carry a file, so one is re-picked if wanted)
        var sb = document.getElementById('s-submit');
        if (sb && !sb.dataset.sent) sb.disabled = !chain;
        var need = document.getElementById('s-need');
        if (need) {
          var why = broken ? 'Step ' + broken + ' (' + panelNames[broken] + ') needs attention: ' + panelWhy(broken) + '.' : '';
          need.hidden = !why;
          need.textContent = why;
        }
      }
      submitForm.addEventListener('input', paintPanels);
      submitForm.addEventListener('change', paintPanels);
      submitForm.addEventListener('input', function(){ clearTimeout(draftTimer); draftTimer = setTimeout(saveDraft, 300); });
      submitForm.addEventListener('change', function(){ clearTimeout(draftTimer); draftTimer = setTimeout(saveDraft, 300); });
      document.getElementById('s-clear').addEventListener('click', function(){
        if (!window.confirm(editRunId ? 'Discard your changes and reload the archived record?'
                                      : 'Clear every field and discard the saved draft?')) return;
        if (!editRunId) dropDraft();   // the draft belongs to a new run, not to this edit
        submitFormDirty = false;
        location.reload();
      });
      function restoreDraft(){
        var d = null;
        try { d = JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null'); } catch (e) {}
        if (!d || !d.t || Date.now() - Date.parse(d.t) > 30 * 86400000) return false;
        draftNote.hidden = false;
        draftNote.textContent = 'Draft saved ' + stamp(d.t) + ' (restored)';
        if (d.game && gameTitles[d.game]) {
          pendingDraft = d;          // the category list arrives later
          pickGame(d.game);
        } else {
          applyDraftFields(d.fields);
        }
        paintPanels();
        return true;
      }

      var gamePick = document.getElementById('s-gamepick');
      var gameSearch = document.getElementById('s-gamesearch');
      var gameList = gamePick.querySelector('.gamelist');
      var gameLocked = document.getElementById('s-gamelocked');
      var gameKeys = Object.keys(gameTitles);
      function pickGame(key){
        gameSelect.value = key;
        gameSearch.value = gameTitles[key];
        gameList.hidden = true;
        loadGoals();
      }
      function fillGameList(){
        var q = gameSearch.value.trim().toLowerCase();
        gameList.innerHTML = '';
        if (q) {
          gameKeys.filter(function(k){
            return gameTitles[k].toLowerCase().indexOf(q) >= 0 || k.indexOf(q) >= 0;
          }).slice(0, 12).forEach(function(k){
            var row = el('div', 'authopt', gameTitles[k]);
            row.addEventListener('mousedown', function(ev){ ev.preventDefault(); pickGame(k); });
            gameList.appendChild(row);
          });
        }
        gameList.hidden = false;
      }
      gameSearch.addEventListener('input', fillGameList);
      gameSearch.addEventListener('focus', fillGameList);
      gameSearch.addEventListener('blur', function(){ setTimeout(function(){ gameList.hidden = true; }, 150); });

      // arriving from a game page: the context comes along, locked
      var presetGame = null;
      try { presetGame = new URLSearchParams(location.search).get('game'); } catch (e) {}
      initAuthorPick(submitForm.querySelector('.authpick'), [d.user]);
      // a draft restores first; a game named in the URL then overrides its game
      var restored = editRunId ? false : restoreDraft();
      if (!editRunId && presetGame && gameTitles[presetGame]) {
        if (restored && pendingDraft) pendingDraft.game = presetGame;
        pickGame(presetGame);
        gamePick.hidden = true;
        gameLocked.hidden = false;
        document.getElementById('s-gamelockname').textContent = gameTitles[presetGame];
        document.getElementById('s-gameunlock').addEventListener('click', function(ev){
          ev.preventDefault();
          gameLocked.hidden = true;
          gamePick.hidden = false;
          gameSearch.focus();
        });
      }
      if (!restored && !presetGame && !editRunId) fillGoals([]);
      paintPanels();
      if (editRunId) setTimeout(function(){ enterEditMode(d); }, 0);   // once every handler below is wired

      // live encode check: the thumbnail is derived from the encode, so the
      // link is validated as it is typed — preview frame + green check
      var encodeInput = document.getElementById('s-encode');
      var encodeCheck = document.getElementById('enc-check');
      var encodeThumb = document.getElementById('enc-thumb');
      var encodeStatus = document.getElementById('enc-status');
      // by id, never by class: 'button.btn' picks the FIRST such button in
      // the form, which is Preview, so the encode check disabled Preview on
      // load and left Submit ungated (the same mistake broke double-submit)
      var submitBtn = document.getElementById('s-submit');
      var encTimer = null;
      var encodeHosts = 'ENCODE_HOSTS'.split('|');
      function knownHost(u){
        var m = /^https?:\/\/([^\/:?#]+)/i.exec((u || '').trim());
        if (!m) return false;
        return encodeHosts.indexOf(m[1].toLowerCase().replace(/^www\./, '')) >= 0;
      }
      function checkEncode(){
        var url = encodeInput.value.trim();
        submitBtn.disabled = true;
        encodeThumb.hidden = true;
        encodeSeconds = null;
        paintTimeImport();
        if (!knownHost(url)) {
          encodeThumb.removeAttribute('src');
          encodeCheck.hidden = url === '';
          encodeStatus.textContent = '✗ not a link from ENCODE_NAMES';
          encodeStatus.className = 'enc-bad';
          paintPanels();
          return;
        }
        encodeCheck.hidden = false;
        encodeStatus.textContent = 'checking…';
        encodeStatus.className = 'enc-wait';
          paintPanels();
        // the archivist resolves it: several platforms reveal their thumbnail
        // only through an API a browser is not allowed to call
        var asked = url;
        fetch(api + '/api/encode/check?url=' + encodeURIComponent(url), {credentials: 'include'})
          .then(function(r){ return r.json(); })
          .then(function(j){
            if (encodeInput.value.trim() !== asked) return;      // the field moved on
            if (!j.ok) {
              encodeStatus.textContent = '✗ ' + (j.error || 'that link does not work');
              encodeStatus.className = 'enc-bad';
          paintPanels();
              return;
            }
            submitBtn.disabled = false;
            encodeStatus.textContent = '✓ ' + j.name +
              ' encode verified; this frame becomes the run thumbnail';
            encodeStatus.className = 'enc-good';
            encodeSeconds = (typeof j.seconds === 'number' && j.seconds > 0) ? j.seconds : null;
            paintTimeImport();
            paintPanels();
            if (j.thumb) {
              encodeThumb.onerror = function(){ encodeThumb.hidden = true; };
              encodeThumb.onload = function(){ encodeThumb.hidden = false; };
              encodeThumb.src = j.thumb;
            }
          })
          .catch(function(){
            encodeStatus.textContent = '✗ could not reach the archivist to check the link';
            encodeStatus.className = 'enc-bad';
          paintPanels();
          });
      }
      encodeInput.addEventListener('input', function(){
        clearTimeout(encTimer);
        encTimer = setTimeout(checkEncode, 400);
      });
      checkEncode();

      document.getElementById('s-preview-btn').addEventListener('click', function(){
        previewed = true; paintPanels();
        var preview = document.getElementById('s-preview');
        preview.hidden = false;
        var gameLabel = gameTitles[gameSelect.value] || '';
        var goalLabel = goalSelect.value === 'unclassified' ? 'Unclassified'
          : (goalSelect.options[goalSelect.selectedIndex] ? goalSelect.options[goalSelect.selectedIndex].text : '');
        document.getElementById('pv-title').textContent = gameLabel;
        document.getElementById('pv-chips').innerHTML =
          '<span class="chip">' + escapeHtml(goalLabel) + '</span><span class="chip pendchip">Pending</span>';
        document.getElementById('pv-authors').textContent =
          'by ' + (submitForm.querySelector('[name=authors]').value || '').split(',').join(', ');
        // the poster is whatever the live check already resolved and drew
        var poster = document.getElementById('pv-poster');
        if (!encodeThumb.hidden && encodeThumb.src) {
          document.getElementById('pv-thumb').src = encodeThumb.src;
          poster.hidden = false;
        } else poster.hidden = true;
        // the archivist renders the preview with the same code that renders
        // the published page, so what you see is what you will get (#30)
        var previewNotes = document.getElementById('pv-notes');
        previewNotes.textContent = 'Rendering…';
        var previewForm = new FormData();
        previewForm.append('notes', submitForm.querySelector('[name=notes]').value || '');
        fetch(api + '/api/preview', {method: 'POST', body: previewForm, credentials: 'include'})
          .then(function(r){ return r.json(); })
          .then(function(j){ previewNotes.innerHTML = j.ok ? j.html : escapeHtml(j.error || 'preview failed'); })
          .catch(function(){ previewNotes.textContent = 'The archivist is not reachable; the preview needs it.'; });
        preview.scrollIntoView({behavior: 'smooth', block: 'start'});
      });

      var submitting = false;
      // ---- saving an edit: the revision through /api/edit; an expert's
      // movie replacement and category move are their own logged edits ----
      function saveEdit(){
        var submitBtn = document.getElementById('s-submit');
        var run = editRecord.run, expertish = editMay.expert || editMay.editor;
        function revision(dry){
          var fd = new FormData();
          fd.append('run', editRunId);
          if (dry) fd.append('dry_run', '1');
          ['encode', 'emulator', 'completed', 'notes', 'goal_description'].forEach(function(n){
            var e = submitForm.querySelector('[name=' + n + ']'); if (e) fd.append(n === 'goal_description' ? 'goalDescription' : n, e.value);
          });
          if (editMay.author) fd.append('authors', submitForm.querySelector('[name=authors]').value);
          fd.append('content_warnings_set', '1');
          submitForm.querySelectorAll('[name=content_warnings]:checked').forEach(function(c){ fd.append('content_warnings', c.value); });
          fd.append('files_set', '1');
          submitForm.querySelectorAll('[name=file_name]').forEach(function(i){ fd.append('file_name', i.value); });
          submitForm.querySelectorAll('[name=file_sha1]').forEach(function(i){ fd.append('file_sha1', i.value); });
          submitForm.querySelectorAll('input[name^=metric_]').forEach(function(h){ fd.append(h.name, h.value); });
          if (timeStatedNeeded()) fd.append('time', byIdS('s-time').value);
          var att = submitForm.querySelector('[name=attachments]');
          if (editMay.author && att.files) Array.prototype.forEach.call(att.files, function(f){ fd.append('attachments', f); });
          var why = submitForm.querySelector('[name=reason]').value.trim();
          if (why) fd.append('reason', why);
          return fd;
        }
        var newMovie = expertish && movieInput.files && movieInput.files[0];
        var newGoal = expertish ? goalSelect.value + (subWrap.hidden ? '' : '/' + subSelect.value) : null;
        var oldGoal = run.category.goal + (run.category.sub ? '/' + run.category.sub : '');
        var moveGoal = newGoal && newGoal !== oldGoal ? newGoal : null;
        // an editor may only move the run: /api/edit is not theirs, so the
        // revision step is skipped and the move goes straight to expert/edit
        var mayRevise = editMay.author || editMay.expert;
        var dry = mayRevise ? post('/api/edit', revision(true), submitBtn)
                            : Promise.resolve({ok: false, j: {error: 'nothing to change'}});
        dry.then(function(res){
          var wv = (res.ok && res.j.ok) ? (res.j.would_void || []) : [];
          var nothing = !res.ok && /nothing to change/.test(res.j.error || '');
          if (!(res.ok && res.j.ok) && !nothing) { note(msg, res.j.error || 'something went wrong', false); return; }
          if (newMovie) wv = wv.concat(['reproductions', 'consoleVerifications']);
          var text = '';
          if (wv.indexOf('verifications') >= 0) text = 'This run is verified. Changing its scoring invalidates the verifications: it leaves the ranking until somebody verifies it again.';
          if (wv.indexOf('reproductions') >= 0) text += (text ? ' ' : '') + 'Changing its reproduction information invalidates the reproductions: they synced the old setup.';
          if (wv.indexOf('consoleVerifications') >= 0) text += (text ? ' ' : '') + 'Its console verifications are invalidated too.';
          if (text && !window.confirm(text + ' Save anyway?')) { setMark(submitBtn, '', ''); return; }
          submitting = true;
          var steps = [];
          if (!nothing) steps.push(function(){ return post('/api/edit', revision(false), submitBtn); });
          if (newMovie) steps.push(function(){
            var fd = new FormData(); fd.append('kind', 'run'); fd.append('target', editRunId); fd.append('field', 'movie');
            fd.append('movie', newMovie); fd.append('reason', submitForm.querySelector('[name=reason]').value.trim() || 'Replaced the movie file.');
            return post('/api/expert/edit', fd, submitBtn);
          });
          if (moveGoal) steps.push(function(){
            var fd = new FormData(); fd.append('kind', 'run'); fd.append('target', editRunId); fd.append('field', 'goal');
            fd.append('value', moveGoal); fd.append('reason', submitForm.querySelector('[name=reason]').value.trim() || 'Moved to the right category.');
            return post('/api/expert/edit', fd, submitBtn);
          });
          if (!steps.length) { submitting = false; note(msg, 'Nothing changed.', false); return; }
          var serial = null, voided = [];
          (function step(){
            if (!steps.length) {
              submitting = false;
              submitFormDirty = false;
              lastBtn = submitBtn;
              var editRunUrl = runPageUrl(editRunId);
              var editRunLines = [
                'Saved.' + (voided.length ? ' Invalidated: ' + voided.join(', ') + '.' : ''),
                'The run page shows it now: <a href="' + escapeHtml(editRunUrl) + '">' + escapeHtml(editRunUrl) + '</a>.'
              ];
              noteHtml(msg, true, editRunLines);
              return;
            }
            steps.shift()().then(function(r2){
              if (r2.ok && r2.j.ok) { serial = r2.j.serial || serial; (r2.j.voided || []).forEach(function(v){ if (voided.indexOf(v) < 0) voided.push(v); }); step(); }
              else { submitting = false; note(msg, r2.j.error || 'something went wrong', false); }
            });
          })();
        });
      }
      submitForm.addEventListener('submit', function(ev){
        ev.preventDefault();
        if (fileRowsOf.submitform && !fileRowsOf.submitform.valid()) return;
        // one archive per press: the button used to be picked by class, which
        // matched Preview, so a double click submitted the run twice
        if (submitting) return;
        if (editRunId) { saveEdit(); return; }
        submitting = true;
        var submitBtn = document.getElementById('s-submit');
        if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Archiving…'; }
        note(msg, 'Archiving your run…', true);
        post('/api/submit', new FormData(submitForm), submitBtn).then(function(res){
          submitting = false;
          if (submitBtn) submitBtn.textContent = 'Submit';
          if (res.ok && res.j.ok) {
            if (submitBtn) { submitBtn.disabled = true; submitBtn.dataset.sent = '1'; }   // done: never offer it again
            submitFormDirty = false;                  // archived: nothing left to lose
            dropDraft();
            submitForm.hidden = true;
            var forumUrl = res.j.forum ? String(res.j.forum) : '';
            var runUrl = runPageUrl(res.j.id);
            var lines = [
              'Archived as ' + escapeHtml(String(res.j.id)) + '.',
              forumUrl ? 'Announced on the forum: <a href="' + escapeHtml(forumUrl) + '">' + escapeHtml(forumUrl) + '</a>' : '',
              'Your run page is live at <a href="' + escapeHtml(runUrl) + '">' + escapeHtml(runUrl) + '</a>.'
            ];
            noteHtml(msg, true, lines);
          } else note(msg, res.j.error || 'something went wrong', false);
        });
      });
    });
  }

  // ---- create-game / create-category pages ----
  // The metrics editor both forms share: up to 4 rows, order = tie-break
  // hierarchy; time is a row like any other (a row labeled Time is the
  // run's main time).
  function initMetricsEd(root, initial){
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
        label: el('input', 'mlabel'), type: document.createElement('select'),
        better: document.createElement('select'), unit: el('input', 'munit')
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
  // the create-game / create-category form: gated on login, one press at a time
  function wireCreateForm(form, loginEl, msgEl, endpoint, done){
    mePromise.then(function(d){
      if (d.unreachable) { note(msgEl, 'The archivist is not reachable right now; try again later.', false); return; }
      if (!d.loggedIn) { loginEl.hidden = false; return; }
      form.hidden = false;
      initMetricsEd(form.querySelector('.metriced'));
      var inFlight = false;
      form.addEventListener('submit', function(ev){
        ev.preventDefault();
        if (inFlight) return;
        inFlight = true;
        var btn = form.querySelector('button.btn:not(.quiet):not(.mmove)');
        if (btn) btn.disabled = true;
        note(msgEl, 'Creating…', true);
        post(endpoint, new FormData(form), btn).then(function(res){
          inFlight = false;
          if (btn) btn.disabled = false;
          if (res.ok && res.j.ok) { form.hidden = true; done(res.j); }
          else note(msgEl, res.j.error || 'something went wrong', false);
        });
      });
    });
  }
  // ---- create-game page ----
  var createGameForm = document.getElementById('creategameform');
  if (createGameForm) {
    var createGameMsg = document.getElementById('cg-msg');
    wireCreateForm(createGameForm, document.getElementById('cg-login'), createGameMsg,
                   '/api/game/create', function(j){
      note(createGameMsg, 'Created. Publishing to the site…', true);
      waitBuilt(j.serial, function(live){
        note(createGameMsg, live ? 'Created. The game page is live; submit a run to it now.'
                         : 'Created. It will appear on the site shortly.', true);
        var a = document.createElement('a');
        a.className = 'btn'; a.href = '../submit/?game=' + j.game;
        a.textContent = 'Submit a run to ' + j.game;
        createGameMsg.appendChild(document.createElement('br'));
        createGameMsg.appendChild(a);
      });
    });
  }
  // ---- create-category page ----
  var createCatForm = document.getElementById('createcatform');
  if (createCatForm) {
    var catGameTitles = JSON.parse(document.getElementById('ccgamedata').textContent);
    var catGameKey = null;
    try { catGameKey = new URLSearchParams(location.search).get('game'); } catch (e) {}
    if (!catGameKey || !catGameTitles[catGameKey]) {
      document.getElementById('cc-nogame').hidden = false;
    } else {
      document.getElementById('cc-game').value = catGameKey;
      // Set the visible game name and — when the element is an <a> — attach the game page URL
      var ccGamenameEl = document.getElementById('cc-gamename');
      var ccCrumbGameEl = document.getElementById('cc-crumb-game');
      if (ccGamenameEl || ccCrumbGameEl) {
        var name = catGameTitles[catGameKey];
        if (ccGamenameEl) {
          ccGamenameEl.textContent = name;
          try {
            if (ccGamenameEl.tagName && ccGamenameEl.tagName.toLowerCase() === 'a') {
              ccGamenameEl.href = rel + 'games/' + catGameKey + '/';
            }
          } catch (e) {}
        }
        if (ccCrumbGameEl) {
          ccCrumbGameEl.textContent = name;
          try {
            if (ccCrumbGameEl.tagName && ccCrumbGameEl.tagName.toLowerCase() === 'a') {
              ccCrumbGameEl.href = rel + 'games/' + catGameKey + '/';
            }
          } catch (e) {}
        }
      }
      var createCatMsg = document.getElementById('cc-msg');
      // the game's categories, to offer "subcategory of"; a subcategory has
      // no metrics of its own and may leave the rule to its category
      var parentSelect = document.getElementById('cc-parent');
      fetch(api + '/api/categories?game=' + encodeURIComponent(catGameKey))
        .then(function(r){ return r.ok ? r.json() : null; })
        .then(function(cats){
          if (!cats) return;
          (cats.dimensions || []).forEach(function(dim){
            (dim.options || []).forEach(function(o){
              var opt = document.createElement('option');
              opt.value = o.key; opt.textContent = o.label;
              parentSelect.appendChild(opt);
            });
          });
        }).catch(function(){});
      function paintParent(){
        var isSub = !!parentSelect.value;
        document.getElementById('cc-parenthint').hidden = !isSub;
        var metricsBox = document.getElementById('cc-metrics');
        metricsBox.hidden = isSub;
        metricsBox.querySelectorAll('input, select, button').forEach(function(e){ e.disabled = isSub; });
        var ruleIn = createCatForm.querySelector('[name=rule]');
        ruleIn.required = !isSub;
        ruleIn.placeholder = isSub ? 'What this subcategory adds to the category rule (optional)'
                                   : 'What must a run do to belong here?';
        createCatForm.querySelector('[name=label]').placeholder = isSub ? 'e.g. any%' : 'e.g. 100k points';
      }
      parentSelect.addEventListener('change', paintParent);
      wireCreateForm(createCatForm, document.getElementById('cc-login'), createCatMsg,
                     '/api/category/add', function(j){
        note(createCatMsg, 'Created. Publishing to the site…', true);
        waitBuilt(j.serial, function(live){
          note(createCatMsg, live ? 'Created. The category is live; submit the run now.'
                           : 'Created. It will appear on the site shortly.', true);
          var a = document.createElement('a');
          a.className = 'btn'; a.href = '../submit/?game=' + catGameKey;
          a.textContent = 'Submit a run';
          createCatMsg.appendChild(document.createElement('br'));
          createCatMsg.appendChild(a);
          // re-show the form instead of reloading: reset fields and restore the parent/metrics UI
          setTimeout(function(){
            try { createCatForm.hidden = false; } catch (e) {}
            try { createCatForm.reset(); } catch (e) {}
            try { paintParent(); } catch (e) {}
            try { var lab = createCatForm.querySelector('[name=label]'); if (lab && lab.focus) lab.focus(); } catch (e) {}
          }, 1200);
          // Alternative: refresh the page so the form and the new categories render correctly
          //setTimeout(function(){ try { location.reload(); } catch (e) {} }, 2000);
        });
      });
    }
  }

  // ---- claim page ----
  // ---- forum discussion, in place ----
  // The archivist proxies the topic so the browser talks to one origin, and
  // posts a reply as the logged-in member (never as the bot).
  var discussion = document.getElementById('discussion');
  if (discussion && api) {
    var postsBox = document.getElementById('disc-posts');
    var replyForm = document.getElementById('disc-reply');
    var loginNote = document.getElementById('disc-login');
    var topicId = discussion.dataset.topic;
    var renderPosts = function(d){
      if (!d.posts || !d.posts.length) {
        postsBox.innerHTML = '<p class="emptynote">No posts yet. Be the first to say ' +
          'something about this run.</p>';
        return;
      }
      postsBox.innerHTML = d.posts.map(function(p){
        return '<article class="dpost"><div class="dhead">' +
          (p.avatar ? '<img class="davatar" src="' + escapeHtml(p.avatar) + '" alt="" loading="lazy">' : '') +
          '<b>' + escapeHtml(p.user || '') + '</b>' +
          '<span class="actmeta">' + escapeHtml((p.date || '').replace('T', ' ')) + '</span>' +
          '<a class="actmeta" href="' + escapeHtml(d.url) + '/' + (p.number || 1) + '">#' +
          (p.number || 1) + '</a></div>' +
          '<div class="dbody">' + (p.html || '') + '</div></article>';
      }).join('');
    };
    var loadDiscussion = function(){
      return fetch(api + '/api/discussion?topic=' + encodeURIComponent(topicId),
                   {credentials: 'include'})
        .then(function(r){ return r.json(); })
        .then(function(d){
          if (!d.ok) throw new Error(d.error || 'failed');
          renderPosts(d);
        })
        .catch(function(){
          postsBox.innerHTML = '<p class="emptynote">The discussion could not be loaded. ' +
            'Read it on <a href="' + escapeHtml(discussion.dataset.url) + '">the forum</a>.</p>';
        });
    };
    loadDiscussion();
    mePromise.then(function(me){
      if (!me.loggedIn) { loginNote.hidden = false; return; }
      document.getElementById('disc-who').textContent = me.user;
      replyForm.hidden = false;
      replyForm.addEventListener('submit', function(ev){
        ev.preventDefault();
        var msg = document.getElementById('disc-msg');
        var btn = actionBtn(replyForm);
        var fd = new FormData(replyForm);
        fd.append('topic', topicId);
        btn.disabled = true;
        note(msg, 'Posting…', true);
        post('/api/discussion/reply', fd, btn).then(function(res){
          btn.disabled = false;
          if (!res.ok) { note(msg, res.j.error || 'could not post', false); return; }
          note(msg, 'Posted. Thank you.', true);
          replyForm.querySelector('[name=body]').value = '';
          loadDiscussion();
        });
      });
    });
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

  // one Bluesky post, rendered in our own markup
  function bskyPostHtml(item, profileUrl, linkify, since){
    var postView = item.post || {}, record = postView.record || {};
    var rkey = String(postView.uri || '').split('/').pop();
    var url = profileUrl + '/post/' + rkey;
    var card = '';
    var embed = postView.embed || {};
    if (embed.external && embed.external.uri) {
      card = '<a class="bcard" href="' + escapeHtml(embed.external.uri) + '" rel="noopener">'
        + (embed.external.thumb ? '<img src="' + escapeHtml(embed.external.thumb) + '" alt="" loading="lazy">' : '')
        + '<span><b>' + escapeHtml(embed.external.title || embed.external.uri) + '</b>'
        + escapeHtml((embed.external.description || '').slice(0, 90)) + '</span></a>';
    } else if (embed.images && embed.images.length) {
      card = '<a class="bcard" href="' + escapeHtml(url) + '" rel="noopener">'
        + '<img src="' + escapeHtml(embed.images[0].thumb) + '" alt="'
        + escapeHtml(embed.images[0].alt || '') + '" loading="lazy">'
        + '<span><b>Image</b>view on Bluesky</span></a>';
    }
    return '<article class="bpost"><div class="btext">' + linkify(record.text || '')
      + '</div>' + card + '<div class="bmeta"><a href="' + escapeHtml(url) + '" rel="noopener">'
      + since(record.createdAt || postView.indexedAt) + '</a>'
      + '<span>♥ ' + (postView.likeCount || 0) + '</span>'
      + '<span>↻ ' + (postView.repostCount || 0) + '</span></div></article>';
  }

  // ---- Bluesky feed in the News & Events column ----
  // The AT Protocol serves public posts as JSON to anybody (CORS open, no
  // token, no cookies), so the panel renders in our own markup instead of
  // handing the reader to a third-party widget.
  // ---- member news: ten shown, the rest a quiet click away ----
  document.querySelectorAll('.newsmore').forEach(function(b){
    b.addEventListener('click', function(){
      var rest = b.parentElement.querySelector('.newsrest');
      if (rest) rest.hidden = false;
      b.remove();
    });
  });

  // ---- shelves: one row, dragged sideways (home page) ----
  // native touch panning is left alone; the mouse gets click-hold-drag,
  // and the faint arrows appear only on the side with more to see
  document.querySelectorAll('.hwrap > .hrow').forEach(function(row){
    var wrap = row.parentElement;
    function makeArrow(dir){
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'harr ' + (dir < 0 ? 'left' : 'right');
      b.textContent = dir < 0 ? '\u2039' : '\u203a';
      b.setAttribute('aria-label', dir < 0 ? 'scroll left' : 'scroll right');
      b.addEventListener('click', function(){
        row.scrollBy({left: dir * row.clientWidth * 0.8, behavior: 'smooth'});
      });
      wrap.appendChild(b);
      return b;
    }
    var left = makeArrow(-1), right = makeArrow(1);
    function paint(){
      var max = row.scrollWidth - row.clientWidth - 1;
      left.hidden = row.scrollLeft <= 0;
      right.hidden = row.scrollLeft >= max;
    }
    row.addEventListener('scroll', paint, {passive: true});
    window.addEventListener('resize', paint);
    paint();
    var down = false, moved = false, startX = 0, startScroll = 0;
    row.addEventListener('pointerdown', function(e){
      if (e.pointerType !== 'mouse' || e.button !== 0) return;
      down = true; moved = false; startX = e.clientX; startScroll = row.scrollLeft;
    });
    row.addEventListener('pointermove', function(e){
      if (!down) return;
      var dx = e.clientX - startX;
      if (!moved && Math.abs(dx) > 4) {
        moved = true;
        row.classList.add('dragging');
        // capture only once it IS a drag: capturing on pointerdown
        // retargets the click at the row and kills the card links
        try { row.setPointerCapture(e.pointerId); } catch (err) {}
      }
      if (moved) row.scrollLeft = startScroll - dx;
    });
    function lift(){
      if (!down) return;
      down = false;
      setTimeout(function(){ moved = false; row.classList.remove('dragging'); }, 0);
    }
    row.addEventListener('pointerup', lift);
    row.addEventListener('pointercancel', lift);
    row.addEventListener('click', function(e){
      if (moved) { e.preventDefault(); e.stopPropagation(); }
    }, true);
  });

  // the Bluesky feed box: fetch the public feed and draw it
  var feedBox = document.getElementById('bskyfeed');
  if (feedBox) {
    var handle = feedBox.dataset.handle || '';
    var profileUrl = 'https://bsky.app/profile/' + handle;
    var since = function(iso){
      var s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
      if (s < 3600) return Math.floor(s / 60) + 'm ago';
      if (s < 86400) return Math.floor(s / 3600) + 'h ago';
      if (s < 2592000) return Math.floor(s / 86400) + 'd ago';
      return new Date(iso).toISOString().slice(0, 10);
    };
    var linkify = function(text){
      return escapeHtml(text).replace(/(https?:\/\/[^\s<]+)/g,
        function(u){ return '<a href="' + u + '" rel="noopener">' + u + '</a>'; });
    };
    var showFeedError = function(){
      feedBox.innerHTML = '<p class="emptynote">Could not reach Bluesky just now. ' +
        'Read the latest at <a href="' + profileUrl + '">@' + escapeHtml(handle) + '</a>.</p>';
    };
    fetch('https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor='
          + encodeURIComponent(handle) + '&limit=10&filter=posts_no_replies')
      .then(function(r){ return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function(d){
        var items = (d && d.feed) || [];
        if (!items.length) {
          feedBox.innerHTML = '<p class="emptynote">No posts yet. Follow ' +
            '<a href="' + profileUrl + '">@' + escapeHtml(handle) + '</a> for announcements.</p>';
          return;
        }
        feedBox.innerHTML = items.map(function(item){
          return bskyPostHtml(item, profileUrl, linkify, since);
        }).join('');
      })
      .catch(showFeedError);
  }

  // ---- self-service TASVideos import ----
  // profile: reveal the owner-only "Import runs" button
  var selfImportBtn = document.getElementById('selfimport');
  if (selfImportBtn && api) {
    mePromise.then(function(me){
      if (me.loggedIn && me.user &&
          me.user.toLowerCase() === (selfImportBtn.dataset.author || '').toLowerCase()) {
        selfImportBtn.hidden = false;
      }
    });
  }
  // /import/ page: disclaimer -> auto-scan -> import with progress bar + log
  var impMsg = document.getElementById('imp-msg');
  if (impMsg && api) {
    var controls = document.getElementById('imp-ctl');
    var scanline = document.getElementById('imp-scanline');
    var list = document.getElementById('imp-list');
    var runBtn = document.getElementById('imp-run');
    var progress = document.getElementById('imp-prog');
    var progressFill = document.getElementById('imp-fill');
    var progressCount = document.getElementById('imp-count');
    var logBox = document.getElementById('imp-log');
    var titles = {};
    var NL = String.fromCharCode(10);
    function importPost(path, fd, btn){
      busy(btn, true);
      return fetch(api + path, {method: 'POST', credentials: 'include',
                                body: fd || undefined})
        .then(function(r){ return r.json(); })
        .catch(function(){ return {ok: false, error: 'network error; the archivist may be unreachable'}; })
        .then(function(r){ busy(btn, false); return r; });
    }
    function logLine(s){
      logBox.textContent += s + NL;
      logBox.scrollTop = logBox.scrollHeight;
    }
    var scanBtn = document.getElementById('imp-scan');
    var soloBtn = document.getElementById('imp-solo');
    var clearBtn = document.getElementById('imp-clear');
    mePromise.then(function(me){
      if (me.unreachable) {
        note(impMsg, 'The archivist is not reachable right now; try again later.', false);
        return;
      }
      if (!me.loggedIn) {
        note(impMsg, 'Log in (top right) to import your runs.', false);
        return;
      }
      // the member presses the source to check it: nothing runs unasked.
      // The row is where the next source lands beside this one.
      document.getElementById('imp-sources').hidden = false;
    });
    // one set of listeners, attached once; every scan resets the state they
    // work on. Attaching them per scan stacked handlers, so a rescan imported
    // twice per click.
    var boxes = [];
    function chosen(){
      return boxes.filter(function(b){ return b.box.checked; })
                  .map(function(b){ return b.box.value; });
    }
    function paintRun(){
      var n = chosen().length;
      runBtn.hidden = n === 0;
      runBtn.textContent = 'Import ' + n + ' selected run' + (n !== 1 ? 's' : '');
    }
    soloBtn.addEventListener('click', function(){
      boxes.forEach(function(b){ b.box.checked = !b.multi && !b.box.disabled; });
      paintRun();
    });
    clearBtn.addEventListener('click', function(){
      boxes.forEach(function(b){ b.box.checked = false; });
      paintRun();
    });
    scanBtn.addEventListener('click', function(){
      note(impMsg, 'Checking the TASVideos snapshot…', true);
      importPost('/api/import/scan', undefined, scanBtn).then(function(d){
        if (!d.ok) { note(impMsg, d.error || 'scan failed', false); return; }
        impMsg.hidden = true;
        controls.hidden = false;
        progress.hidden = true;
        list.innerHTML = '';
        boxes = [];
        try {
          d.pending.forEach(function(x){ titles['M' + x.id] = x.title; });
          if (!d.pending.length) {
            scanline.textContent = 'TASVideos: nothing to import, all ' + d.total +
              ' of your publications there are already archived (snapshot of ' + d.backupDate + ').';
          } else {
            scanline.textContent = 'TASVideos: ' + d.pending.length + ' of your ' + d.total +
              ' publications there are not archived yet (snapshot of ' + d.backupDate +
              '). Tick the ones to bring over; nothing is imported unpicked.';
          }
          d.pending.forEach(function(x){
            var row = el('label', 'improw');
            var box = document.createElement('input');
            box.type = 'checkbox';
            box.value = x.id;
            var blocked = x.movieMissing || x.tooBig;
            if (blocked) box.disabled = true;
            var text = 'M' + x.id + ' · ' + x.title;
            if (x.movieMissing) text += ' (movie not in the snapshot yet)';
            if (x.tooBig) text += ' (movie too large to import; ask on the forum)';
            row.appendChild(box);
            row.appendChild(document.createTextNode(' ' + text));
            if (x.multiAuthor && !blocked) {
              var others = x.authors.filter(function(a){
                return a.toLowerCase() !== (d.user || '').toLowerCase(); }).join(', ');
              row.appendChild(el('span', 'actmeta',
                ' co-authored with ' + others + '; importing it is your responsibility'));
            }
            boxes.push({box: box, multi: !!x.multiAuthor});
            box.addEventListener('change', paintRun);
            list.appendChild(row);
          });
          (d.already || []).forEach(function(x){
            var row = el('span', 'improw done');
            row.appendChild(el('span', 'actmeta', '✓ already archived: '));
            var a = el('a', '', 'M' + x.id + ' · ' + x.title);
            a.href = '../runs/M' + x.id + '/';
            row.appendChild(a);
            list.appendChild(row);
          });
        } catch (e) {
          // a render that dies silently reads as "the list never appears";
          // say what actually broke, so the next report carries the cause
          note(impMsg, 'Could not draw the list: ' + e + ' — please report this '
                       + 'exact message.', false);
          return;
        }
        list.hidden = false;
        soloBtn.hidden = clearBtn.hidden = !d.pending.length;
        paintRun();
      });
    });
    runBtn.addEventListener('click', function(){
      var picked = chosen();
      if (!picked.length) return;
      busy(runBtn, true);
      boxes.forEach(function(b){ b.box.disabled = true; });
      progress.hidden = false;
      var total = picked.length;
      var done = 0, skips = 0;
      progressCount.textContent = '0 / ' + total;
      var fd = new FormData();
      fd.append('select', picked.join(' '));
      function step(){
        importPost('/api/import/run', fd).then(function(r){
          if (!r.ok) {
            logLine('✗ ' + (r.error || 'import failed'));
            note(impMsg, 'Import stopped after ' + done + ' of ' + total +
              '; your progress is saved. Reload to resume.', false);
            impMsg.hidden = false;
            busy(runBtn, false);
            return;
          }
          r.imported.forEach(function(id){
            done += 1;
            logLine('✓ ' + id + ' · ' + (titles[id] || ''));
          });
          (r.skipped || []).forEach(function(s){
            skips += 1;
            logLine('⚠ ' + s);
          });
          progressCount.textContent = Math.min(done + skips, total) + ' / ' + total;
          progressFill.style.width = Math.round(100 * Math.min(done + skips, total) / total) + '%';
          if (r.remaining > 0 && r.imported.length) { step(); return; }
          logLine('');
          logLine('Done: ' + done + ' imported' + (skips ? ', ' + skips + ' need attention (see above)' : '') + '.');
          logLine('Publishing to the site…');
          waitBuilt(r.serial, function(live){
            logLine(live ? 'Your runs are live on the site now.'
                         : 'Your runs will appear on the site shortly.');
          });
          runBtn.hidden = true;
          busy(runBtn, false);
        });
      }
      step();
    });
  }

})();
