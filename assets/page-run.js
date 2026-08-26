// toolAssisted.run — the run page: the act zone (withdraw, reproduce,
// verify, console-verify, move category, invalidate, reports), likes,
// the 18+ gate, and the in-place forum discussion. Moved out of app.js:
// these ids exist only on a run page.
import { api, mePromise, viewAsCoverage, el, fileRowsOf, note, noteBuilt,
  actionBtn, post, escapeHtml } from './app.js';

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

export const page = 'run';
window.TARApp.page = page;
