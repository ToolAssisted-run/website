// toolAssisted.run — the self-service TASVideos import page: scan the
// backup snapshot for a claimed member's unarchived publications, pick,
// import with a progress bar and log. Moved out of app.js: these ids
// exist only on /import/.
import { api, mePromise, el, note, waitBuilt, busy } from './app.js';

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

export const page = 'import';
window.TARApp.page = page;
