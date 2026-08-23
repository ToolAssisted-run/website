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
  // an outcome: on the mark beside the button that asked, when there is
  // one; in the box otherwise
  function note(box, text, good){
    if (lastBtn && setMark(lastBtn, good ? 'ok' : 'bad', text)) {
      if (box) box.hidden = true;
      return;
    }
    noteText(box, text, good);
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
  function armMultiPick(form, name, listId, allowed){
    var input = form && form.querySelector('[name=' + name + ']');
    if (!input || !input.parentNode) return null;
    input.type = 'hidden';
    var pickInput = el('input', 'pickbox');
    pickInput.setAttribute('list', listId);
    pickInput.placeholder = 'type to find; picking adds it';
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
  // the member page: the Committee's delete, gated tighter when the target
  // sits on the Committee (the Founder's alone) and never for the Founder
  (function(){
    var memberDataEl = document.getElementById('memberactdata');
    if (!memberDataEl) return;
    var memberData = JSON.parse(memberDataEl.textContent);
    mePromise.then(function(d){
      if (d.unreachable || !d.loggedIn) return;
      var who = d.user.toLowerCase();
      if (memberData.committee.indexOf(who) < 0) return;
      if (memberData.targetSeated && memberData.founders.indexOf(who) < 0) return;
      var zone = document.getElementById('memberacts');
      var msg = document.getElementById('memberact-msg');
      zone.hidden = false;
      var form = document.getElementById('f-memberdelete');
      form.addEventListener('submit', function(ev){
        ev.preventDefault();
        if (!window.confirm('Delete the member ' + memberData.target + ' outright? ' +
                            'This cannot be undone.')) return;
        post('/api/member/delete', new FormData(form), actionBtn(form))
          .then(function(res){
            if (res.ok && res.j.ok) {
              noteBuilt(msg, 'Deleted.', res.j.serial,
                        'The page is gone from the site now.');
            } else note(msg, res.j.error || 'something went wrong', false);
          });
      });
    });
  })();
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
      var candidateList = document.getElementById('dl-role-candidates');
      function refreshRoleCandidates(){
        var holders = (roleSelect.value === 'committee' ? committeeData.committeeNames
                       : roleSelect.value === 'editor' ? (committeeData.editors || [])
                       : committeeData.moderators)
          .map(function(x){ return x.toLowerCase(); });
        var pool = actionSelect.value === 'granted'
          ? committeeData.members.filter(function(m){ return holders.indexOf(m.toLowerCase()) < 0; })
          : committeeData.members.filter(function(m){ return holders.indexOf(m.toLowerCase()) >= 0; });
        candidateList.innerHTML = '';
        pool.forEach(function(m){
          var o = document.createElement('option');
          o.value = m;
          candidateList.appendChild(o);
        });
      }
      roleSelect.addEventListener('change', refreshRoleCandidates);
      actionSelect.addEventListener('change', refreshRoleCandidates);
      refreshRoleCandidates();
      roleForm.addEventListener('submit', function(ev){
        ev.preventDefault();
        post('/api/role/decide', new FormData(roleForm), actionBtn(roleForm))
          .then(function(res){
            if (res.ok && res.j.ok) {
              noteBuilt(roleMsg, 'Recorded: ' + res.j.votes + ' of ' + res.j.committee +
                        ' voted for it.', res.j.serial);
              roleForm.reset();
            } else note(roleMsg, res.j.error || 'something went wrong', false);
          });
      });
      // the whole-site appointment: everyone who does not already hold it
      var siteExpertSelect = document.getElementById('siteexpert-user');
      committeeData.members.forEach(function(m){
        if (committeeData.siteExperts.indexOf(m.toLowerCase()) >= 0) return;
        var o = document.createElement('option');
        o.value = m;
        o.textContent = m;
        siteExpertSelect.appendChild(o);
      });
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
      var editorSelect = document.getElementById('editorrole-user');
      committeeData.members.forEach(function(m){
        if ((committeeData.editors || []).map(function(x){ return x.toLowerCase(); })
            .indexOf(m.toLowerCase()) >= 0) return;
        var o = document.createElement('option');
        o.value = m;
        o.textContent = m;
        editorSelect.appendChild(o);
      });
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
      var myGames = panelData.games.filter(function(g){ return coversGame(myScopes, g.key); });
      var myGroups = panelData.groups.filter(function(gr){ return coversGroup(myScopes, gr.key); });
      var apptGames = amCommittee ? panelData.games : myGames;
      var apptGroups = amCommittee ? panelData.groups : myGroups;
      fillSelect(document.getElementById('appoint-game'),
           apptGames.map(function(g){ return {value: g.key, label: g.title + ' (' + g.key + ')'}; }),
           'no game is yours to hand out');
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

      // members who do not already speak for the chosen scope
      function refreshCandidates(scopeSel, userSel){
        if (!scopeSel || !userSel) return;
        var scope = scopeSel.value;
        var free = panelData.members.filter(function(m){
          var s = scopesOf(m);
          if (!s.length) return true;
          if (s.indexOf('site') >= 0) return false;
          if (scope.indexOf('group:') === 0) return !coversGroup(s, scope.slice(6));
          if (scope.indexOf('/') > 0) return !coversGame(s, scope);
          return s.indexOf(scope) < 0;
        });
        fillSelect(userSel, free.map(function(m){ return {value: m, label: m}; }),
             'everybody here already speaks for it');
      }
      [['appoint-game', 'appoint-game-user'], ['appoint-group', 'appoint-group-user'],
       ['appoint-wide', 'appoint-wide-user']].forEach(function(pair){
        var scopeSelect = document.getElementById(pair[0]), userSelect = document.getElementById(pair[1]);
        if (!scopeSelect) return;
        scopeSelect.addEventListener('change', function(){ refreshCandidates(scopeSelect, userSelect); });
        refreshCandidates(scopeSelect, userSelect);
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
      // game already in one would be refused, since a game belongs to one
      var groupable = myGames.filter(function(g){ return !g.group; });
      var gameList = document.getElementById('panel-gamelist');
      groupable.forEach(function(g){
        var o = document.createElement('option');
        o.value = g.key;
        o.label = g.title;
        gameList.appendChild(o);
      });
      var groupableKeys = groupable.map(function(g){ return g.key; });
      armMultiPick(document.getElementById('f-groupnew'), 'games',
                   'panel-gamelist', function(){ return groupableKeys; });
      var groupEditSelect = document.getElementById('groupedit-key');
      armMultiPick(document.getElementById('f-groupedit'), 'add',
                   'panel-gamelist', function(){ return groupableKeys; });
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
          post(path, fd, actionBtn(form)).then(function(res){
            if (res.ok && res.j.ok) {
              noteBuilt(msgBox, 'Recorded, thank you.', res.j.serial);
            } else note(msgBox, res.j.error || 'something went wrong', false);
          });
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
      if (isAuthor || isExpert) {
        arm('f-edit', '/api/edit', function(form){
          if (isAuthor) {
            initAuthorPick(form.querySelector('.authpick'), runData.authorsDisplay);
          } else {
            // the author list and their supplementary files are never an
            // expert's to touch; an expert edit states its public reason
            var editDetails = form.closest('details');
            var editSummary = editDetails && editDetails.querySelector('summary');
            if (editSummary) editSummary.textContent = 'Edit run (expert mode)';
            var authorsField = document.getElementById('fe-authors');
            authorsField.hidden = true;
            authorsField.querySelector('[name=authors]').disabled = true;
            var attachField = document.getElementById('fe-attach');
            attachField.hidden = true;
            attachField.querySelector('[name=attachments]').disabled = true;
            var reasonField = document.getElementById('fe-why');
            reasonField.hidden = false;
            reasonField.querySelector('[name=reason]').required = true;
          }
          form.querySelector('[name=emulator]').value = runData.emulator;
          form.querySelector('[name=completed]').value = runData.completed;
          var notesArea = form.querySelector('[name=notes]');
          fetch(runData.notesUrl).then(function(r){ return r.ok ? r.text() : ''; })
            .then(function(txt){ notesArea.value = txt; }).catch(function(){});
        });
      }
      if (!isAuthor && !runData.imported) {
        if (!runData.videoOnly) {
          if (runData.reproduced.indexOf(myName) < 0) arm('f-repro', '/api/reproduce');
        }
        if (runData.hasEncode && runData.verified.indexOf(myName) < 0) arm('f-verify', '/api/verify');
        if (!runData.videoOnly && (runData.consoled || []).indexOf(myName) < 0) arm('f-console', '/api/console-verify');
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

  // ---- the game editor (covering experts) ----
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
      document.getElementById('geditor').hidden = false;
      var msg = document.getElementById('ge-msg');
      // messages land beside the form they answer (#50): a form's own
      // .prop-msg or .actmsg when it has one, the page's otherwise
      function msgFor(form){
        return (form && form.querySelector('.prop-msg, .actmsg')) || msg;
      }
      function noteSaved(text, serial, where){
        noteBuilt(where || msg, text, serial);
      }
      function wire(id, path, confirmText, done){
        var form = document.getElementById(id);
        if (!form) return;
        form.addEventListener('submit', function(ev){
          ev.preventDefault();
          if (confirmText && !window.confirm(confirmText)) return;
          post(path, new FormData(form), actionBtn(form))
            .then(function(res){
              if (res.ok && res.j.ok) noteSaved(done(res.j, form), res.j.serial, msgFor(form));
              else note(msgFor(form), res.j.error || 'something went wrong', false);
            });
        });
      }
      // progressive rows (#50): Edit unfolds the row's form, Cancel folds it
      // back, a save rewrites the static value and folds it after a moment
      document.querySelectorAll('.prop').forEach(function(row){
        var form = row.querySelector('.prop-form');
        var editBtn = row.querySelector('.prop-edit');
        function fold(){ form.hidden = true; row.classList.remove('editing'); }
        editBtn.addEventListener('click', function(){
          form.hidden = false; row.classList.add('editing');
          var first = form.querySelector('input:not([type=hidden]), textarea');
          if (first) first.focus();
        });
        row.querySelector('.prop-cancel').addEventListener('click', function(){
          form.reset(); form.querySelector('.prop-msg').hidden = true; fold();
        });
        row.fold = fold;
      });
      function showValue(form, text){
        var row = form.closest('.prop');
        if (!row) return;
        var v = row.querySelector('.prop-value');
        v.textContent = '';
        if (text) v.textContent = text;
        else v.appendChild(el('span', 'prop-empty', 'not set'));
        setTimeout(function(){ if (row.fold) row.fold(); }, 1800);
      }
      wire('f-ge-title', '/api/expert/edit', null,
           function(j, form){ showValue(form, j.to); return 'Renamed to ' + j.to + '.'; });
      wire('f-ge-thumb', '/api/expert/edit', null,
           function(j, form){ showValue(form, 'set'); return 'Thumbnail set.'; });
      // the game properties (#44): one logged edit per field
      ['released', 'unofficial', 'discord', 'website', 'rta'].forEach(function(field){
        wire('f-ge-' + field, '/api/expert/edit', null,
             function(j, form){
               showValue(form, field === 'unofficial' ? (j.to === 'True' ? 'yes' : 'no') : j.to);
               return j.to === '' ? 'Cleared ' + field + '.' : 'Saved ' + field + ': ' + j.to + '.';
             });
      });
      wire('f-ge-add', '/api/category/add', null,
           function(j){ return 'Added ' + j.key + '.'; });
      var addFormMetrics = document.querySelector('#f-ge-add .metriced');
      if (addFormMetrics) initMetricsEd(addFormMetrics, null);

      // one card per category option: edit in place, delete-if-empty
      var box = document.getElementById('ge-cats');
      // order: the selectors list categories left to right as the file
      // does, so the popular ones go first. Arrows move a card one step
      // and send the whole order; the same for a category's subcategories.
      var orderMsg = el('p', 'actmsg'); orderMsg.hidden = true;
      function reorder(keys, optionKey, btn, done){
        var fd = new FormData();
        fd.append('game', gameEditData.game);
        fd.append('order', keys.join(','));
        if (optionKey) fd.append('option', optionKey);
        return post('/api/category/reorder', fd, btn).then(function(res){
          if (res.ok && res.j.ok) { done(); noteSaved('Order saved.', res.j.serial, orderMsg); }
          else note(orderMsg, res.j.error || 'something went wrong', false);
        });
      }
      function arrows(list, item, keyOf, optionKey, container, render){
        // ◀ ▶ for one item of a list; on success the array and the DOM follow
        var wrap = el('span', 'orderbtns');
        [['◀', -1, 'Move left (earlier)'], ['▶', 1, 'Move right (later)']].forEach(function(spec){
          var b = el('button', 'authx orderbtn', spec[0]); b.type = 'button'; b.title = spec[2];
          b.addEventListener('click', function(){
            var i = list.indexOf(item), j = i + spec[1];
            if (j < 0 || j >= list.length) return;
            var next = list.slice(); next.splice(i, 1); next.splice(j, 0, item);
            reorder(next.map(keyOf), optionKey, b, function(){
              list.length = 0; next.forEach(function(x){ list.push(x); });
              render();
            });
          });
          wrap.appendChild(b);
        });
        return wrap;
      }
      box.appendChild(orderMsg);
      var options = gameEditData.options || [];
      function renderCards(){
        box.querySelectorAll('.gecard').forEach(function(c){ c.remove(); });
        options.forEach(buildCard);
      }
      function buildCard(o){
        var card = el('div', 'gecard');
        var head = el('div', 'gehead');
        head.appendChild(el('b', '', o.key));
        head.appendChild(el('span', 'actmeta',
          ' ' + o.runs + ' run' + (o.runs === 1 ? '' : 's')));
        head.appendChild(arrows(options, o, function(x){ return x.key; }, null, box, renderCards));
        card.appendChild(head);
        function field(labelText, tag){
          var lab = el('label', '', labelText + ' ');
          var inp = el(tag === 'textarea' ? 'textarea' : 'input');
          lab.appendChild(inp);
          card.appendChild(lab);
          return inp;
        }
        var labelInput = field('Label');
        labelInput.value = o.label;
        var ruleInput = field('Rule', 'textarea');
        ruleInput.value = o.rule;
        ruleInput.rows = 2;
        // the category's metrics, editable like label and rule; adding one
        // writes an explicit 0 onto every run here, for experts to fill
        var metricsRoot = el('div');
        metricsRoot.innerHTML = document.getElementById('med-skeleton').innerHTML;
        var metricsBox = metricsRoot.firstElementChild;
        card.appendChild(metricsBox);
        var metricsEd = initMetricsEd(metricsBox, o.metrics || null);
        var metricsBefore = metricsEd.value();
        // subcategories (#43): a second level inside this category, each
        // with a label and a rule fragment; added here, renamed here,
        // removed here while empty. Every change is its own logged edit.
        var subBox = el('div', 'subcats');
        subBox.appendChild(el('h4', '', 'Subcategories'));
        subBox.appendChild(el('p', 'rules', 'Optional. A second level inside this category ' +
          '(Episode 1: any%, 100%). Once one exists, every run here names one; the first ' +
          'subcategory takes the runs already in the category.'));
        var subList = el('div', 'sublist');
        function renderSubs(){
          subList.innerHTML = '';
          (o.subcategories || []).forEach(subRow);
        }
        function subRow(sc){
          var row = el('div', 'subrowed');
          row.appendChild(arrows(o.subcategories, sc, function(x){ return x.key; }, o.key, subList, renderSubs));
          var labelIn = el('input'); labelIn.value = sc.label; labelIn.maxLength = 80; labelIn.placeholder = 'label';
          var ruleIn = el('input'); ruleIn.value = sc.rule || ''; ruleIn.maxLength = 500; ruleIn.placeholder = 'rule fragment (optional)';
          var whyIn = el('input'); whyIn.placeholder = 'why (public)'; whyIn.minLength = 8; whyIn.maxLength = 500;
          row.appendChild(el('code', 'subkey', sc.key));
          row.appendChild(labelIn); row.appendChild(ruleIn); row.appendChild(whyIn);
          row.appendChild(el('span', 'actmeta', sc.runs + ' run' + (sc.runs === 1 ? '' : 's')));
          var rowMsg = el('p', 'actmsg'); rowMsg.hidden = true;
          var saveSub = el('button', 'btn', 'Save'); saveSub.type = 'button';
          saveSub.addEventListener('click', function(){
            var jobs = [];
            if (labelIn.value.trim() !== sc.label) jobs.push(['label', labelIn.value.trim()]);
            if (ruleIn.value.trim() !== (sc.rule || '')) jobs.push(['rule', ruleIn.value.trim()]);
            if (!jobs.length) { note(rowMsg, 'Nothing changed on ' + sc.key + '.', false); return; }
            var serial;
            (function step(){
              if (!jobs.length) { sc.label = labelIn.value.trim(); sc.rule = ruleIn.value.trim(); noteSaved('Saved ' + sc.key + '.', serial, rowMsg); return; }
              var job = jobs.shift();
              var fd = new FormData();
              fd.append('kind', 'category');
              fd.append('target', gameEditData.game + ':' + o.key + '/' + sc.key);
              fd.append('field', job[0]); fd.append('value', job[1]);
              fd.append('reason', whyIn.value.trim());
              post('/api/expert/edit', fd, saveSub).then(function(res){
                if (res.ok && res.j.ok) { serial = res.j.serial; step(); }
                else note(rowMsg, res.j.error || 'something went wrong', false);
              });
            })();
          });
          row.appendChild(saveSub);
          // any empty subcategory may go; so may the last one, runs and all
          // (they stay in the category, naming none)
          if (!sc.runs || o.subcategories.length === 1) {
            var delSub = el('button', 'btn danger', 'Delete'); delSub.type = 'button';
            delSub.addEventListener('click', function(){
              var ask = sc.runs ? 'Delete the last subcategory ' + sc.key + '? Its ' + sc.runs + ' run(s) stay in ' + o.key + ' without a subcategory.'
                                : 'Delete the unused subcategory ' + sc.key + '?';
              if (!window.confirm(ask)) return;
              var fd = new FormData();
              fd.append('game', gameEditData.game); fd.append('option', o.key); fd.append('sub', sc.key);
              fd.append('reason', whyIn.value.trim() || 'Removed unused by a covering expert.');
              post('/api/category/delete', fd, delSub).then(function(res){
                if (res.ok && res.j.ok) { o.subcategories.splice(o.subcategories.indexOf(sc), 1); renderSubs(); noteSaved('Removed ' + sc.key + '.', res.j.serial); }
                else note(rowMsg, res.j.error || 'something went wrong', false);
              });
            });
            row.appendChild(delSub);
          }
          row.appendChild(rowMsg);
          subList.appendChild(row);
        }
        o.subcategories = o.subcategories || [];
        renderSubs();
        subBox.appendChild(subList);
        var addRow = el('div', 'subrowed subadd');
        var addLabel = el('input'); addLabel.placeholder = 'new subcategory label, e.g. any%'; addLabel.maxLength = 80;
        var addRule = el('input'); addRule.placeholder = 'rule fragment (optional)'; addRule.maxLength = 500;
        var addBtn = el('button', 'btn leave', '+ Add a subcategory'); addBtn.type = 'button';
        var addMsg = el('p', 'actmsg'); addMsg.hidden = true;
        addBtn.addEventListener('click', function(){
          if (!addLabel.value.trim()) { addLabel.focus(); return; }
          var fd = new FormData();
          fd.append('game', gameEditData.game); fd.append('parent', o.key);
          fd.append('label', addLabel.value.trim()); fd.append('rule', addRule.value.trim());
          post('/api/category/add', fd, addBtn).then(function(res){
            if (res.ok && res.j.ok) {
              o.subcategories.push({key: res.j.key, label: addLabel.value.trim(), rule: addRule.value.trim(), runs: res.j.runs_moved || 0});
              renderSubs();
              addLabel.value = ''; addRule.value = '';
              noteSaved('Added ' + res.j.key + (res.j.runs_moved ? ', taking the ' + res.j.runs_moved + ' run(s) already here' : '') + '.', res.j.serial, addMsg);
            } else note(addMsg, res.j.error || 'something went wrong', false);
          });
        });
        addRow.appendChild(addLabel); addRow.appendChild(addRule); addRow.appendChild(addBtn); addRow.appendChild(addMsg);
        subBox.appendChild(addRow);
        card.appendChild(subBox);
        var reasonInput = field('Why (published with the change)');
        reasonInput.placeholder = 'required to save a change';
        var cardMsg = el('p', 'actmsg');
        cardMsg.hidden = true;
        var row = el('div', 'gebtns');
        var saveBtn = el('button', 'btn', 'Save');
        saveBtn.type = 'button';
        saveBtn.addEventListener('click', function(){
          var jobs = [];
          if (labelInput.value.trim() !== o.label) jobs.push(['label', labelInput.value.trim()]);
          if (ruleInput.value.trim() !== o.rule) jobs.push(['rule', ruleInput.value.trim()]);
          if (metricsEd.value() !== metricsBefore) jobs.push(['metrics', metricsEd.value()]);
          if (!jobs.length) { note(cardMsg, 'Nothing changed on ' + o.key + '.', false); return; }
          var savedSerial;
          function step(){
            if (!jobs.length) {
              o.label = labelInput.value.trim();
              o.rule = ruleInput.value.trim();
              metricsBefore = metricsEd.value();
              noteSaved('Saved ' + o.key + '.', savedSerial, cardMsg);
              return;
            }
            var job = jobs.shift();
            var fd = new FormData();
            fd.append('kind', 'category');
            fd.append('target', gameEditData.game + ':' + o.key);
            fd.append('field', job[0]);
            fd.append('value', job[1]);
            fd.append('reason', reasonInput.value.trim());
            post('/api/expert/edit', fd, saveBtn).then(function(res){
              if (res.ok && res.j.ok) { savedSerial = res.j.serial; step(); }
              else note(cardMsg, res.j.error || 'something went wrong', false);
            });
          }
          step();
        });
        row.appendChild(saveBtn);
        if (!o.runs) {
          var deleteBtn = el('button', 'btn danger', 'Delete');
          deleteBtn.type = 'button';
          deleteBtn.addEventListener('click', function(){
            if (!window.confirm('Delete the unused category ' + o.key + '?')) return;
            var fd = new FormData();
            fd.append('game', gameEditData.game);
            fd.append('option', o.key);
            post('/api/category/delete', fd, deleteBtn).then(function(res){
              if (res.ok && res.j.ok) { card.remove(); noteSaved('Removed ' + o.key + '.', res.j.serial); }
              else note(cardMsg, res.j.error || 'something went wrong', false);
            });
          });
          row.appendChild(deleteBtn);
        }
        card.appendChild(row);
        card.appendChild(cardMsg);
        box.appendChild(card);
      }
      renderCards();
      if (!(gameEditData.options || []).length) {
        box.appendChild(el('p', 'emptynote',
          'No categories yet: add the first one below.'));
      }
    });
  }

  // ---- file a claim: anybody logged in, one at a time ----
  var claimForm = document.getElementById('f-claim');
  if (claimForm) {
    mePromise.then(function(d){
      if (d.unreachable) return;
      if (!d.loggedIn) {
        document.getElementById('claim-login').hidden = false;
        return;
      }
      document.getElementById('claim-form-wrap').hidden = false;
      var msg = document.getElementById('claim-msg');
      claimForm.addEventListener('submit', function(ev){
        ev.preventDefault();
        post('/api/claim/request', new FormData(claimForm),
             actionBtn(claimForm)).then(function(res){
          if (res.ok && res.j.ok) {
            note(msg, 'Filed. The Steering Committee answers it, and you will hear ' +
                      'either way, by private message on the forum.', true);
            claimForm.hidden = true;
          } else note(msg, res.j.error || 'something went wrong', false);
        });
      });
    });
  }

  // ---- attest an identity (site experts) ----
  var siteExpertsEl = document.getElementById('siteexperts');
  if (siteExpertsEl) {
    var siteExpertList = JSON.parse(siteExpertsEl.textContent);
    mePromise.then(function(d){
      if (!d.loggedIn || siteExpertList.indexOf(d.user.toLowerCase()) < 0) return;
      var wrap = document.getElementById('attest-wrap');
      var form = document.getElementById('f-attest');
      var msg = document.getElementById('attest-msg');
      wrap.hidden = false;
      form.addEventListener('submit', function(ev){
        ev.preventDefault();
        post('/api/claim/attest', new FormData(form), actionBtn(form))
          .then(function(res){
            if (res.ok && res.j.ok) {
              note(msg, 'Attested: ' + res.j.identity + ' is now ' + res.j.member +
                        '. ' + (res.j.rename || ''), true);
              form.hidden = true;
            } else note(msg, res.j.error || 'something went wrong', false);
          });
      });
    });
  }

  // ---- sortable tables (click a column header to sort) ----
  document.querySelectorAll('table.sortable').forEach(function(table){
    var dirs = {};
    function cellValue(tr, i){
      var td = tr.children[i];
      return td ? td.textContent.trim() : '';
    }
    function parseVal(s){
      // an ISO date (with or without a clock) orders by time, not by the
      // leading year that parseFloat would stop at
      if (/^[0-9]{4}-[0-9]{2}-[0-9]{2}/.test(s)) return Date.parse(s.replace(' ', 'T') + (s.length > 10 ? 'Z' : ''));
      var m = /^([0-9]+):([0-9][0-9])(?::([0-9][0-9]))?(?:[.]([0-9]+))?$/.exec(s);
      if (m) {
        var sec = m[3] !== undefined
          ? (+m[1]) * 3600 + (+m[2]) * 60 + (+m[3])
          : (+m[1]) * 60 + (+m[2]);
        return sec + (m[4] ? parseFloat('0.' + m[4]) : 0);
      }
      var n = parseFloat(s.replace(/[,f★\s]/g, ''));
      return isNaN(n) ? null : n;
    }
    table.querySelectorAll('thead th').forEach(function(th, i){
      th.classList.add('sorth');
      th.addEventListener('click', function(){
        var dir = dirs[i] = -(dirs[i] || -1);
        var tbody = table.querySelector('tbody');
        var rows = Array.prototype.slice.call(tbody.children);
        rows.sort(function(a, b){
          var va = cellValue(a, i), vb = cellValue(b, i);
          var na = parseVal(va), nb = parseVal(vb);
          var cmp = (na !== null && nb !== null) ? na - nb : va.localeCompare(vb);
          return cmp * dir;
        });
        rows.forEach(function(r){ tbody.appendChild(r); });
        table.querySelectorAll('thead th').forEach(function(x){
          x.classList.remove('sort-asc', 'sort-desc');
        });
        th.classList.add(dir === 1 ? 'sort-asc' : 'sort-desc');
      });
    });
  });

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
      submitForm.addEventListener('input', function(){ submitFormDirty = true; });
      submitForm.addEventListener('change', function(){ submitFormDirty = true; });
      window.addEventListener('beforeunload', function(ev){
        if (!submitFormDirty) return;
        ev.preventDefault();
        ev.returnValue = '';
      });
      // video-only: the movie input and the stated time trade places, and
      // the one that is hidden must not hold a stale required flag
      var videoOnlyBox = document.getElementById('s-videoonly');
      var movieWrap = document.getElementById('s-moviewrap');
      var timeWrap = document.getElementById('s-timewrap');
      // the stated time is four number boxes (h m s ms): a format mistake is
      // impossible, and the canonical [h:]mm:ss.mmm value is composed here
      var timeSegments = ['t-h', 't-m', 't-s', 't-ms'].map(function(id){
        return document.getElementById(id);
      });
      var timeField = document.getElementById('s-time');
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
      // the dashed box; the derived real time is never typed for movie runs
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
            + ' — ' + (m.better === 'higher' ? 'higher' : 'lower') + ' is better';
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
            metricFields.appendChild(wrap);
          } else {
            var numberInput = document.createElement('input');
            numberInput.type = 'number'; numberInput.min = 0; numberInput.step = 'any';
            numberInput.required = true; numberInput.inputMode = 'decimal';
            numberInput.addEventListener('input', function(){ hiddenField.value = String(secsOf(numberInput.value)); });
            metricFields.appendChild(numberInput);
          }
          metricFields.appendChild(hiddenField);
        });
      }
      function paintKind(){
        var v = videoOnlyBox.checked;
        movieWrap.hidden = v;
        var needsTime = wantsTime();
        timeWrap.hidden = !(v && needsTime);
        metricsBox.hidden = statedDefs().length === 0 && timeWrap.hidden;
        movieWrap.querySelector('input').required = !v;
        var secs = document.getElementById('t-s');
        if (secs) secs.required = v && needsTime;
        composeTime();
      }
      videoOnlyBox.addEventListener('change', paintKind);
      paintKind();
      var goalCache = {};
      var pendingDraft = null;   // a restored draft waiting for its game's categories
      function fillGoals(goals){
        goalSelect.innerHTML = '';
        (goals || []).forEach(function(g){
          var o = document.createElement('option');
          o.value = g.key; o.textContent = g.label;
          goalSelect.appendChild(o);
        });
        var u = document.createElement('option');
        u.value = 'unclassified'; u.textContent = 'Unclassified (no goal; ranked by likes)';
        goalSelect.appendChild(u);
        if (pendingDraft && (goals || []).length && pendingDraft.game === gameSelect.value) {
          var d = pendingDraft; pendingDraft = null;
          if (d.goal) goalSelect.value = d.goal;
          paintCategory();
          if (d.sub) subSelect.value = d.sub;
          applyDraftFields(d.fields);
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
        if (goalCache[gameSelect.value]) { fillGoals(goalCache[gameSelect.value]); return; }
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
            fillGoals(goals);
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
        var data = {t: new Date().toISOString(), game: gameSelect.value, goal: goalSelect.value,
                    sub: subSelect.disabled ? '' : subSelect.value, fields: draftFields()};
        try { localStorage.setItem(DRAFT_KEY, JSON.stringify(data)); } catch (e) { return; }
        draftNote.hidden = false;
        draftNote.textContent = 'Draft saved ' + stamp(data.t);
      }
      function dropDraft(){
        try { localStorage.removeItem(DRAFT_KEY); } catch (e) {}
        draftNote.hidden = true;
      }
      submitForm.addEventListener('input', function(){ clearTimeout(draftTimer); draftTimer = setTimeout(saveDraft, 300); });
      submitForm.addEventListener('change', function(){ clearTimeout(draftTimer); draftTimer = setTimeout(saveDraft, 300); });
      document.getElementById('s-clear').addEventListener('click', function(){
        if (!window.confirm('Clear every field and discard the saved draft?')) return;
        dropDraft();
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
      var restored = restoreDraft();
      if (presetGame && gameTitles[presetGame]) {
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
      if (!restored && !presetGame) fillGoals([]);

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
        if (!knownHost(url)) {
          encodeThumb.removeAttribute('src');
          encodeCheck.hidden = url === '';
          encodeStatus.textContent = '✗ not a link from ENCODE_NAMES';
          encodeStatus.className = 'enc-bad';
          return;
        }
        encodeCheck.hidden = false;
        encodeStatus.textContent = 'checking…';
        encodeStatus.className = 'enc-wait';
        // the archivist resolves it: several platforms reveal their thumbnail
        // only through an API a browser is not allowed to call
        var asked = url;
        fetch(api + '/api/encode/check?url=' + encodeURIComponent(url))
          .then(function(r){ return r.json(); })
          .then(function(j){
            if (encodeInput.value.trim() !== asked) return;      // the field moved on
            if (!j.ok) {
              encodeStatus.textContent = '✗ ' + (j.error || 'that link does not work');
              encodeStatus.className = 'enc-bad';
              return;
            }
            submitBtn.disabled = false;
            encodeStatus.textContent = '✓ ' + j.name +
              ' encode verified; this frame becomes the run thumbnail';
            encodeStatus.className = 'enc-good';
            if (j.thumb) {
              encodeThumb.onerror = function(){ encodeThumb.hidden = true; };
              encodeThumb.onload = function(){ encodeThumb.hidden = false; };
              encodeThumb.src = j.thumb;
            }
          })
          .catch(function(){
            encodeStatus.textContent = '✗ could not reach the archivist to check the link';
            encodeStatus.className = 'enc-bad';
          });
      }
      encodeInput.addEventListener('input', function(){
        clearTimeout(encTimer);
        encTimer = setTimeout(checkEncode, 400);
      });
      checkEncode();

      document.getElementById('s-preview-btn').addEventListener('click', function(){
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
        fetch(api + '/api/preview', {method: 'POST', body: previewForm})
          .then(function(r){ return r.json(); })
          .then(function(j){ previewNotes.innerHTML = j.ok ? j.html : escapeHtml(j.error || 'preview failed'); })
          .catch(function(){ previewNotes.textContent = 'The archivist is not reachable; the preview needs it.'; });
        preview.scrollIntoView({behavior: 'smooth', block: 'start'});
      });

      var submitting = false;
      submitForm.addEventListener('submit', function(ev){
        ev.preventDefault();
        if (fileRowsOf.submitform && !fileRowsOf.submitform.valid()) return;
        // one archive per press: the button used to be picked by class, which
        // matched Preview, so a double click submitted the run twice
        if (submitting) return;
        submitting = true;
        var submitBtn = document.getElementById('s-submit');
        if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Archiving…'; }
        note(msg, 'Archiving your run…', true);
        post('/api/submit', new FormData(submitForm), submitBtn).then(function(res){
          submitting = false;
          if (submitBtn) submitBtn.textContent = 'Submit';
          if (res.ok && res.j.ok) {
            if (submitBtn) submitBtn.disabled = true;      // done: never offer it again
            submitFormDirty = false;                  // archived: nothing left to lose
            dropDraft();
            submitForm.hidden = true;
            noteBuilt(msg, 'Archived as ' + res.j.id + '.' +
                      (res.j.forum ? ' Announced on the forum: ' + res.j.forum : ''),
                      res.j.serial,
                      'Your run page is live at ../runs/' + res.j.id + '/.', true);
          } else note(msg, res.j.error || 'something went wrong', false);
        });
      });
    });
  }

  // ---- create-game / create-category pages ----
  // The metrics editor both forms share: up to 4 rows, order = tie-break
  // hierarchy; the derived real-time metric is a checkbox, never a typed row.
  function initMetricsEd(root, initial){
    var rowsEl = root.querySelector('.mrows');
    var addBtn = root.querySelector('.med-add');
    var timeCheckbox = root.querySelector('.med-time');
    var metricsField = root.querySelector('[name=metrics]');
    var rows = [];   // {time:true} or {label,type,better,unit}
    function serialize(){
      var arr = rows.map(function(row){
        if (row.time) return {key: 'time'};
        return {label: row.label.value.trim(), type: row.type.value,
                better: row.better.value,
                unit: row.type.value === 'number' && row.unit.value.trim()
                      ? row.unit.value.trim() : undefined};
      }).filter(function(m){ return m.key === 'time' || m.label; });
      metricsField.value = arr.length ? JSON.stringify(arr) : '';
    }
    function paint(){
      rowsEl.innerHTML = '';
      rows.forEach(function(row, i){
        var div = el('div', 'mrow');
        if (row.time) {
          div.appendChild(el('span', 'mfixed', 'Real time (derived) — lower is better'));
        } else {
          div.appendChild(row.label); div.appendChild(row.type);
          div.appendChild(row.better); div.appendChild(row.unit);
          row.unit.hidden = row.type.value === 'time';
        }
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
        if (!row.time) {
          var removeBtn = el('button', 'btn quiet mmove', '×');
          removeBtn.type = 'button';
          removeBtn.addEventListener('click', function(){ rows.splice(i, 1); paint(); });
          div.appendChild(removeBtn);
        }
        rowsEl.appendChild(div);
      });
      addBtn.disabled = rows.length >= 4;
      timeCheckbox.disabled = !timeCheckbox.checked && rows.length >= 4;
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
    timeCheckbox.addEventListener('change', function(){
      if (timeCheckbox.checked) { if (rows.length < 4) rows.push({time: true}); else timeCheckbox.checked = false; }
      else rows = rows.filter(function(row){ return !row.time; });
      paint();
    });
    (initial || []).forEach(function(def){
      if (def.key === 'time') { rows.push({time: true}); timeCheckbox.checked = true; }
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
      document.getElementById('cc-gamename').textContent = catGameTitles[catGameKey];
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
    function busy(btn, on){
      if (!btn) return;
      btn.disabled = on;
      btn.classList.toggle('busy', on);
    }
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
