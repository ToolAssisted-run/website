// toolAssisted.run client script — the whole frontend behavior.
// A real file the generator ships verbatim, except two build-time
// substitutions (the accepted video platforms, host list and display
// names, both from archivist/providers.py).
// It talks to the backend (the archivist) only through its JSON API,
// and reads page data from embedded application/json blobs.
(function(){
  var T = window.TAR || {};
  var api = T.api, rel = T.rel || '';
  var V = '?v=' + (T.v || '0');
  var mep = fetch(api + '/api/me', {credentials: 'include'})
    .then(function(r){ return r.json(); })
    .catch(function(){ return {loggedIn: false, unreachable: true}; });

  // shared by every page: the submit preview, the news feed, anything that
  // puts text it did not write into the DOM
  function escH(s){
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;').replace(/'/g, '&#x27;');
  }
  function el(tag, cls, text){
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text) e.textContent = text;
    return e;
  }
  function note(box, text, good){
    box.hidden = false;
    box.textContent = text;
    box.className = 'actmsg ' + (good ? 'good' : 'bad');
  }
  var _namesP = null;
  function authorNames(){
    _namesP = _namesP || fetch(rel + 'assets/authornames.json' + V)
      .then(function(r){ return r.json(); }).catch(function(){ return []; });
    return _namesP;
  }
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
    function sync(){
      field.value = selected.join(',');
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

  function busy(btn, on){
    // archivist work takes time (git pushes, mail): the button that started
    // it spins, goes flat and grey, and cannot be pressed again
    if (!btn) return;
    btn.disabled = on;
    btn.classList.toggle('busy', on);
  }
  function post(path, fd, btn){
    busy(btn, true);
    return fetch(api + path, {method: 'POST', body: fd, credentials: 'include'})
      .then(function(r){ return r.json().then(function(j){ return {ok: r.ok, j: j}; }); })
      .catch(function(){ return {ok: false, j: {error: 'network error; the archivist may be unreachable'}}; })
      .then(function(res){ busy(btn, false); return res; });
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
  mep.then(function(d){
    // An archivist we cannot reach used to leave the nav simply empty, which
    // looks exactly like a broken page: say so instead, and offer the retry.
    var off = document.getElementById('navoffline');
    if (off && d.unreachable) {
      off.hidden = false;
      off.addEventListener('click', function(){
        off.textContent = 'retrying…';
        fetch(api + '/api/me', {credentials: 'include'})
          .then(function(r){ return r.json(); })
          .then(function(){ location.reload(); })
          .catch(function(){ off.textContent = 'still unreachable'; });
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
      fetch(rel + 'assets/news.json' + V).then(function(r){ return r.json(); })
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
    var wrap = el('span', 'acctmenu');
    var btn = el('button', 'avatarbtn');
    btn.setAttribute('aria-label', 'Account menu');
    if (d.avatar) {
      var img = el('img', 'avatar');
      img.src = d.avatar;
      img.alt = d.user;
      btn.appendChild(img);
    } else {
      btn.appendChild(el('span', 'avatar avatar-fallback', d.user.charAt(0).toUpperCase()));
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
    fetch(rel + 'assets/authorstats.json' + V).then(function(r){ return r.json(); })
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
    menu.appendChild(item('My contributions', rel + 'contribute/'));
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
    btn.addEventListener('click', function(ev){
      ev.stopPropagation();
      menu.hidden = !menu.hidden;
    });
    document.addEventListener('click', function(){ menu.hidden = true; });
    wrap.appendChild(btn);
    wrap.appendChild(menu);
    box.appendChild(wrap);
    var cur = 'system';
    try { cur = localStorage.getItem('tar-theme') || 'system'; } catch (e) {}
    theme.querySelectorAll('button').forEach(function(b){
      b.classList.toggle('on', b.dataset.theme === cur);
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
    var pick = el('input', 'pickbox');
    pick.setAttribute('list', listId);
    pick.placeholder = 'type to find; picking adds it';
    var chips = el('span', 'picked');
    var chosen = [];
    function sync(){
      input.value = chosen.join(' ');
      chips.innerHTML = '';
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
        chips.appendChild(c);
      });
    }
    function add(){
      var v = pick.value.trim();
      if (!v) return;
      var ok = allowed ? allowed().indexOf(v) >= 0 : true;
      if (ok && chosen.indexOf(v) < 0) chosen.push(v);
      if (ok) pick.value = '';
      sync();
    }
    pick.addEventListener('change', add);
    pick.addEventListener('keydown', function(e){
      if (e.key === 'Enter') { e.preventDefault(); add(); }
    });
    input.parentNode.insertBefore(pick, input);
    input.parentNode.insertBefore(chips, input);
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
    var el_ = document.getElementById(dataId);
    if (!el_) return;
    var D = JSON.parse(el_.textContent);
    mep.then(function(d){
      if (d.unreachable || !d.loggedIn) return;
      var who = d.user.toLowerCase();
      if ((D.experts || D.siteExperts || []).indexOf(who) < 0) return;
      var zone = document.getElementById(zoneId);
      var msg = document.getElementById(msgId);
      zone.hidden = false;
      var zbtn = document.getElementById(dataId + '-btn');
      if (zbtn) zbtn.hidden = false;
      var sysSel = document.getElementById('ga-system');
      if (sysSel && D.system_options) {
        D.system_options.forEach(function(s){
          var o = document.createElement('option');
          o.value = s.key;
          o.textContent = s.name;
          sysSel.appendChild(o);
        });
      }
      forms.forEach(function(spec){
        var form = document.getElementById(spec.id);
        if (!form) return;
        form.addEventListener('submit', function(ev){
          ev.preventDefault();
          if (spec.confirm && !window.confirm(spec.confirm)) return;
          post(spec.path, new FormData(form), form.querySelector('button'))
            .then(function(res){
              if (res.ok && res.j.ok) {
                note(msg, spec.done(res.j) + ' The site rebuilds from the archive; ' +
                          'it shows here in a few minutes.', true);
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
    {id: 'f-gamedelete', path: '/api/game/delete',
     confirm: 'Delete this game outright? Its runs survive, moved to the ' +
              'Uncategorized game of this system. This cannot be undone.',
     done: function(j){ return 'Deleted. ' + (j.runs_moved && j.runs_moved.length
       ? j.runs_moved.length + ' run(s) moved to ' + j.to + '.' : 'It held no runs.'); }}]);
  armZone('groupactdata', 'groupacts', 'groupact-msg', [
    {id: 'f-groupaddgame', path: '/api/game/create',
     done: function(j){ return j.game + ' is in this group.'; }},
    {id: 'f-groupremove', path: '/api/group/request-removal',
     done: function(){ return 'Filed. A site-wide expert answers it.'; }},
    {id: 'f-groupdelete', path: '/api/group/delete',
     confirm: 'Delete this group outright? Its games become ungrouped. ' +
              'This cannot be undone.',
     done: function(j){ return 'Deleted; ' + j.released.length +
       ' game(s) are ungrouped.'; }}]);
  // the member page: the Committee's delete, gated tighter when the target
  // sits on the Committee (the Founder's alone) and never for the Founder
  (function(){
    var mEl = document.getElementById('memberactdata');
    if (!mEl) return;
    var M = JSON.parse(mEl.textContent);
    mep.then(function(d){
      if (d.unreachable || !d.loggedIn) return;
      var who = d.user.toLowerCase();
      if (M.committee.indexOf(who) < 0) return;
      if (M.targetSeated && M.founders.indexOf(who) < 0) return;
      var zone = document.getElementById('memberacts');
      var msg = document.getElementById('memberact-msg');
      zone.hidden = false;
      var form = document.getElementById('f-memberdelete');
      form.addEventListener('submit', function(ev){
        ev.preventDefault();
        if (!window.confirm('Delete the member ' + M.target + ' outright? ' +
                            'This cannot be undone.')) return;
        post('/api/member/delete', new FormData(form), form.querySelector('button'))
          .then(function(res){
            if (res.ok && res.j.ok) {
              note(msg, 'Deleted. The site rebuilds from the archive; the page goes ' +
                        'in a few minutes.', true);
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
        res.j.pending.forEach(function(r){
          var line = el('p', 'statline');
          line.appendChild(el('b', '', r.member));
          line.appendChild(el('span', '', ' claims '));
          line.appendChild(el('b', '', r.identity));
          line.appendChild(el('span', 'actmeta', ' ' + (r.email || 'no address on file') +
                              ' · ' + r.date));
          line.appendChild(el('p', 'actnote', r.evidence));
          var b = el('button', 'btn quiet', 'Answer');
          b.addEventListener('click', function(){
            document.getElementById(ids.identity).value = r.identity;
            document.getElementById(ids.what).textContent =
              'Answering the claim by ' + r.member + ' to the name ' + r.identity;
            form.hidden = false;
          });
          line.appendChild(b);
          list.appendChild(line);
        });
      });
    }
    function answer(approve){
      var fd = new FormData(form);
      fd.append('action', approve ? 'approved' : 'denied');
      if (approve) fd.delete('note');
      post('/api/claim/decide', fd, form.querySelector('button')).then(function(res){
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
  var fpanelEl = document.getElementById('fpaneldata');
  if (fpanelEl) {
    var F = JSON.parse(fpanelEl.textContent);
    mep.then(function(d){
      var gate = document.getElementById('fpanel-gate');
      if (d.unreachable) {
        gate.textContent = 'The archivist is unreachable, so who you are cannot be ' +
          'checked right now.';
        return;
      }
      if (!d.loggedIn || F.founders.indexOf(d.user.toLowerCase()) < 0) {
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
               form.querySelector('button')).then(function(res){
            if (res.ok && res.j.ok) {
              note(msg, (res.j.action === 'granted' ? 'Seated. ' : 'Unseated. ') +
                        (res.j.told || '') + ' It shows on the site in a few minutes.',
                   true);
              form.reset();
            } else note(msg, res.j.error || 'something went wrong', false);
          });
        });
      });
    });
  }

  // ---- steering committee panel ----
  var cpanelEl = document.getElementById('cpaneldata');
  if (cpanelEl) {
    var C = JSON.parse(cpanelEl.textContent);
    mep.then(function(d){
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
      if (C.committee.indexOf(d.user.toLowerCase()) < 0) {
        gate.textContent = 'This panel is for the Steering Committee, and you are not on it.';
        return;
      }
      gate.hidden = true;
      document.getElementById('cpanel').hidden = false;
      var cmsg = document.getElementById('cpanel-msg');
      mountClaimsBoard({list: 'cpanel-list', form: 'f-claimdecide',
                        identity: 'cdecide-identity', what: 'cdecide-what',
                        yes: 'cdecide-yes', no: 'cdecide-no', cancel: 'cdecide-cancel'},
                       cmsg);

      // recording a Committee decision (moved here from the members page:
      // governance tools live in panels, the members page is about the runs)
      var roleForm = document.getElementById('f-role');
      var roleMsg = document.getElementById('role-msg');
      // who can be granted a role is whoever lacks it; who can lose one is
      // whoever holds it. The list follows the two selects, so the box never
      // offers a name the archivist would refuse.
      var roleSel = roleForm.querySelector('[name=role]');
      var actSel = roleForm.querySelector('[name=action]');
      var candDl = document.getElementById('dl-role-candidates');
      function refreshRoleCandidates(){
        var holders = (roleSel.value === 'committee' ? C.committeeNames : C.moderators)
          .map(function(x){ return x.toLowerCase(); });
        var pool = actSel.value === 'granted'
          ? C.members.filter(function(m){ return holders.indexOf(m.toLowerCase()) < 0; })
          : C.members.filter(function(m){ return holders.indexOf(m.toLowerCase()) >= 0; });
        candDl.innerHTML = '';
        pool.forEach(function(m){
          var o = document.createElement('option');
          o.value = m;
          candDl.appendChild(o);
        });
      }
      roleSel.addEventListener('change', refreshRoleCandidates);
      actSel.addEventListener('change', refreshRoleCandidates);
      refreshRoleCandidates();
      roleForm.addEventListener('submit', function(ev){
        ev.preventDefault();
        post('/api/role/decide', new FormData(roleForm), roleForm.querySelector('button'))
          .then(function(res){
            if (res.ok && res.j.ok) {
              note(roleMsg, 'Recorded: ' + res.j.votes + ' of ' + res.j.committee +
                        ' voted for it. The site rebuilds from the archive; the badge ' +
                        'appears here in a few minutes.', true);
              roleForm.reset();
            } else note(roleMsg, res.j.error || 'something went wrong', false);
          });
      });
      // the whole-site appointment: everyone who does not already hold it
      var sel = document.getElementById('siteexpert-user');
      C.members.forEach(function(m){
        if (C.siteExperts.indexOf(m.toLowerCase()) >= 0) return;
        var o = document.createElement('option');
        o.value = m;
        o.textContent = m;
        sel.appendChild(o);
      });
      var sform = document.getElementById('f-siteexpert');
      sform.addEventListener('submit', function(ev){
        ev.preventDefault();
        post('/api/expert/appoint', new FormData(sform),
             sform.querySelector('button')).then(function(res){
          if (res.ok && res.j.ok) {
            note(cmsg, res.j.user + ' is now an expert for the whole site. It shows ' +
                       'on the site in a few minutes.', true);
            sform.reset();
          } else note(cmsg, res.j.error || 'something went wrong', false);
        });
      });
    });
  }

  // ---- expert panel ----
  var panelEl = document.getElementById('paneldata');
  if (panelEl) {
    var P = JSON.parse(panelEl.textContent);
    mep.then(function(d){
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
      var u = d.user.toLowerCase();
      var mine = P.roster.filter(function(e){ return e.user.toLowerCase() === u; });
      var amCommittee = P.committee.indexOf(u) >= 0;
      // a Committee seat opens the panel too: any single Committee member may
      // appoint an expert at any scope (Principles 2.5.3), so the appointment
      // forms are theirs even when they hold no expert scope of their own
      if (!mine.length && !amCommittee) {
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
      mine.forEach(function(e){
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
      P.groups.forEach(function(gr){
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
        return P.roster.filter(function(e){ return e.user.toLowerCase() === low; })
                       .map(function(e){ return e.scope; });
      }
      var myScopes = mine.map(function(e){ return e.scope; });
      var amSite = myScopes.indexOf('site') >= 0;

      function fill(sel, items, empty){
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
      var myGames = P.games.filter(function(g){ return coversGame(myScopes, g.key); });
      var myGroups = P.groups.filter(function(gr){ return coversGroup(myScopes, gr.key); });
      var apptGames = amCommittee ? P.games : myGames;
      var apptGroups = amCommittee ? P.groups : myGroups;
      fill(document.getElementById('appoint-game'),
           apptGames.map(function(g){ return {value: g.key, label: g.title + ' (' + g.key + ')'}; }),
           'no game is yours to hand out');
      fill(document.getElementById('appoint-group'),
           apptGroups.map(function(gr){ return {value: 'group:' + gr.key, label: gr.title}; }),
           'no group is yours to hand out');
      if (amSite || amCommittee) {
        document.getElementById('appoint-wide-wrap').hidden = false;
        fill(document.getElementById('appoint-wide'),
             P.scopes.filter(function(s){ return s.key !== 'site' && s.key.indexOf('/') < 0
                                                 && s.key.indexOf('group:') < 0; })
                     .map(function(s){ return {value: s.key, label: s.label}; }),
             'nothing');
      }

      // members who do not already speak for the chosen scope
      function refreshCandidates(scopeSel, userSel){
        if (!scopeSel || !userSel) return;
        var scope = scopeSel.value;
        var free = P.members.filter(function(m){
          var s = scopesOf(m);
          if (!s.length) return true;
          if (s.indexOf('site') >= 0) return false;
          if (scope.indexOf('group:') === 0) return !coversGroup(s, scope.slice(6));
          if (scope.indexOf('/') > 0) return !coversGame(s, scope);
          return s.indexOf(scope) < 0;
        });
        fill(userSel, free.map(function(m){ return {value: m, label: m}; }),
             'everybody here already speaks for it');
      }
      [['appoint-game', 'appoint-game-user'], ['appoint-group', 'appoint-group-user'],
       ['appoint-wide', 'appoint-wide-user']].forEach(function(pair){
        var s = document.getElementById(pair[0]), us = document.getElementById(pair[1]);
        if (!s) return;
        s.addEventListener('change', function(){ refreshCandidates(s, us); });
        refreshCandidates(s, us);
      });

      // ---- what is waiting on me ----
      // ratification is gone: creations are real on arrival, so the only
      // thing that waits on anybody is a removal request
      var pending = (amSite ? P.removals : []).map(function(r){
        return {kind: 'removal', key: r.key, sub: r.kind, title: r.title,
                what: 'removal asked by ' + r.by + ' · ' + r.reason}; });
      var plist = document.getElementById('pending-list');
      var dform = document.getElementById('f-decide');
      if (!pending.length) {
        plist.appendChild(el('p', 'emptynote', 'Nothing is waiting for you.'));
      } else {
        pending.forEach(function(item){
          var line = el('p', 'statline');
          line.appendChild(el('b', '', item.title));
          line.appendChild(el('span', 'actmeta', ' ' + item.what + ' '));
          var b = el('button', 'btn quiet', 'Decide');
          b.addEventListener('click', function(){
            document.getElementById('decide-kind').value = item.kind;
            document.getElementById('decide-key').value = item.key;
            document.getElementById('decide-sub').value = item.sub || '';
            dyes.textContent = item.kind === 'removal' ? 'Grant the removal' : 'Approve';
            dno.textContent = item.kind === 'removal' ? 'Decline it' : 'Refuse';
            document.getElementById('decide-what').textContent =
              'Deciding on ' + item.title + ' (' + item.what + ')';
            dform.hidden = false;
          });
          line.appendChild(b);
          plist.appendChild(line);
        });
      }
      function decide(approve){
        var kind = document.getElementById('decide-kind').value;
        var key = document.getElementById('decide-key').value;
        var sub = document.getElementById('decide-sub').value;
        var reason = dform.querySelector('[name=reason]').value.trim();
        if (!approve && reason.length < 8) {
          note(msg, 'Saying no needs a reason the other person can read and answer.', false);
          return;
        }
        var fd = new FormData();
        var path;
        if (kind === 'removal') {
          // answering a request another member filed, not deciding for myself
          fd.append('kind', sub);
          fd.append('target', key);
          fd.append('action', approve ? 'granted' : 'declined');
          if (!approve) fd.append('note', reason);
          path = '/api/removal/decide';
        } else {
          return;   // nothing but removals waits on anybody any more
        }
        post(path, fd, dform.querySelector('button')).then(function(res){
          if (res.ok && res.j.ok) {
            note(msg, (approve ? 'Approved. ' : 'Refused. ') +
                      'The site rebuilds from the archive; it shows here in about a ' +
                      'minute.', true);
            dform.hidden = true;
          } else note(msg, res.j.error || 'something went wrong', false);
        });
      }
      var dyes = document.getElementById('decide-yes');
      var dno = document.getElementById('decide-no');
      var dcancel = document.getElementById('decide-cancel');
      if (dyes) dyes.addEventListener('click', function(){ decide(true); });
      if (dno) dno.addEventListener('click', function(){ decide(false); });
      if (dcancel) dcancel.addEventListener('click', function(){ dform.hidden = true; });

      // the lists the remaining forms run on: what I may step down from, the
      // group I may change, and the games I may name in one
      fill(document.getElementById('resign-scope'),
           [{value: '', label: 'every scope I hold'}].concat(
             mine.map(function(e){ return {value: e.scope, label: e.label}; })),
           'nothing to step down from');
      fill(document.getElementById('groupedit-key'),
           myGroups.map(function(gr){
             return {value: gr.key,
                     label: gr.title}; }),
           'no group is yours to change');
      // the group pickers offer only games a group could actually take: a
      // game already in one would be refused, since a game belongs to one
      var groupable = myGames.filter(function(g){ return !g.group; });
      var gdl = document.getElementById('panel-gamelist');
      groupable.forEach(function(g){
        var o = document.createElement('option');
        o.value = g.key;
        o.label = g.title;
        gdl.appendChild(o);
      });
      var groupableKeys = groupable.map(function(g){ return g.key; });
      armMultiPick(document.getElementById('f-groupnew'), 'games',
                   'panel-gamelist', function(){ return groupableKeys; });
      var editSel = document.getElementById('groupedit-key');
      armMultiPick(document.getElementById('f-groupedit'), 'add',
                   'panel-gamelist', function(){ return groupableKeys; });
      // removing offers only what the chosen group actually holds
      var rdl = document.createElement('datalist');
      rdl.id = 'groupedit-removelist';
      document.body.appendChild(rdl);
      function refreshRemoveList(){
        rdl.innerHTML = '';
        (gamesIn[editSel.value] || []).forEach(function(k){
          var o = document.createElement('option');
          o.value = k;
          rdl.appendChild(o);
        });
      }
      editSel.addEventListener('change', refreshRemoveList);
      refreshRemoveList();
      armMultiPick(document.getElementById('f-groupedit'), 'remove',
                   'groupedit-removelist', function(){
        return gamesIn[editSel.value] || [];
      });
      // annulment names an expert, so the experts are the list
      var edl = document.getElementById('panel-expertlist');
      if (edl) {
        var seen = {};
        P.roster.forEach(function(e){
          if (seen[e.user]) return;
          seen[e.user] = 1;
          var o = document.createElement('option');
          o.value = e.user;
          edl.appendChild(o);
        });
      }
      // annulment names any scope, since the Committee decides it, not me
      var sdl = document.getElementById('panel-scopelist');
      P.scopes.forEach(function(s){
        var o = document.createElement('option');
        o.value = s.key;
        o.label = s.label;
        sdl.appendChild(o);
      });

      if (P.committee.indexOf(u) >= 0) {
        document.getElementById('panel-annul-wrap').hidden = false;
      }

      function armPanel(id, path, done){
        var form = document.getElementById(id);
        if (!form) return;
        form.addEventListener('submit', function(ev){
          ev.preventDefault();
          post(path, new FormData(form), form.querySelector('button')).then(function(res){
            if (res.ok && res.j.ok) {
              note(msg, done(res.j) + ' The site rebuilds from the archive; it shows ' +
                        'here in a few minutes.', true);
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
  var dataEl = document.getElementById('actdata');
  if (dataEl) {
    var D = JSON.parse(dataEl.textContent);
    // the visit tally: counted here so plain crawlers stay out of it
    if (api) {
      var vfd = new FormData();
      vfd.append('run', D.run);
      fetch(api + '/api/visit', {method: 'POST', body: vfd})
        .then(function(r){ return r.json(); })
        .then(function(j){
          if (!j.ok) return;
          document.getElementById('visitnum').textContent = j.visits.toLocaleString();
          document.getElementById('visitbadge').hidden = false;
        }).catch(function(){});
    }
    mep.then(function(d){
      var zone = document.getElementById('actzone');
      if (!zone || d.unreachable) return;
      var msg = document.getElementById('act-msg');
      var u = d.loggedIn ? d.user.toLowerCase() : null;
      var isAuthor = u !== null && D.authors.indexOf(u) >= 0;
      var isExpert = u !== null && D.experts.indexOf(u) >= 0;
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
        var out = (menu && document.getElementById('expert-msg')) || msg;
        anything = true;
        if (prefill) prefill(form);
        form.addEventListener('submit', function(ev){
          ev.preventDefault();
          var fd = new FormData(form);
          fd.append('run', D.run);
          post(path, fd, form.querySelector('button')).then(function(res){
            if (res.ok && res.j.ok) {
              note(out, 'Recorded, thank you. The site rebuilds from the archive; ' +
                        'your change appears here in a few minutes.', true);
            } else note(out, res.j.error || 'something went wrong', false);
          });
        });
      }
      if (!d.loggedIn) {
        zone.hidden = false;
        document.getElementById('act-login').hidden = false;
        return;
      }
      if (isAuthor) arm('f-withdraw', '/api/withdraw');
      if (isAuthor || isExpert) {
        arm('f-edit', '/api/edit', function(form){
          if (isAuthor) {
            initAuthorPick(form.querySelector('.authpick'), D.authorsDisplay);
          } else {
            // the author list and their supplementary files are never an
            // expert's to touch; an expert edit states its public reason
            var fa = document.getElementById('fe-authors');
            fa.hidden = true;
            fa.querySelector('[name=authors]').disabled = true;
            var fat = document.getElementById('fe-attach');
            fat.hidden = true;
            fat.querySelector('[name=attachments]').disabled = true;
            var why = document.getElementById('fe-why');
            why.hidden = false;
            why.querySelector('[name=reason]').required = true;
          }
          form.querySelector('[name=emulator]').value = D.emulator;
          form.querySelector('[name=completed]').value = D.completed;
          var ta = form.querySelector('[name=notes]');
          fetch(D.notesUrl).then(function(r){ return r.ok ? r.text() : ''; })
            .then(function(txt){ ta.value = txt; }).catch(function(){});
        });
      }
      if (!isAuthor && !D.imported) {
        if (!D.videoOnly) {
          if (D.reproduced.indexOf(u) < 0) arm('f-repro', '/api/reproduce');
          else arm('f-note-repro', '/api/note', function(form){
            form.querySelector('[name=notes]').value = D.roleNotes.reproducer || '';
          });
        }
        if (D.hasEncode && D.verified.indexOf(u) < 0) arm('f-verify', '/api/verify');
        if (!D.videoOnly && (D.consoled || []).indexOf(u) < 0) arm('f-console', '/api/console-verify');
        if (D.verified.indexOf(u) >= 0) arm('f-note-verify', '/api/note', function(form){
          form.querySelector('[name=notes]').value = D.roleNotes.verifier || '';
        });
        if (D.openCase) {
          if (D.openCase.verifiers.indexOf(u) >= 0 && D.openCase.voted.indexOf(u) < 0) {
            var vf = document.getElementById('f-vote');
            vf.hidden = false;
            anything = true;
            vf.querySelectorAll('button[data-reaffirm]').forEach(function(b){
              b.addEventListener('click', function(ev){
                ev.preventDefault();
                var fd = new FormData(vf);
                fd.append('run', D.run);
                fd.append('case', D.openCase.id);
                fd.append('reaffirm', b.dataset.reaffirm);
                post('/api/case/vote', fd, b).then(function(res){
                  if (res.ok && res.j.ok) {
                    vf.hidden = true;
                    note(msg, 'Vote recorded. The case is now ' + res.j.case_status + '.', true);
                  } else note(msg, res.j.error || 'something went wrong', false);
                });
              });
            });
          }
        } else if (D.liveVerifs > 0) {
          document.getElementById('f-case-wrap').hidden = false;
          arm('f-case', '/api/case/open');
        }
      }
      if (isExpert) {
        arm('f-rundelete', '/api/run/delete');
        var rdel = document.getElementById('f-rundelete');
        if (rdel) rdel.addEventListener('submit', function(ev){
          if (!window.confirm('Delete this run outright? This cannot be undone, ' +
                              'and only your reason remains.')) {
            ev.stopImmediatePropagation();
            ev.preventDefault();
          }
        }, true);
        // invalidating: the target is a live act on this run, so offer exactly those
        var invSel = document.getElementById('inv-target');
        var kinds = [['reproduction', D.reproducedNames || []],
                     ['verification', D.verifiedNames || []],
                     ['console', D.consoledNames || []]];
        var options = [];
        kinds.forEach(function(pair){
          pair[1].forEach(function(name){
            options.push({kind: pair[0], name: name});
          });
        });
        if (invSel && options.length) {
          options.forEach(function(o, i){
            var opt = document.createElement('option');
            opt.value = o.name;
            opt.dataset.kind = o.kind;
            opt.textContent = o.kind + ' by ' + o.name;
            invSel.appendChild(opt);
          });
          var kindField = document.getElementById('inv-kind');
          function syncKind(){
            var opt = invSel.options[invSel.selectedIndex];
            kindField.value = opt ? opt.dataset.kind : '';
          }
          invSel.addEventListener('change', syncKind);
          syncKind();
          arm('f-invalidate', '/api/invalidate');
        }
        // closing a report: only the open ones on this run
        var resSel = document.getElementById('res-report');
        if (resSel && (D.openReports || []).length) {
          D.openReports.forEach(function(rep){
            var opt = document.createElement('option');
            opt.value = rep.id;
            opt.textContent = 'R' + rep.id + ' · ' + rep.kind + ' · by ' + rep.by;
            resSel.appendChild(opt);
          });
          arm('f-resolve', '/api/report/resolve');
        }
      }
      if ((D.noteExperts || []).indexOf(u) >= 0) {
        arm('f-expertnote', '/api/note', function(form){
          form.querySelector('[name=notes]').value = D.roleNotes.expert || '';
        });
      }
      if (anything) zone.hidden = false;
    });
  }

  // ---- the game editor (covering experts) ----
  var geEl = document.getElementById('gameeditdata');
  if (geEl) {
    var GE = JSON.parse(geEl.textContent);
    mep.then(function(d){
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
      if ((GE.experts || []).indexOf(d.user.toLowerCase()) < 0) {
        gate.textContent = 'This page is for the experts covering this game, ' +
          'and you hold no scope over it.';
        return;
      }
      gate.hidden = true;
      document.getElementById('geditor').hidden = false;
      var msg = document.getElementById('ge-msg');
      function ok(text){
        note(msg, text + ' The site rebuilds from the archive; it shows in a ' +
                  'few minutes.', true);
      }
      function wire(id, path, confirmText, done){
        var form = document.getElementById(id);
        if (!form) return;
        form.addEventListener('submit', function(ev){
          ev.preventDefault();
          if (confirmText && !window.confirm(confirmText)) return;
          post(path, new FormData(form), form.querySelector('button'))
            .then(function(res){
              if (res.ok && res.j.ok) ok(done(res.j));
              else note(msg, res.j.error || 'something went wrong', false);
            });
        });
      }
      wire('f-ge-title', '/api/expert/edit', null,
           function(j){ return 'Renamed to ' + j.to + '.'; });
      wire('f-ge-thumb', '/api/expert/edit', null,
           function(){ return 'Thumbnail set.'; });
      wire('f-ge-add', '/api/category/add', null,
           function(j){ return 'Added ' + j.key + '.'; });
      var addMed = document.querySelector('#f-ge-add .metriced');
      if (addMed) initMetricsEd(addMed, null);

      // one card per category option: edit in place, delete-if-empty
      var box = document.getElementById('ge-cats');
      (GE.options || []).forEach(function(o){
        var card = el('div', 'gecard');
        var head = el('div', 'gehead');
        head.appendChild(el('b', '', o.key));
        head.appendChild(el('span', 'actmeta',
          ' ' + o.runs + ' run' + (o.runs === 1 ? '' : 's')));
        card.appendChild(head);
        function field(labelText, tag){
          var lab = el('label', '', labelText + ' ');
          var inp = el(tag === 'textarea' ? 'textarea' : 'input');
          lab.appendChild(inp);
          card.appendChild(lab);
          return inp;
        }
        var labIn = field('Label');
        labIn.value = o.label;
        var ruleIn = field('Rule', 'textarea');
        ruleIn.value = o.rule;
        ruleIn.rows = 2;
        // the category's metrics, editable like label and rule; adding one
        // writes an explicit 0 onto every run here, for experts to fill
        var medRoot = el('div');
        medRoot.innerHTML = document.getElementById('med-skeleton').innerHTML;
        var medBox = medRoot.firstElementChild;
        card.appendChild(medBox);
        var med = initMetricsEd(medBox, o.metrics || null);
        var med0 = med.value();
        var whyIn = field('Why (published with the change)');
        whyIn.placeholder = 'required to save a change';
        var row = el('div', 'gebtns');
        var save = el('button', 'btn', 'Save');
        save.type = 'button';
        save.addEventListener('click', function(){
          var jobs = [];
          if (labIn.value.trim() !== o.label) jobs.push(['label', labIn.value.trim()]);
          if (ruleIn.value.trim() !== o.rule) jobs.push(['rule', ruleIn.value.trim()]);
          if (med.value() !== med0) jobs.push(['metrics', med.value()]);
          if (!jobs.length) { note(msg, 'Nothing changed on ' + o.key + '.', false); return; }
          function step(){
            if (!jobs.length) {
              o.label = labIn.value.trim();
              o.rule = ruleIn.value.trim();
              med0 = med.value();
              ok('Saved ' + o.key + '.');
              return;
            }
            var job = jobs.shift();
            var fd = new FormData();
            fd.append('kind', 'category');
            fd.append('target', GE.game + ':' + o.key);
            fd.append('field', job[0]);
            fd.append('value', job[1]);
            fd.append('reason', whyIn.value.trim());
            post('/api/expert/edit', fd, save).then(function(res){
              if (res.ok && res.j.ok) step();
              else note(msg, res.j.error || 'something went wrong', false);
            });
          }
          step();
        });
        row.appendChild(save);
        if (!o.runs) {
          var del = el('button', 'btn danger', 'Delete');
          del.type = 'button';
          del.addEventListener('click', function(){
            if (!window.confirm('Delete the unused category ' + o.key + '?')) return;
            var fd = new FormData();
            fd.append('game', GE.game);
            fd.append('option', o.key);
            post('/api/category/delete', fd, del).then(function(res){
              if (res.ok && res.j.ok) { card.remove(); ok('Removed ' + o.key + '.'); }
              else note(msg, res.j.error || 'something went wrong', false);
            });
          });
          row.appendChild(del);
        }
        card.appendChild(row);
        box.appendChild(card);
      });
      if (!(GE.options || []).length) {
        box.appendChild(el('p', 'emptynote',
          'No categories yet: add the first one below.'));
      }
    });
  }

  // ---- file a claim: anybody logged in, one at a time ----
  var claimForm = document.getElementById('f-claim');
  if (claimForm) {
    mep.then(function(d){
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
             claimForm.querySelector('button')).then(function(res){
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
  var siteExperts = document.getElementById('siteexperts');
  if (siteExperts) {
    var SE = JSON.parse(siteExperts.textContent);
    mep.then(function(d){
      if (!d.loggedIn || SE.indexOf(d.user.toLowerCase()) < 0) return;
      var wrap = document.getElementById('attest-wrap');
      var form = document.getElementById('f-attest');
      var msg = document.getElementById('attest-msg');
      wrap.hidden = false;
      form.addEventListener('submit', function(ev){
        ev.preventDefault();
        post('/api/claim/attest', new FormData(form), form.querySelector('button'))
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
  var likeEl = document.getElementById('likedata');
  if (likeEl) {
    var L = JSON.parse(likeEl.textContent);
    var lbtn = document.getElementById('likebtn');
    mep.then(function(d){
      if (!lbtn) return;
      if (d.unreachable) { lbtn.disabled = true; return; }
      if (!d.loggedIn) {
        lbtn.addEventListener('click', function(){ location.href = api + '/login'; });
        lbtn.title = 'Log in to like this run';
        return;
      }
      var u = d.user.toLowerCase();
      if (L.authors.indexOf(u) >= 0) {
        lbtn.disabled = true;
        lbtn.title = 'Authors cannot like their own run';
        return;
      }
      // the same star both ways: press to like, press again to take it back
      function paint(liked){
        lbtn.classList.toggle('on', liked);
        lbtn.title = liked ? 'You like this run; press again to take it back'
                           : 'Like this run';
      }
      paint(L.likes.indexOf(u) >= 0);
      lbtn.addEventListener('click', function(){
        var fd = new FormData();
        fd.append('run', L.run);
        post('/api/like', fd, lbtn).then(function(res){
          if (res.ok && res.j.ok) {
            document.getElementById('likecount').textContent = res.j.likes;
            paint(res.j.liked);
          }
        });
      });
    });
    mep.then(function(d){
      if (!d.loggedIn || d.unreachable) return;
      var rbox = document.getElementById('reportbox');
      if (!rbox) return;
      rbox.hidden = false;
      var rform = document.getElementById('f-report');
      rform.addEventListener('submit', function(ev){
        ev.preventDefault();
        var fd = new FormData(rform);
        fd.append('run', L.run);
        var msg = document.getElementById('report-msg');
        post('/api/report', fd, rform.querySelector('button')).then(function(res){
          if (res.ok && res.j.ok) {
            rbox.hidden = true;
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
  function unblurAll(){
    document.querySelectorAll('.nsfwblur').forEach(function(i){ i.classList.remove('nsfwblur'); });
    document.querySelectorAll('.nsfw18').forEach(function(b){ b.remove(); });
  }
  var gate = document.getElementById('nsfwgate');
  function revealGate(){
    var tpl = document.getElementById('nsfwreal');
    if (gate && tpl) gate.replaceWith(tpl.content.cloneNode(true));
  }
  if (adultOk()) { unblurAll(); revealGate(); }
  var okbtn = document.getElementById('nsfwok');
  if (okbtn) okbtn.addEventListener('click', function(){
    try { sessionStorage.setItem('tar-adult', '1'); } catch (e) {}
    unblurAll(); revealGate();
  });

  // ---- submit page ----
  var sform = document.getElementById('submitform');
  if (sform) {
    var GD = JSON.parse(document.getElementById('gamedata').textContent);
    var G = GD.games;
    mep.then(function(d){
      var msg = document.getElementById('s-msg');
      if (d.unreachable) { note(msg, 'The archivist is not reachable right now; try again later.', false); return; }
      if (!d.loggedIn) { document.getElementById('s-login').hidden = false; return; }
      sform.hidden = false;
      // half-written submissions are easy to lose to a stray click: once
      // anything in the form changes, leaving asks the standard are-you-sure
      var sformDirty = false;
      sform.addEventListener('input', function(){ sformDirty = true; });
      sform.addEventListener('change', function(){ sformDirty = true; });
      window.addEventListener('beforeunload', function(ev){
        if (!sformDirty) return;
        ev.preventDefault();
        ev.returnValue = '';
      });
      // video-only: the movie input and the stated time trade places, and
      // the one that is hidden must not hold a stale required flag
      var vonly = document.getElementById('s-videoonly');
      var mwrap = document.getElementById('s-moviewrap');
      var twrap = document.getElementById('s-timewrap');
      // the stated time is four number boxes (h m s ms): a format mistake is
      // impossible, and the canonical [h:]mm:ss.mmm value is composed here
      var tsegs = ['t-h', 't-m', 't-s', 't-ms'].map(function(id){
        return document.getElementById(id);
      });
      var timeField = document.getElementById('s-time');
      function two(n){ return (n < 10 ? '0' : '') + n; }
      function composeTime(){
        if (!timeField) return;
        var v = tsegs.map(function(seg){
          if (!seg || seg.value === '') return 0;
          var n = parseInt(seg.value, 10) || 0;
          var max = parseInt(seg.getAttribute('max') || seg.dataset.max || '999', 10);
          n = Math.max(0, Math.min(max, n));
          if (String(n) !== seg.value) seg.value = n;
          return n;
        });
        var h = v[0], m = v[1], s = v[2], ms = v[3];
        var body = two(m) + ':' + two(s) + '.' + ('00' + ms).slice(-3);
        timeField.value = (h + m + s + ms) === 0 ? '' : (h > 0 ? h + ':' : '') + body;
      }
      tsegs.forEach(function(seg){
        if (seg) seg.addEventListener('input', composeTime);
      });
      var gsel = document.getElementById('s-game'), csel = document.getElementById('s-goal');
      // the category's stated metrics: fields appear on category pick, in
      // the dashed box; the derived real time is never typed for movie runs
      var mbox = document.getElementById('s-metrics');
      var mfields = document.getElementById('s-mfields');
      var curMetrics = null;   // null = classic (real time, lower is better)
      function wantsTime(){
        if (csel.value === 'unclassified') return true;
        return !curMetrics || curMetrics.some(function(m){ return m.key === 'time'; });
      }
      function statedDefs(){
        if (csel.value === 'unclassified') return [];
        return (curMetrics || []).filter(function(m){ return m.key !== 'time'; });
      }
      function secsOf(v){ return v === '' ? 0 : (parseFloat(v) || 0); }
      function buildMetricFields(){
        mfields.innerHTML = '';
        statedDefs().forEach(function(m){
          var lab = document.createElement('label');
          lab.textContent = m.label + (m.unit ? ' (' + m.unit + ')' : '')
            + ' — ' + (m.better === 'higher' ? 'higher' : 'lower') + ' is better';
          mfields.appendChild(lab);
          var hid = document.createElement('input');
          hid.type = 'hidden'; hid.name = 'metric_' + m.key;
          if (m.type === 'time') {
            // segmented h/m/s/ms, composed into seconds
            var wrap = document.createElement('div');
            wrap.className = 'timepick';
            var segs = [['h', 999], ['m', 59], ['s', 59], ['ms', 999]].map(function(sp){
              var span = document.createElement('span'); span.className = 'tseg';
              var inp = document.createElement('input');
              inp.type = 'number'; inp.inputMode = 'numeric';
              inp.min = 0; inp.max = sp[1]; inp.placeholder = sp[0] === 'h' ? '0' : '00';
              var sl = document.createElement('label'); sl.textContent = sp[0];
              span.appendChild(inp); span.appendChild(sl); wrap.appendChild(span);
              return inp;
            });
            function compose(){
              var v = segs.map(function(s_){ return Math.max(0, parseInt(s_.value, 10) || 0); });
              hid.value = String(v[0] * 3600 + v[1] * 60 + v[2] + v[3] / 1000);
            }
            segs.forEach(function(s_){ s_.addEventListener('input', compose); });
            segs[2].required = true;
            compose();
            mfields.appendChild(wrap);
          } else {
            var num = document.createElement('input');
            num.type = 'number'; num.min = 0; num.step = 'any';
            num.required = true; num.inputMode = 'decimal';
            num.addEventListener('input', function(){ hid.value = String(secsOf(num.value)); });
            mfields.appendChild(num);
          }
          mfields.appendChild(hid);
        });
      }
      function paintKind(){
        var v = vonly.checked;
        mwrap.hidden = v;
        var wt = wantsTime();
        twrap.hidden = !(v && wt);
        mbox.hidden = statedDefs().length === 0 && twrap.hidden;
        mwrap.querySelector('input').required = !v;
        var secs = document.getElementById('t-s');
        if (secs) secs.required = v && wt;
        composeTime();
      }
      vonly.addEventListener('change', paintKind);
      paintKind();
      var goalCache = {};
      function fillGoals(goals){
        csel.innerHTML = '';
        (goals || []).forEach(function(g){
          var o = document.createElement('option');
          o.value = g.key; o.textContent = g.label;
          csel.appendChild(o);
        });
        var u = document.createElement('option');
        u.value = 'unclassified'; u.textContent = 'Unclassified (no goal; ranked by likes)';
        csel.appendChild(u);
        paintCategory();
      }
      function loadGoals(){
        // categories arrive from the archive itself when a game is picked:
        // the page ships no per-game payload up front
        var ccbtn = document.getElementById('s-createcat');
        if (ccbtn) {
          if (gsel.value) {
            ccbtn.href = '../create-category/?game=' + encodeURIComponent(gsel.value);
            ccbtn.removeAttribute('aria-disabled');
          } else {
            ccbtn.removeAttribute('href');
            ccbtn.setAttribute('aria-disabled', 'true');
          }
        }
        if (!gsel.value) { fillGoals([]); return; }
        if (goalCache[gsel.value]) { fillGoals(goalCache[gsel.value]); return; }
        var key = gsel.value;
        fillGoals([]);
        fetch(GD.raw + '/games/' + key + '/categories.json')
          .then(function(r){ return r.ok ? r.json() : null; })
          .then(function(c){
            if (!c || gsel.value !== key) return;
            var goals = [];
            (c.dimensions || []).forEach(function(d_){
              (d_.options || []).forEach(function(o){
                goals.push({key: o.key, label: o.label, metrics: o.metrics || null});
              });
            });
            goalCache[key] = goals;
            fillGoals(goals);
          }).catch(function(){});
      }
      function paintCategory(){
        document.getElementById('s-uncldesc').hidden = csel.value !== 'unclassified';
        var goals = goalCache[gsel.value] || [];
        var picked = goals.filter(function(g){ return g.key === csel.value; })[0];
        curMetrics = (picked && picked.metrics) || null;
        buildMetricFields();
        paintKind();
      }
      csel.addEventListener('change', paintCategory);

      // the game picker: type to find, nothing rendered until you type, and
      // the way out is always offered ("Add a new game")
      var gpick = document.getElementById('s-gamepick');
      var gsearch = document.getElementById('s-gamesearch');
      var glist = gpick.querySelector('.gamelist');
      var glocked = document.getElementById('s-gamelocked');
      var KEYS = Object.keys(G);
      function pickGame(key){
        gsel.value = key;
        gsearch.value = G[key];
        glist.hidden = true;
        loadGoals();
      }
      function fillGameList(){
        var q = gsearch.value.trim().toLowerCase();
        glist.innerHTML = '';
        if (q) {
          KEYS.filter(function(k){
            return G[k].toLowerCase().indexOf(q) >= 0 || k.indexOf(q) >= 0;
          }).slice(0, 12).forEach(function(k){
            var row = el('div', 'authopt', G[k]);
            row.addEventListener('mousedown', function(ev){ ev.preventDefault(); pickGame(k); });
            glist.appendChild(row);
          });
        }
        glist.hidden = false;
      }
      gsearch.addEventListener('input', fillGameList);
      gsearch.addEventListener('focus', fillGameList);
      gsearch.addEventListener('blur', function(){ setTimeout(function(){ glist.hidden = true; }, 150); });

      // arriving from a game page: the context comes along, locked
      var pre = null;
      try { pre = new URLSearchParams(location.search).get('game'); } catch (e) {}
      if (pre && G[pre]) {
        pickGame(pre);
        gpick.hidden = true;
        glocked.hidden = false;
        document.getElementById('s-gamelockname').textContent = G[pre];
        document.getElementById('s-gameunlock').addEventListener('click', function(ev){
          ev.preventDefault();
          glocked.hidden = true;
          gpick.hidden = false;
          gsearch.focus();
        });
      }
      fillGoals([]);

      initAuthorPick(sform.querySelector('.authpick'), [d.user]);

      // ROM picker: name + SHA1 computed locally, nothing uploaded
      var romFile = document.getElementById('s-romfile');
      romFile.addEventListener('change', function(){
        var f = romFile.files && romFile.files[0];
        if (!f) return;
        var note = document.getElementById('s-romnote');
        note.hidden = false;
        note.textContent = 'hashing …';
        f.arrayBuffer().then(function(buf){
          return crypto.subtle.digest('SHA-1', buf);
        }).then(function(hash){
          var hex = Array.prototype.map.call(new Uint8Array(hash), function(b){
            return b.toString(16).padStart(2, '0');
          }).join('');
          document.getElementById('s-romname').value = f.name;
          document.getElementById('s-romsha1').value = hex;
          note.textContent = '✓ ' + f.name + ' · sha1 ' + hex.slice(0, 12) +
                             '… (computed locally; the ROM was not uploaded)';
        }).catch(function(){
          note.textContent = 'could not hash the file; fill the fields manually';
        });
      });

      // live encode check: the thumbnail is derived from the encode, so the
      // link is validated as it is typed — preview frame + green check
      var encIn = document.getElementById('s-encode');
      var encBox = document.getElementById('enc-check');
      var encImg = document.getElementById('enc-thumb');
      var encSt = document.getElementById('enc-status');
      // by id, never by class: 'button.btn' picks the FIRST such button in
      // the form, which is Preview, so the encode check disabled Preview on
      // load and left Submit ungated (the same mistake broke double-submit)
      var sbtn = document.getElementById('s-submit');
      var encTimer = null;
      var ENC_HOSTS = 'ENCODE_HOSTS'.split('|');
      function knownHost(u){
        var m = /^https?:\/\/([^\/:?#]+)/i.exec((u || '').trim());
        if (!m) return false;
        return ENC_HOSTS.indexOf(m[1].toLowerCase().replace(/^www\./, '')) >= 0;
      }
      function checkEncode(){
        var url = encIn.value.trim();
        sbtn.disabled = true;
        encImg.hidden = true;
        if (!knownHost(url)) {
          encImg.removeAttribute('src');
          encBox.hidden = url === '';
          encSt.textContent = '✗ not a link from ENCODE_NAMES';
          encSt.className = 'enc-bad';
          return;
        }
        encBox.hidden = false;
        encSt.textContent = 'checking…';
        encSt.className = 'enc-wait';
        // the archivist resolves it: several platforms reveal their thumbnail
        // only through an API a browser is not allowed to call
        var asked = url;
        fetch(api + '/api/encode/check?url=' + encodeURIComponent(url))
          .then(function(r){ return r.json(); })
          .then(function(j){
            if (encIn.value.trim() !== asked) return;      // the field moved on
            if (!j.ok) {
              encSt.textContent = '✗ ' + (j.error || 'that link does not work');
              encSt.className = 'enc-bad';
              return;
            }
            sbtn.disabled = false;
            encSt.textContent = '✓ ' + j.name +
              ' encode verified; this frame becomes the run thumbnail';
            encSt.className = 'enc-good';
            if (j.thumb) {
              encImg.onerror = function(){ encImg.hidden = true; };
              encImg.onload = function(){ encImg.hidden = false; };
              encImg.src = j.thumb;
            }
          })
          .catch(function(){
            encSt.textContent = '✗ could not reach the archivist to check the link';
            encSt.className = 'enc-bad';
          });
      }
      encIn.addEventListener('input', function(){
        clearTimeout(encTimer);
        encTimer = setTimeout(checkEncode, 400);
      });
      checkEncode();

      // preview: approximate client-side rendering of the notes dialect
      function inlineMd(s){
        s = escH(s);
        s = s.split('%%%').join('<br>');
        s = s.replace(/__(.+?)__/g, '<b>$1</b>');
        s = s.replace(/''(.+?)''/g, '<em>$1</em>');
        s = s.replace(/\[(https?:[^\s|\]]+)\|([^\]]+)\]/g, '<a href="$1">$2</a>');
        s = s.replace(/\[(https?:[^\s\]]+)\]/g, '<a href="$1">$1</a>');
        return s;
      }
      function renderNotes(text){
        var out = [], inUl = false, inOl = false, inCode = false, inQuote = false,
            inTable = false, code = [];
        function closeAll(quoteToo){
          if (inUl) { out.push('</ul>'); inUl = false; }
          if (inOl) { out.push('</ol>'); inOl = false; }
          if (inTable) { out.push('</tbody></table></div>'); inTable = false; }
          // mirror the server renderer: any block start also ends an open
          // quote, or the preview drifts from the published page
          if (quoteToo !== false && inQuote) { out.push('</blockquote>'); inQuote = false; }
        }
        text.split(/\r?\n/).forEach(function(raw){
          var l = raw.replace(/\s+$/, ''), s = l.trim(), m;
          if (inCode) {
            if (s.toUpperCase().indexOf('%%END_EMBED') === 0) {
              out.push('<pre class="codebox"><code>' + escH(code.join('\n')) + '</code></pre>');
              code = []; inCode = false;
            } else code.push(l);
            return;
          }
          if (s.toUpperCase().indexOf('%%SRC_EMBED') === 0) { closeAll(); inCode = true; return; }
          if (s.toUpperCase().indexOf('%%QUOTE_END') === 0 || s.toUpperCase().indexOf('%%END_QUOTE') === 0) {
            closeAll(false);
            if (inQuote) { out.push('</blockquote>'); inQuote = false; } return;
          }
          if (s.toUpperCase().indexOf('%%QUOTE') === 0) {
            closeAll();
            var who = s.slice(7).trim();
            out.push('<blockquote class="wquote">' + (who ? '<p class="qwho">' + inlineMd(who) + ':</p>' : ''));
            inQuote = true; return;
          }
          if (s === '%%TOC%%') return;
          m = /^\[module:youtube\|v=([\w-]+)\]$/.exec(s);
          if (m) { closeAll(); out.push('<div class="notes-embed"><iframe src="https://www.youtube-nocookie.com/embed/' + m[1] + '" allowfullscreen loading="lazy"></iframe></div>'); return; }
          if (/^-{4,}$/.test(s)) { closeAll(); out.push('<hr>'); return; }
          m = /^(!{1,3})\s*(.*)/.exec(l);
          if (m) { closeAll(); var tag = m[1].length === 1 ? 'h4' : 'h3';
            out.push('<' + tag + '>' + inlineMd(m[2]) + '</' + tag + '>'); return; }
          if (s.indexOf('||') === 0 || (s.charAt(0) === '|' && s.charAt(s.length - 1) === '|' && s.length > 1)) {
            if (!inTable) { closeAll(); out.push('<div class="tblwrap"><table><tbody>'); inTable = true; }
            var hdr = s.indexOf('||') === 0;
            var cells = s.replace(/^\|+|\|+$/g, '').split(hdr ? '||' : '|');
            out.push('<tr>' + cells.map(function(c){
              return (hdr ? '<th>' : '<td>') + inlineMd(c.trim()) + (hdr ? '</th>' : '</td>');
            }).join('') + '</tr>');
            return;
          } else if (inTable) { out.push('</tbody></table></div>'); inTable = false; }
          m = /^\*+\s*(.*)/.exec(l);
          if (m) { if (inOl) { out.push('</ol>'); inOl = false; }
            if (!inUl) { out.push('<ul>'); inUl = true; }
            out.push('<li>' + inlineMd(m[1]) + '</li>'); return; }
          m = /^#+\s+(.*)/.exec(l);
          if (m) { if (inUl) { out.push('</ul>'); inUl = false; }
            if (!inOl) { out.push('<ol>'); inOl = true; }
            out.push('<li>' + inlineMd(m[1]) + '</li>'); return; }
          if (!s) { closeAll(); return; }
          if (inUl) { out.push('</ul>'); inUl = false; }
          if (inOl) { out.push('</ol>'); inOl = false; }
          out.push('<p>' + inlineMd(l) + '</p>');
        });
        if (inCode && code.length) out.push('<pre class="codebox"><code>' + escH(code.join('\n')) + '</code></pre>');
        closeAll();   // closes lists and tables first, then any open quote
        return out.join('\n');
      }
      document.getElementById('s-preview-btn').addEventListener('click', function(){
        var pv = document.getElementById('s-preview');
        pv.hidden = false;
        var gameLabel = G[gsel.value] || '';
        var goalLabel = csel.value === 'unclassified' ? 'Unclassified'
          : (csel.options[csel.selectedIndex] ? csel.options[csel.selectedIndex].text : '');
        document.getElementById('pv-title').textContent = gameLabel;
        document.getElementById('pv-chips').innerHTML =
          '<span class="chip">' + escH(goalLabel) + '</span><span class="chip pendchip">Pending</span>';
        document.getElementById('pv-authors').textContent =
          'by ' + (sform.querySelector('[name=authors]').value || '').split(',').join(', ');
        // the poster is whatever the live check already resolved and drew
        var poster = document.getElementById('pv-poster');
        if (!encImg.hidden && encImg.src) {
          document.getElementById('pv-thumb').src = encImg.src;
          poster.hidden = false;
        } else poster.hidden = true;
        document.getElementById('pv-notes').innerHTML =
          renderNotes(sform.querySelector('[name=notes]').value || '');
        pv.scrollIntoView({behavior: 'smooth', block: 'start'});
      });

      var submitting = false;
      sform.addEventListener('submit', function(ev){
        ev.preventDefault();
        // one archive per press: the button used to be picked by class, which
        // matched Preview, so a double click submitted the run twice
        if (submitting) return;
        submitting = true;
        var sbtn = document.getElementById('s-submit');
        if (sbtn) { sbtn.disabled = true; sbtn.textContent = 'Archiving…'; }
        note(msg, 'Archiving your run…', true);
        post('/api/submit', new FormData(sform), sbtn).then(function(res){
          submitting = false;
          if (sbtn) sbtn.textContent = 'Submit';
          if (res.ok && res.j.ok) {
            if (sbtn) sbtn.disabled = true;      // done: never offer it again
            sformDirty = false;                  // archived: nothing left to lose
            sform.hidden = true;
            note(msg, 'Archived as ' + res.j.id + '. Your run page appears after the next ' +
                      'rebuild (about a minute) at ../runs/' + res.j.id + '/' +
                      (res.j.forum ? '. Announced on the forum: ' + res.j.forum : ''), true);
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
    var timeCb = root.querySelector('.med-time');
    var out = root.querySelector('[name=metrics]');
    var rows = [];   // {time:true} or {label,type,better,unit}
    function serialize(){
      var arr = rows.map(function(r){
        if (r.time) return {key: 'time'};
        return {label: r.label.value.trim(), type: r.type.value,
                better: r.better.value,
                unit: r.type.value === 'number' && r.unit.value.trim()
                      ? r.unit.value.trim() : undefined};
      }).filter(function(m){ return m.key === 'time' || m.label; });
      out.value = arr.length ? JSON.stringify(arr) : '';
    }
    function paint(){
      rowsEl.innerHTML = '';
      rows.forEach(function(r, i){
        var div = el('div', 'mrow');
        if (r.time) {
          div.appendChild(el('span', 'mfixed', 'Real time (derived) — lower is better'));
        } else {
          div.appendChild(r.label); div.appendChild(r.type);
          div.appendChild(r.better); div.appendChild(r.unit);
          r.unit.hidden = r.type.value === 'time';
        }
        [['↑', -1], ['↓', 1]].forEach(function(mv){
          var b = el('button', 'btn quiet mmove', mv[0]);
          b.type = 'button';
          b.disabled = (i + mv[1] < 0) || (i + mv[1] >= rows.length);
          b.addEventListener('click', function(){
            rows.splice(i + mv[1], 0, rows.splice(i, 1)[0]);
            paint();
          });
          div.appendChild(b);
        });
        if (!r.time) {
          var rm = el('button', 'btn quiet mmove', '×');
          rm.type = 'button';
          rm.addEventListener('click', function(){ rows.splice(i, 1); paint(); });
          div.appendChild(rm);
        }
        rowsEl.appendChild(div);
      });
      addBtn.disabled = rows.length >= 4;
      timeCb.disabled = !timeCb.checked && rows.length >= 4;
      serialize();
    }
    function makeRow(def){
      var r = {
        label: el('input', 'mlabel'), type: document.createElement('select'),
        better: document.createElement('select'), unit: el('input', 'munit')
      };
      r.label.placeholder = 'Metric name, e.g. Score';
      [['number', 'number'], ['time', 'time (h:mm:ss)']].forEach(function(o){
        var op = document.createElement('option');
        op.value = o[0]; op.textContent = o[1]; r.type.appendChild(op);
      });
      [['higher', 'higher is better'], ['lower', 'lower is better']].forEach(function(o){
        var op = document.createElement('option');
        op.value = o[0]; op.textContent = o[1]; r.better.appendChild(op);
      });
      r.unit.placeholder = 'unit, e.g. pts';
      if (def) {
        r.label.value = def.label || '';
        r.type.value = def.type || 'number';
        r.better.value = def.better || 'lower';
        r.unit.value = def.unit || '';
      }
      [r.label, r.type, r.better, r.unit].forEach(function(inp){
        inp.addEventListener('input', serialize);
        inp.addEventListener('change', function(){ paint(); });
      });
      return r;
    }
    addBtn.addEventListener('click', function(){
      if (rows.length >= 4) return;
      rows.push(makeRow(null));
      paint();
    });
    timeCb.addEventListener('change', function(){
      if (timeCb.checked) { if (rows.length < 4) rows.push({time: true}); else timeCb.checked = false; }
      else rows = rows.filter(function(r){ return !r.time; });
      paint();
    });
    (initial || []).forEach(function(def){
      if (def.key === 'time') { rows.push({time: true}); timeCb.checked = true; }
      else rows.push(makeRow(def));
    });
    paint();
    return {value: function(){ return out.value; }};
  }
  function wireCreateForm(form, loginEl, msgEl, endpoint, done){
    mep.then(function(d){
      if (d.unreachable) { note(msgEl, 'The archivist is not reachable right now; try again later.', false); return; }
      if (!d.loggedIn) { loginEl.hidden = false; return; }
      form.hidden = false;
      initMetricsEd(form.querySelector('.metriced'));
      var busy = false;
      form.addEventListener('submit', function(ev){
        ev.preventDefault();
        if (busy) return;
        busy = true;
        var btn = form.querySelector('button.btn:not(.quiet):not(.mmove)');
        if (btn) btn.disabled = true;
        note(msgEl, 'Creating…', true);
        post(endpoint, new FormData(form), btn).then(function(res){
          busy = false;
          if (btn) btn.disabled = false;
          if (res.ok && res.j.ok) { form.hidden = true; done(res.j); }
          else note(msgEl, res.j.error || 'something went wrong', false);
        });
      });
    });
  }
  var cgform = document.getElementById('creategameform');
  if (cgform) {
    var cgmsg = document.getElementById('cg-msg');
    wireCreateForm(cgform, document.getElementById('cg-login'), cgmsg,
                   '/api/game/create', function(j){
      note(cgmsg, 'Created. The game page appears after the next rebuild (about a minute). ' +
                  'Submit the run now: ../submit/?game=' + j.game, true);
      var a = document.createElement('a');
      a.className = 'btn'; a.href = '../submit/?game=' + j.game;
      a.textContent = 'Submit a run to ' + j.game;
      cgmsg.appendChild(document.createElement('br'));
      cgmsg.appendChild(a);
    });
  }
  var ccform = document.getElementById('createcatform');
  if (ccform) {
    var ccG = JSON.parse(document.getElementById('ccgamedata').textContent);
    var ccKey = null;
    try { ccKey = new URLSearchParams(location.search).get('game'); } catch (e) {}
    if (!ccKey || !ccG[ccKey]) {
      document.getElementById('cc-nogame').hidden = false;
    } else {
      document.getElementById('cc-game').value = ccKey;
      document.getElementById('cc-gamename').textContent = ccG[ccKey];
      var ccmsg = document.getElementById('cc-msg');
      wireCreateForm(ccform, document.getElementById('cc-login'), ccmsg,
                     '/api/category/add', function(j){
        note(ccmsg, 'Created. Submit the run now: ../submit/?game=' + ccKey, true);
        var a = document.createElement('a');
        a.className = 'btn'; a.href = '../submit/?game=' + ccKey;
        a.textContent = 'Submit a run';
        ccmsg.appendChild(document.createElement('br'));
        ccmsg.appendChild(a);
      });
    }
  }

  // ---- claim page ----
  // ---- forum discussion, in place ----
  // The archivist proxies the topic so the browser talks to one origin, and
  // posts a reply as the logged-in member (never as the bot).
  var disc = document.getElementById('discussion');
  if (disc && api) {
    var dposts = document.getElementById('disc-posts');
    var dform = document.getElementById('disc-reply');
    var dlogin = document.getElementById('disc-login');
    var topicId = disc.dataset.topic;
    var renderPosts = function(d){
      if (!d.posts || !d.posts.length) {
        dposts.innerHTML = '<p class="emptynote">No posts yet. Be the first to say ' +
          'something about this run.</p>';
        return;
      }
      dposts.innerHTML = d.posts.map(function(p){
        return '<article class="dpost"><div class="dhead">' +
          (p.avatar ? '<img class="davatar" src="' + escH(p.avatar) + '" alt="" loading="lazy">' : '') +
          '<b>' + escH(p.user || '') + '</b>' +
          '<span class="actmeta">' + escH((p.date || '').replace('T', ' ')) + '</span>' +
          '<a class="actmeta" href="' + escH(d.url) + '/' + (p.number || 1) + '">#' +
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
          dposts.innerHTML = '<p class="emptynote">The discussion could not be loaded. ' +
            'Read it on <a href="' + escH(disc.dataset.url) + '">the forum</a>.</p>';
        });
    };
    loadDiscussion();
    mep.then(function(me){
      if (!me.loggedIn) { dlogin.hidden = false; return; }
      document.getElementById('disc-who').textContent = me.user;
      dform.hidden = false;
      dform.addEventListener('submit', function(ev){
        ev.preventDefault();
        var msg = document.getElementById('disc-msg');
        var btn = dform.querySelector('button');
        var fd = new FormData(dform);
        fd.append('topic', topicId);
        btn.disabled = true;
        note(msg, 'Posting…', true);
        post('/api/discussion/reply', fd, btn).then(function(res){
          btn.disabled = false;
          if (!res.ok) { note(msg, res.j.error || 'could not post', false); return; }
          note(msg, 'Posted. Thank you.', true);
          dform.querySelector('[name=body]').value = '';
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

  function bskyPostHtml(it, profileUrl, linkify, since){
    var p = it.post || {}, rec = p.record || {};
    var rkey = String(p.uri || '').split('/').pop();
    var url = profileUrl + '/post/' + rkey;
    var card = '';
    var em = p.embed || {};
    if (em.external && em.external.uri) {
      card = '<a class="bcard" href="' + escH(em.external.uri) + '" rel="noopener">'
        + (em.external.thumb ? '<img src="' + escH(em.external.thumb) + '" alt="" loading="lazy">' : '')
        + '<span><b>' + escH(em.external.title || em.external.uri) + '</b>'
        + escH((em.external.description || '').slice(0, 90)) + '</span></a>';
    } else if (em.images && em.images.length) {
      card = '<a class="bcard" href="' + escH(url) + '" rel="noopener">'
        + '<img src="' + escH(em.images[0].thumb) + '" alt="'
        + escH(em.images[0].alt || '') + '" loading="lazy">'
        + '<span><b>Image</b>view on Bluesky</span></a>';
    }
    return '<article class="bpost"><div class="btext">' + linkify(rec.text || '')
      + '</div>' + card + '<div class="bmeta"><a href="' + escH(url) + '" rel="noopener">'
      + since(rec.createdAt || p.indexedAt) + '</a>'
      + '<span>♥ ' + (p.likeCount || 0) + '</span>'
      + '<span>↻ ' + (p.repostCount || 0) + '</span></div></article>';
  }

  // ---- Bluesky feed in the News & Events column ----
  // The AT Protocol serves public posts as JSON to anybody (CORS open, no
  // token, no cookies), so the panel renders in our own markup instead of
  // handing the reader to a third-party widget.
  var bfeed = document.getElementById('bskyfeed');
  if (bfeed) {
    var handle = bfeed.dataset.handle || '';
    var profileUrl = 'https://bsky.app/profile/' + handle;
    var since = function(iso){
      var s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
      if (s < 3600) return Math.floor(s / 60) + 'm ago';
      if (s < 86400) return Math.floor(s / 3600) + 'h ago';
      if (s < 2592000) return Math.floor(s / 86400) + 'd ago';
      return new Date(iso).toISOString().slice(0, 10);
    };
    var linkify = function(text){
      return escH(text).replace(/(https?:\/\/[^\s<]+)/g,
        function(u){ return '<a href="' + u + '" rel="noopener">' + u + '</a>'; });
    };
    var bfail = function(){
      bfeed.innerHTML = '<p class="emptynote">Could not reach Bluesky just now. ' +
        'Read the latest at <a href="' + profileUrl + '">@' + escH(handle) + '</a>.</p>';
    };
    fetch('https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor='
          + encodeURIComponent(handle) + '&limit=10&filter=posts_no_replies')
      .then(function(r){ return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function(d){
        var items = (d && d.feed) || [];
        if (!items.length) {
          bfeed.innerHTML = '<p class="emptynote">No posts yet. Follow ' +
            '<a href="' + profileUrl + '">@' + escH(handle) + '</a> for announcements.</p>';
          return;
        }
        bfeed.innerHTML = items.map(function(it){
          return bskyPostHtml(it, profileUrl, linkify, since);
        }).join('');
      })
      .catch(bfail);
  }

  // ---- self-service TASVideos import ----
  // profile: reveal the owner-only "Import runs" button
  var sib = document.getElementById('selfimport');
  if (sib && api) {
    mep.then(function(me){
      if (me.loggedIn && me.user &&
          me.user.toLowerCase() === (sib.dataset.author || '').toLowerCase()) {
        sib.hidden = false;
      }
    });
  }
  // /import/ page: disclaimer -> auto-scan -> import with progress bar + log
  var impMsg = document.getElementById('imp-msg');
  if (impMsg && api) {
    var ctl = document.getElementById('imp-ctl');
    var scanline = document.getElementById('imp-scanline');
    var list = document.getElementById('imp-list');
    var runBtn = document.getElementById('imp-run');
    var prog = document.getElementById('imp-prog');
    var fill = document.getElementById('imp-fill');
    var count = document.getElementById('imp-count');
    var logBox = document.getElementById('imp-log');
    var titles = {};
    var NL = String.fromCharCode(10);
    function busy(btn, on){
      if (!btn) return;
      btn.disabled = on;
      btn.classList.toggle('busy', on);
    }
    function post(path, fd, btn){
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
    mep.then(function(me){
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
      post('/api/import/scan', undefined, scanBtn).then(function(d){
        if (!d.ok) { note(impMsg, d.error || 'scan failed', false); return; }
        impMsg.hidden = true;
        ctl.hidden = false;
        prog.hidden = true;
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
      var pick = chosen();
      if (!pick.length) return;
      busy(runBtn, true);
      boxes.forEach(function(b){ b.box.disabled = true; });
      prog.hidden = false;
      var total = pick.length;
      var done = 0, skips = 0;
      count.textContent = '0 / ' + total;
      var fd = new FormData();
      fd.append('select', pick.join(' '));
      function step(){
        post('/api/import/run', fd).then(function(r){
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
          count.textContent = Math.min(done + skips, total) + ' / ' + total;
          fill.style.width = Math.round(100 * Math.min(done + skips, total) / total) + '%';
          if (r.remaining > 0 && r.imported.length) { step(); return; }
          logLine('');
          logLine('Done: ' + done + ' imported' + (skips ? ', ' + skips + ' need attention (see above)' : '') + '.');
          logLine('The site rebuilds from the archive; your runs appear in a few minutes.');
          runBtn.hidden = true;
          busy(runBtn, false);
        });
      }
      step();
    });
  }

})();
