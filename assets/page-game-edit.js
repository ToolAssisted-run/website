// toolAssisted.run — the game editor (covering experts and editors):
// one local draft, one Save that replays the differences as an ordered
// sequence of logged edits. Moved out of app.js: these ids exist only
// on a game's /edit/ page.
import { api, mePromise, viewAsCoverage, el, note, noteBuilt, post,
  initMetricsEd, setLastBtn } from './app.js';

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

      // Client image cropper logic START
      var thumbInput = byId('ge-thumb');
      var thumbPreview = byId('ge-thumb-preview');
      var thumbImage = byId('ge-thumb-image');
      var cropPanel = byId('ge-crop');
      var cropStage = byId('ge-crop-stage');
      var cropImage = byId('ge-crop-image');
      var cropBox = byId('ge-crop-box');
      var cropSize = byId('ge-crop-size');
      var cropStatus = byId('ge-thumb-status');
      var thumbSource = null, thumbObjectUrl = null, preparedThumb = null;
      var cropGeom = null, cropDragging = false, cropDragX = 0, cropDragY = 0;

      function cropCanvasBlob(source, sx, sy, sw, sh){
        return new Promise(function(resolve, reject){
          var canvas = document.createElement('canvas');
          var maxWidth = 1280;
          var width = Math.min(maxWidth, sw);
          var height = Math.round(width * 9 / 16);
          canvas.width = width; canvas.height = height;
          var ctx = canvas.getContext('2d');
          if (!ctx) { reject(new Error('thumbnail crop is not supported by this browser')); return; }
          ctx.fillStyle = '#000000'; ctx.fillRect(0, 0, width, height);
          ctx.drawImage(source, sx, sy, sw, sh, 0, 0, width, height);
          canvas.toBlob(function(blob){
            if (blob) resolve(blob);
            else reject(new Error('could not prepare the thumbnail'));
          }, 'image/jpeg', .86);
        });
      }
      function imageFrame(){
        if (!thumbSource || !cropStage) return null;
        var stageW = cropStage.clientWidth, stageH = cropStage.clientHeight;
        var ratio = thumbSource.naturalWidth / thumbSource.naturalHeight;
        var imageW = ratio >= stageW / stageH ? stageW : stageH * ratio;
        var imageH = imageW / ratio;
        return {left: (stageW - imageW) / 2, top: (stageH - imageH) / 2,
                width: imageW, height: imageH};
      }
      function paintCrop(){
        if (!cropGeom) return;
        cropBox.style.left = cropGeom.x + 'px'; cropBox.style.top = cropGeom.y + 'px';
        cropBox.style.width = cropGeom.w + 'px'; cropBox.style.height = cropGeom.h + 'px';
      }
      function layoutCrop(){
        var frame = imageFrame();
        if (!frame) return;
        var maxW = Math.min(frame.width, frame.height * 16 / 9);
        var scale = Number(cropSize.value) / 100;
        var w = maxW * scale, h = w * 9 / 16;
        cropImage.style.left = frame.left + 'px'; cropImage.style.top = frame.top + 'px';
        cropImage.style.width = frame.width + 'px'; cropImage.style.height = frame.height + 'px';
        cropGeom = {frame: frame, x: frame.left + (frame.width - w) / 2,
                    y: frame.top + (frame.height - h) / 2, w: w, h: h};
        paintCrop();
      }
      function clampCrop(){
        if (!cropGeom) return;
        var f = cropGeom.frame;
        cropGeom.x = Math.max(f.left, Math.min(cropGeom.x, f.left + f.width - cropGeom.w));
        cropGeom.y = Math.max(f.top, Math.min(cropGeom.y, f.top + f.height - cropGeom.h));
        paintCrop();
      }
      function openCrop(){
        if (!thumbSource) return;
        cropPanel.hidden = false;
        cropImage.onload = layoutCrop;
        cropImage.src = thumbSource.src;
        if (cropImage.complete) layoutCrop();
        cropPanel.scrollIntoView({behavior: 'smooth', block: 'nearest'});
      }
      function closeCrop(){ cropPanel.hidden = true; cropDragging = false; }
      function cropResult(){
        var f = cropGeom && cropGeom.frame;
        if (!f) return Promise.reject(new Error('choose an image before cropping'));
        return cropCanvasBlob(thumbSource,
          (cropGeom.x - f.left) * thumbSource.naturalWidth / f.width,
          (cropGeom.y - f.top) * thumbSource.naturalHeight / f.height,
          cropGeom.w * thumbSource.naturalWidth / f.width,
          cropGeom.h * thumbSource.naturalHeight / f.height);
      }
      function autoCrop(){
        var ratio = thumbSource.naturalWidth / thumbSource.naturalHeight;
        var sw = ratio >= 16 / 9 ? thumbSource.naturalHeight * 16 / 9 : thumbSource.naturalWidth;
        var sh = sw * 9 / 16;
        return cropCanvasBlob(thumbSource, (thumbSource.naturalWidth - sw) / 2,
                              (thumbSource.naturalHeight - sh) / 2, sw, sh);
      }
      function prepareThumbnail(){
        if (preparedThumb) return Promise.resolve(preparedThumb);
        if (!thumbSource) return Promise.reject(new Error('choose a thumbnail image'));
        return autoCrop().then(function(blob){ preparedThumb = blob; return blob; });
      }
      if (thumbInput) {
        thumbInput.addEventListener('change', function(){
          var file = thumbInput.files && thumbInput.files[0];
          preparedThumb = null; thumbSource = null;
          if (thumbObjectUrl) URL.revokeObjectURL(thumbObjectUrl);
          if (!file) { thumbPreview.hidden = true; closeCrop(); refresh(); return; }
          thumbObjectUrl = URL.createObjectURL(file);
          var image = new Image();
          image.onload = function(){
            thumbSource = image; thumbImage.src = thumbObjectUrl; thumbPreview.hidden = false;
            cropStatus.textContent = 'Centered 16:9 crop will be used on save.';
            closeCrop(); refresh();
          };
          image.onerror = function(){ thumbPreview.hidden = true; note(msg, 'The selected file is not a readable image.', false); };
          image.src = thumbObjectUrl;
        });
        byId('ge-thumb-crop').addEventListener('click', openCrop);
        byId('ge-crop-size').addEventListener('input', function(){
          if (!cropGeom) return;
          var old = {x: cropGeom.x + cropGeom.w / 2, y: cropGeom.y + cropGeom.h / 2};
          var maxW = Math.min(cropGeom.frame.width, cropGeom.frame.height * 16 / 9);
          cropGeom.w = maxW * Number(cropSize.value) / 100; cropGeom.h = cropGeom.w * 9 / 16;
          cropGeom.x = old.x - cropGeom.w / 2; cropGeom.y = old.y - cropGeom.h / 2;
          clampCrop();
        });
        byId('ge-crop-apply').addEventListener('click', function(){
          cropResult().then(function(blob){
            preparedThumb = blob; thumbImage.src = URL.createObjectURL(blob);
            cropStatus.textContent = 'Manual 16:9 crop applied.';
            closeCrop(); refresh();
          }).catch(function(err){ note(msg, err.message, false); });
        });
        byId('ge-crop-cancel').addEventListener('click', closeCrop);
        cropStage.addEventListener('pointerdown', function(ev){
          if (!cropGeom || ev.target !== cropBox && !cropBox.contains(ev.target)) return;
          cropDragging = true; cropDragX = ev.clientX; cropDragY = ev.clientY;
          cropBox.setPointerCapture(ev.pointerId);
        });
        cropStage.addEventListener('pointermove', function(ev){
          if (!cropDragging || !cropGeom) return;
          cropGeom.x += ev.clientX - cropDragX; cropGeom.y += ev.clientY - cropDragY;
          cropDragX = ev.clientX; cropDragY = ev.clientY; clampCrop();
        });
        cropStage.addEventListener('pointerup', function(){ cropDragging = false; });
      }
      // Client image cropper logic FINISH

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
          return prepareThumbnail().then(function(blob){
            var fd = form('game', gameEditData.game, 'thumbnail', '');
            fd.append('thumbnail', blob, 'thumbnail.jpg');
            return post('/api/expert/edit', fd, saveBtn);
          }).catch(function(err){ return {ok: false, j: {error: err.message}}; });
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
            setLastBtn(saveBtn);
            renderCards();
            noteBuilt(msg, 'Saved ' + n + ' change' + (n === 1 ? '' : 's') + '.', lastSerial);
            return;
          }
          var op = ops.shift();
          op.run().then(function(res){
            if (res.ok && res.j.ok) { op.done(res); n++; lastSerial = res.j.serial || lastSerial; step(); }
            else {
              setLastBtn(saveBtn);
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

export const page = 'game-edit';
window.TARApp.page = page;
