// toolAssisted.run — the Founder, Steering Committee and expert panels
// (one module: the three pages share the open-claims board and the
// type-to-find pickers). Moved out of app.js: these ids exist only here.
import { api, rel, mePromise, viewAsActive, el, note, noteBuilt, actionBtn,
  post, searchArchive, armPicker, armMultiPick } from './app.js';

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

export const page = 'panels';
window.TARApp.page = page;
