// toolAssisted.run — the submit page (also /submit/?edit=M1234, the
// same form editing a run in place): the author picker, the movie
// inspector, the stated-metrics fields, the preview, and the archive
// (or save) itself. Moved out of app.js: these ids exist only here.
import { api, rel, versionQuery, mePromise, escapeHtml, el, setMark, waitBuilt,
  fileRowsOf, note, noteHtml, runPageUrl, post, setLastBtn,
  armMultiPick, searchArchive } from './app.js';

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

  // ---- submit page ----
  var submitForm = document.getElementById('submitform');
  // the same page edits or moves a run: /submit/?edit=M1234 or ?move=M1234
  var editRunId = null;
  var moveRunId = null;
  try {
    var _params = new URLSearchParams(location.search);
    var _e = _params.get('edit'), _m = _params.get('move');
    if (_e && /^M\d+$/.test(_e)) editRunId = _e;
    if (!editRunId && _m && /^M\d+$/.test(_m)) moveRunId = _m;
  } catch (e) {}

  function enterMoveMode(gameData, form){
    var msg = document.getElementById('s-msg');
    var title = document.getElementById('s-title');
    var subtitle = document.getElementById('s-subtitle');
    var back = document.getElementById('s-editback');
    var backLink = document.getElementById('s-editback-link');
    var gamePanel = document.getElementById('p-game');
    var agreePanel = document.getElementById('p-agree');
    var submitBtn = document.getElementById('s-submit');
    title.textContent = 'Move the run to a different game';
    subtitle.textContent = 'Loading ' + moveRunId + '\u2026';
    document.getElementById('s-policy').hidden = true;
    document.getElementById('s-moverefresh').hidden = false;
    back.hidden = false;
    backLink.href = '../runs/' + moveRunId + '/';
    ['p-run', 'p-repro', 'p-score', 'p-notes'].forEach(function(id){
      var panel = document.getElementById(id);
      panel.hidden = true;
      panel.querySelectorAll('input, select, textarea, button').forEach(function(control){
        control.disabled = true;
      });
    });
    document.getElementById('s-draftbar').hidden = true;
    document.getElementById('s-uncldesc').hidden = true;
    document.getElementById('p-game-title').innerHTML =
      '<span class="pnum">1</span> Game and category';
    agreePanel.dataset.step = '2';
    document.getElementById('p-agree-title').innerHTML =
      '<span class="pnum">2</span> Agreement';
    document.getElementById('p-agree-waits').textContent =
      'After you pick a different game and its category.';
    var consent = form.querySelector('[name=consent]');
    var consentLabel = consent.closest('label');
    if (consentLabel) consentLabel.hidden = true;
    consent.checked = true;
    var why = form.querySelector('[name=reason]');
    document.getElementById('s-why').hidden = false;
    why.required = true;
    submitBtn.textContent = 'Move';
    submitBtn.disabled = true;

    fetch(api + '/api/run/record?run=' + encodeURIComponent(moveRunId),
          {credentials: 'include'})
      .then(function(response){ return response.json(); })
      .then(function(record){
        if (!record.ok) {
          subtitle.textContent = record.error || 'could not load the run';
          form.hidden = true;
          return;
        }
        if (!record.may.expert) {
          subtitle.textContent = 'Only an expert covering ' + record.game.title +
            ' may move this run to a different game.';
          form.hidden = true;
          return;
        }
        var run = record.run;
        subtitle.textContent = moveRunId + ' \u00b7 ' + record.game.title + ' \u00b7 by ' +
          run.authors.map(function(author){ return author.user; }).join(', ') +
          ' \u00b7 expert mode';

        var gameTitles = gameData.games;
        var gameKeys = Object.keys(gameTitles);
        var gameField = document.getElementById('s-game');
        var gameSearch = document.getElementById('s-gamesearch');
        var gameList = document.getElementById('s-gamelist');
        var goal = document.getElementById('s-goal');
        var sub = document.getElementById('s-sub');
        var subWrap = document.getElementById('s-subwrap');
        var createCategory = document.getElementById('s-createcat');
        var goalCache = {};

        function destinationReady(){
          return !!gameField.value && gameField.value !== record.game.key &&
            !!goal.value && (subWrap.hidden || !!sub.value);
        }
        function paint(){
          var ready = destinationReady();
          gamePanel.classList.toggle('done', ready);
          agreePanel.classList.toggle('folded', !ready);
          agreePanel.classList.toggle('done', ready && why.value.trim().length >= 8);
          submitBtn.disabled = !(ready && why.value.trim().length >= 8);
        }
        function fillGoals(items){
          goal.innerHTML = '';
          (items || []).forEach(function(item){
            var option = document.createElement('option');
            option.value = item.key;
            option.textContent = item.label;
            goal.appendChild(option);
          });
          var uncl = document.createElement('option');
          uncl.value = 'unclassified';
          uncl.textContent = 'Unclassified (no goal; ranked by likes)';
          goal.appendChild(uncl);
          paintCategory();
        }
        function paintCategory(){
          var picked = (goalCache[gameField.value] || []).filter(function(item){
            return item.key === goal.value;
          })[0];
          var subs = (picked && picked.subcategories) || [];
          sub.innerHTML = '';
          subs.forEach(function(item){
            var option = document.createElement('option');
            option.value = item.key;
            option.textContent = item.label;
            sub.appendChild(option);
          });
          subWrap.hidden = !subs.length;
          sub.disabled = !subs.length;
          paint();
        }
        function loadGoals(){
          createCategory.href = '../create-category/?game=' +
            encodeURIComponent(gameField.value);
          createCategory.removeAttribute('aria-disabled');
          var key = gameField.value;
          goal.innerHTML = '';
          subWrap.hidden = true;
          fetch(api + '/api/categories?game=' + encodeURIComponent(key))
            .then(function(response){
              if (response.ok) return response.json();
              return fetch(gameData.raw + '/games/' + key + '/categories.json')
                .then(function(raw){ return raw.ok ? raw.json() : null; });
            })
            .then(function(categories){
              if (!categories || gameField.value !== key) return;
              var items = [];
              (categories.dimensions || []).forEach(function(dimension){
                (dimension.options || []).forEach(function(option){
                  items.push({key: option.key, label: option.label,
                              subcategories: option.subcategories || []});
                });
              });
              goalCache[key] = items;
              fillGoals(items);
            })
            .catch(function(){ note(msg, 'Could not load that game\u2019s categories.', false); });
        }
        function pickGame(key){
          gameField.value = key;
          gameSearch.value = gameTitles[key];
          gameList.hidden = true;
          loadGoals();
        }
        function fillGameList(){
          var query = gameSearch.value.trim().toLowerCase();
          gameList.innerHTML = '';
          gameKeys.filter(function(key){
            return key !== record.game.key && query &&
              (gameTitles[key].toLowerCase().indexOf(query) >= 0 ||
               key.indexOf(query) >= 0);
          }).slice(0, 12).forEach(function(key){
            var row = el('div', 'authopt', gameTitles[key]);
            row.addEventListener('mousedown', function(event){
              event.preventDefault();
              pickGame(key);
            });
            gameList.appendChild(row);
          });
          gameList.hidden = false;
        }
        gameSearch.addEventListener('input', function(){
          gameField.value = '';
          fillGameList();
          paint();
        });
        gameSearch.addEventListener('focus', fillGameList);
        gameSearch.addEventListener('blur', function(){
          setTimeout(function(){ gameList.hidden = true; }, 150);
        });
        goal.addEventListener('change', paintCategory);
        sub.addEventListener('change', paint);
        why.addEventListener('input', paint);
        fillGoals([]);

        form.addEventListener('submit', function(event){
          event.preventDefault();
          if (submitBtn.disabled) return;
          var fd = new FormData();
          fd.append('run', moveRunId);
          fd.append('game', gameField.value);
          fd.append('goal', goal.value);
          if (!subWrap.hidden) fd.append('sub', sub.value);
          fd.append('reason', why.value.trim());
          post('/api/run/move', fd, submitBtn).then(function(result){
            if (!result.ok || !result.j.ok) {
              note(msg, result.j.error || 'something went wrong', false);
              return;
            }
            setMark(submitBtn, 'spin', 'Moved. Publishing to the site\u2026');
            waitBuilt(result.j.serial, function(){
              form.hidden = true;
              location.href = runPageUrl(moveRunId);
            });
          });
        });
        form.hidden = false;
        paint();
      })
      .catch(function(){
        subtitle.textContent = 'could not reach the archivist';
        form.hidden = true;
      });
  }

  if (submitForm) {
    var gameData = JSON.parse(document.getElementById('gamedata').textContent);
    var gameTitles = gameData.games;
    mePromise.then(function(d){
      var msg = document.getElementById('s-msg');
      if (d.unreachable) { note(msg, 'The archivist is not reachable right now; try again later.', false); return; }
      if (!d.loggedIn) { document.getElementById('s-login').hidden = false; return; }
      if (moveRunId) { enterMoveMode(gameData, submitForm); return; }
      submitForm.hidden = false;
      // half-written submissions are easy to lose to a stray click: once
      // anything in the form changes, leaving asks the standard are-you-sure
      var submitFormDirty = false;
      // Avoid warning when the first step has no meaningful edits.
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
      var movieLabel = document.getElementById('s-movielabel');
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
        setTime(sec);
      };
      // A time reaches the picker in whole milliseconds, because that is all
      // the picker can say: the record's own duration, an import from the
      // movie, an import from the encode, a restored draft. Rounding the
      // total (rather than the fraction on its own) keeps a value like
      // 12.9996 s out of an impossible ".1000" the archivist would reject.
      function setTime(sec){
        var ms = Math.round(sec * 1000);
        byIdS('t-h').value = Math.floor(ms / 3600000) || '';
        byIdS('t-m').value = Math.floor(ms / 60000) % 60;
        byIdS('t-s').value = Math.floor(ms / 1000) % 60;
        byIdS('t-ms').value = ms % 1000;
      }
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
      var recordSeconds = null;   // from /api/run/record in edit mode (backend-computed)
      var recordSecondsSource = null;
      function importSources(){
        return {movie: (movieInfo && movieInfo.parsed && movieInfo.seconds) || null,
                encode: encodeSeconds || null,
                record: recordSeconds || null};
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
            var label = o.value === 'movie' ? 'the movie file'
                      : o.value === 'encode' ? 'the video encode'
                      : recordSecondsSource === 'movie' ? 'the archived movie'
                      : 'the archived record';
            o.textContent = label + (sec ? ' · ' + secClock(sec) : '');
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
        setTime(sec);
        composeTime();
        paintPanels();
      });
      function paintKind(){
        // In edit mode authors and covering experts may replace the movie. Existing
        // reproductions remain historical but become obsolete because they synced
        // the old file. A run with NO movie may also receive its first file.
        var hasMovie = !!(editRecord && editRecord.run && editRecord.run.movie);
        var mayReplace = !!(editMay && (editMay.expert || editMay.author));
        movieWrap.hidden = !!(editRunId && (hasMovie ? !mayReplace
                                                     : !(mayReplace || (editMay && editMay.author))));
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
        // Segment inputs compose themselves on input. Do not compose here:
        // category loading can run after edit mode restores the archived time,
        // and an empty set of segments would otherwise erase that value.
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
        const rowsBox = submitForm.querySelector('.filerows');
        var names = [].concat(fields.file_name || []), shas = [].concat(fields.file_sha1 || []);
        if (rowsBox && names.length) {
          rowsBox.querySelectorAll('.filerow').forEach(function(r){ r.remove(); });
          names.forEach(function(){ rowsBox.querySelector('.addfile').click(); });
        }
        var seen = {};
        Array.prototype.forEach.call(submitForm.elements, function(e){
          if (!e.name || !(e.name in fields) || e.type === 'file') return;
          let val = fields[e.name];
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
      function enterEditMode(){
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
            if (!mayEdit) {
              byIdS('s-subtitle').textContent = 'Only the authors of ' + editRunId + ', the experts covering ' + j.game.title + ' and editors may edit it.'; submitForm.hidden = true;
              return;
            }
            byIdS('s-subtitle').textContent = editRunId + ' · ' + j.game.title + ' · by ' + run.authors.map(function(a){ return a.user; }).join(', ') + (editMay.author ? '' : ' · expert mode');
            // panel 1: the game is fixed; category and subcategory only for experts and editors
            var expertish = editMay.expert || editMay.editor;
            gameTitles[j.game.key] = gameTitles[j.game.key] || j.game.title;
            pendingDraft = {game: j.game.key, goal: run.category.goal, sub: run.category.sub || '', fields: {}};
            pickGame(j.game.key);
            // A run filed under the wrong game is moved rather than resubmitted,
            // and the game is picked here like it was at submission. It carries
            // its own consequence: another game means another category, and a
            // category change invalidates the verifications, which judged the
            // old goal. Everyone else sees the game and cannot change it.
            document.querySelectorAll('#p-game .createq').forEach(function(e){ e.hidden = true; });
            // moving a run between games is an EXPERT's: an editor shapes the
            // library and may move it between categories, but member work
            // moves under an expert's name
            if (!editMay.expert) {
              gamePick.hidden = true; gameLocked.hidden = false;
              byIdS('s-gamelockname').textContent = j.game.title;
              gameLocked.innerHTML = 'Game: <b>' + j.game.title + '</b> '
                + '(an expert covering both games moves a run to another one)';
              if (systemSelect) systemSelect.disabled = true;
            }
            if (!expertish) { goalSelect.disabled = true; subSelect.disabled = true; }
            // panel 2 and on: the record
            submitForm.querySelector('[name=encode]').value = ((run.encodes || [])[0] || {}).url || '';
            submitForm.querySelector('[name=encode]').dispatchEvent(new Event('input'));
            var pick = submitForm.querySelector('.authpick');
            if (pick && pick.setAuthors) pick.setAuthors(run.authors.map(function(a){ return a.user; }));
            if (!editMay.author && !editMay.expert) { pick.querySelector('.authsearch').disabled = true; pick.querySelectorAll('.authx').forEach(function(x){ x.disabled = true; }); }
            submitForm.querySelector('[name=completed]').value = run.completed || '';

            // the designated "You may also like" picks: search-as-you-type
            byIdS('s-relatedwrap').hidden = false;
            var relatedSeen = [];
            var relatedList = document.getElementById('s-relatedlist');
            var relatedPick = armMultiPick(submitForm, 'related', 's-relatedlist',
                function(){ return relatedSeen; },
                function(q){ return searchArchive('runs', q).then(function(items){
                  relatedList.innerHTML = '';
                  items.forEach(function(it){
                    if (it.value === editRunId) return;
                    if (relatedSeen.indexOf(it.value) < 0) relatedSeen.push(it.value);
                    var o = document.createElement('option');
                    o.value = it.value;
                    o.label = it.item ? it.item.title : it.value;
                    relatedList.appendChild(o);
                  });
                }); });
            (run.related || []).forEach(function(v){ if (relatedSeen.indexOf(v) < 0) relatedSeen.push(v); });
            if (relatedPick) relatedPick.set(run.related || []);

            // the movie file stays unless an author or covering expert replaces it
            movieInput.required = false;
            if (!run.movie) {
              byIdS('s-movielabel').textContent = 'The movie file, if this run has one after '
                + 'all (optional; adding it stops the run being video-only)';
            } else if (expertish) {
              byIdS('s-movielabel').textContent = 'Replace the movie file (optional; the current one stays unless you pick another)';
            }
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
            // pre-fill notes from backend raw text (the stored markup, not
            // any rendered form); done before paintPanels opens the panels
            var notesArea = submitForm.querySelector('[name=notes]');
            if (notesArea) {
              notesArea.value = j.notes || '';
              notesArea.dispatchEvent(new Event('input', {bubbles: true}));
            }
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
            editStatedTime(run, j.seconds);
            // make the archived time importable from the dropdown too
            if (j.seconds && timeImportSel) {
              recordSeconds = j.seconds;
              recordSecondsSource = j.secondsSource || null;
              if (!timeImportSel.querySelector('[value=record]')) {
                var recOpt = document.createElement('option');
                recOpt.value = 'record';
                timeImportSel.appendChild(recOpt);
              }
            }
            paintKind(); paintPanels();
          }).catch(function(){ byIdS('s-subtitle').textContent = 'could not reach the archivist'; });
      }
      function editStatedTime(run, sec){
        // prefer the backend-computed seconds (includes system-fps fallback
        // for legacy runs that never stored fps in their movie metadata);
        // fall back to the run's own stated duration or frames-derived value
        if (!sec && run.duration) sec = run.duration;
        if (!sec && run.movie && run.movie.frames && run.movie.fps) sec = run.movie.frames / run.movie.fps;
        if (!sec) return;
        setTime(sec);
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
          return !(goalSelect.value === 'unclassified'
              && !submitForm.querySelector('[name=goal_description]').value.trim());
        }
        if (step === 2) {
          return document.getElementById('enc-status').className === 'enc-good'
            && !!submitForm.querySelector('[name=authors]').value;
        }
        if (step === 3) {
          // Everything here is optional, and an unreadable file is a warning
          // and never a refusal: the archive keeps it exactly as it is. So
          // this waits for the archivist to have LOOKED at the movie, not for
          // it to have parsed every detail. An unreadable movie is retained as
          // submitted, while a readable one can supply checked timing data.
          if (movieInput.files && movieInput.files[0]) return movieInfo !== null;
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

      // ---- the system comes first, and narrows the games below it ----
      // A game key IS system/slug, so the filter needs nothing the page does
      // not already carry. Empty means every system, which is what somebody
      // who knows the game's name wants.
      var systemNames = {};
      try { systemNames = JSON.parse(document.getElementById('systemdata').textContent); }
      catch (e) { systemNames = {}; }
      var systemSelect = document.getElementById('s-system');
      function fillSystems(selected){
        if (!systemSelect) return;
        var keys = Object.keys(systemNames).sort(function(a, b){
          return systemNames[a].toLowerCase() < systemNames[b].toLowerCase() ? -1 : 1;
        });
        systemSelect.innerHTML = '';
        var every = document.createElement('option');
        every.value = ''; every.textContent = 'Every system';
        systemSelect.appendChild(every);
        keys.forEach(function(k){
          var o = document.createElement('option');
          o.value = k; o.textContent = systemNames[k];
          systemSelect.appendChild(o);
        });
        systemSelect.value = selected || '';
      }
      fillSystems('');
      function systemOf(key){ return String(key).split('/')[0]; }
      function pickGame(key){
        gameSelect.value = key;
        gameSearch.value = gameTitles[key];
        gameList.hidden = true;
        if (systemSelect && gameTitles[key]) systemSelect.value = systemOf(key);
        loadGoals();
      }
      function fillGameList(){
        var q = gameSearch.value.trim().toLowerCase();
        var sys = systemSelect ? systemSelect.value : '';
        gameList.innerHTML = '';
        var pool = gameKeys.filter(function(k){ return !sys || systemOf(k) === sys; });
        // with a system chosen the list is worth showing unprompted: it is
        // short, and it is the answer to "what is already here?"
        var rows = (q ? pool.filter(function(k){
          return gameTitles[k].toLowerCase().indexOf(q) >= 0 || k.indexOf(q) >= 0;
        }) : (sys ? pool : []));
        rows.slice(0, 12).forEach(function(k){
          var row = el('div', 'authopt', gameTitles[k]);
          row.addEventListener('mousedown', function(ev){ ev.preventDefault(); pickGame(k); });
          gameList.appendChild(row);
        });
        if (sys && !rows.length) {
          gameList.appendChild(el('div', 'authopt authnone',
            q ? 'no game of that name on this system yet'
              : 'no games on this system yet: create the game first'));
        }
        gameList.hidden = false;
      }
      if (systemSelect) systemSelect.addEventListener('change', function(){
        // a game from another system is no longer the one being submitted
        if (gameSelect.value && systemSelect.value
            && systemOf(gameSelect.value) !== systemSelect.value) {
          gameSelect.value = '';
          gameSearch.value = '';
          fillGoals([]);
        }
        paintPanels();
      });

      // ---- a machine nobody has listed yet, added from here ----
      var addSystemBtn = document.getElementById('s-addsystem');
      if (addSystemBtn) {
        var newBox = document.getElementById('s-newsystem');
        var newName = document.getElementById('s-newsystem-name');
        var newKey = document.getElementById('s-newsystem-key');
        var newMsg = document.getElementById('s-newsystem-msg');
        var newGo = document.getElementById('s-newsystem-go');
        function keyFor(name){
          return name.toLowerCase().replace(/[^a-z0-9]+/g, '-')
                     .replace(/^-+|-+$/g, '').slice(0, 24).replace(/-+$/, '');
        }
        addSystemBtn.addEventListener('click', function(){
          newBox.hidden = !newBox.hidden;
          if (!newBox.hidden) newName.focus();
        });
        newName.addEventListener('input', function(){
          newKey.textContent = keyFor(newName.value) || 'the-system-name';
        });
        newGo.addEventListener('click', function(){
          var name = newName.value.trim();
          if (name.length < 2) { note(newMsg, 'the name, as people write it', false); return; }
          var fd = new FormData();
          fd.append('name', name);
          post('/api/system/create', fd, newGo).then(function(res){
            if (res.ok && res.j.ok) {
              systemNames[res.j.key] = res.j.system.name;
              fillSystems(res.j.key);
              note(newMsg, res.j.system.name + ' is a system here now, as ' + res.j.key +
                           '. Create the game on it, then come back.', true);
              newName.value = '';
              fillGameList();
              paintPanels();
            } else note(newMsg, res.j.error || 'something went wrong', false);
          });
        });
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
      if (editRunId) setTimeout(function(){ enterEditMode(); }, 0);   // once every handler below is wired

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
      // ---- saving an edit: the revision through /api/edit; category moves
      // remain their own expert-only logged edits ----
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
          if (pickedMovie && (editMay.author || editMay.expert)) fd.append('movie', pickedMovie);
          var att = submitForm.querySelector('[name=attachments]');
          if (editMay.author && att.files) Array.prototype.forEach.call(att.files, function(f){ fd.append('attachments', f); });
          var why = submitForm.querySelector('[name=reason]').value.trim();
          if (why) fd.append('reason', why);
          return fd;
        }
        var pickedMovie = movieInput.files && movieInput.files[0];
        // Authors and covering experts submit movie additions or replacements through
        // the ordinary revision; editors remain limited to category moves.
        var newGoal = expertish ? goalSelect.value + (subWrap.hidden ? '' : '/' + subSelect.value) : null;
        var oldGoal = run.category.goal + (run.category.sub ? '/' + run.category.sub : '');
        // another GAME is a relocation, not a field edit: the run folder moves
        // and its category is chosen from the new game's own. It carries the
        // category change with it, so it voids what a category change voids.
        var moveGame = editMay.expert && gameSelect.value && gameSelect.value !== run.game
                       ? gameSelect.value : null;
        var moveGoal = !moveGame && newGoal && newGoal !== oldGoal ? newGoal : null;
        var liveVerifications = (run.verifications || []).filter(function(v){ return !v.invalidated; }).length;
        // an editor may only move the run: /api/edit is not theirs, so the
        // revision step is skipped and the move goes straight to expert/edit
        var mayRevise = editMay.author || editMay.expert;
        var dry = mayRevise ? post('/api/edit', revision(true), submitBtn)
                            : Promise.resolve({ok: false, j: {error: 'nothing to change'}});
        dry.then(function(res){
          var wv = (res.ok && res.j.ok) ? (res.j.would_void || []) : [];
          var nothing = !res.ok && /nothing to change/.test(res.j.error || '');
          if (!(res.ok && res.j.ok) && !nothing) { note(msg, res.j.error || 'something went wrong', false); return; }
          if ((moveGame || moveGoal) && liveVerifications) wv = wv.concat(['verifications']);
          var text = '';
          if (wv.indexOf('verifications') >= 0) text = 'This run is verified. Changing its scoring invalidates the verifications: it leaves the ranking until somebody verifies it again.';
          if (wv.indexOf('reproductions') >= 0) text += (text ? ' ' : '') + 'Changing its reproduction information invalidates the reproductions: they synced the old setup.';
          if (wv.indexOf('consoleVerifications') >= 0) text += (text ? ' ' : '') + 'Its console verifications are invalidated too.';
          if (text && !window.confirm(text + ' Save anyway?')) { setMark(submitBtn, '', ''); return; }
          submitting = true;
          var steps = [];
          if (!nothing) steps.push(function(){ return post('/api/edit', revision(false), submitBtn); });
          if (moveGame) steps.push(function(){
            var fd = new FormData();
            fd.append('run', editRunId); fd.append('game', moveGame);
            fd.append('goal', goalSelect.value);
            if (!subWrap.hidden && subSelect.value) fd.append('sub', subSelect.value);
            fd.append('reason', submitForm.querySelector('[name=reason]').value.trim()
                                || 'Moved to the game it belongs to.');
            return post('/api/run/move', fd, submitBtn);
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
              setLastBtn(submitBtn);
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

export const page = 'submit';
window.TARApp.page = page;
