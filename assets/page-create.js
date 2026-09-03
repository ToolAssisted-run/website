// toolAssisted.run — the create-game and create-category pages: a
// shared gated-form wiring (login gate, one press at a time) around
// the metrics editor app.js exports. Moved out of app.js: these ids
// exist only here.
import { api, rel, mePromise, note, waitBuilt, post, initMetricsEd }
  from './app.js';

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

      // Preview button render logic for rules
      var ccRuleIn = createCatForm.querySelector('[name=rule]');
      var ccPvBtn = document.getElementById('cc-preview-btn');
      var ccPvBox = document.getElementById('cc-preview');
      var ccPvContent = document.getElementById('cc-preview-content');
      if (ccPvBtn && ccPvBox && ccPvContent) {
        ccPvBtn.addEventListener('click', function(){
          if (ccPvBox.hidden) {
            ccPvBox.hidden = false;
            ccPvContent.textContent = 'Rendering…';
            var fd = new FormData();
            fd.append('notes', ccRuleIn.value || '');
            fd.append('kind', 'rules');
            post('/api/preview', fd, ccPvBtn).then(function(res){
              ccPvContent.innerHTML = (res.ok && res.j.ok) ? res.j.html : ((res.j && res.j.error) || 'preview failed');
            }).catch(function(){ ccPvContent.textContent = 'The archivist is not reachable; the preview needs it.'; });
          } else {
            ccPvBox.hidden = true;
          }
        });
      }
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

export const page = 'create';
window.TARApp.page = page;
