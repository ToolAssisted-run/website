// toolAssisted.run — the game editor (covering experts and editors):
// one local draft, one Save that replays the differences as an ordered
// sequence of logged edits. Moved out of app.js: these ids exist only
// on a game's /edit/ page.
import { api, mePromise, viewAsCoverage, el, note, noteBuilt, post,
  initMetricsEd, setLastBtn, escapeHtml } from './app.js';

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
      // Helper: 6-dot grip icon for drag handles
      function gripIcon(size){
        var s = size || 14;
        var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('class', 'grip-icon');
        svg.setAttribute('width', s);
        svg.setAttribute('height', s);
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('fill', 'currentColor');
        svg.setAttribute('aria-hidden', 'true');
        [[9,6], [15,6], [9,12], [15,12], [9,18], [15,18]].forEach(function(pt){
          var cEl = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
          cEl.setAttribute('cx', pt[0]);
          cEl.setAttribute('cy', pt[1]);
          cEl.setAttribute('r', '2');
          svg.appendChild(cEl);
        });
        return svg;
      }

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
          b.addEventListener('click', function(ev){
            ev.stopPropagation();
            list.splice(i, 1); list.splice(j, 0, item);
            render();
          });
          wrap.appendChild(b);
        });
        return wrap;
      }

      var draggedCat = null;
      function clearCatDragClasses(){
        box.querySelectorAll('.gecard').forEach(function(node){
          node.classList.remove('drag-over-top', 'drag-over-bottom', 'dragging');
        });
      }

      function card(c){
        var el_ = el('div', 'gecard');
        c.el = el_;
        if (c.deleted) el_.classList.add('deleted');
        if (c._open) el_.classList.add('is-open');

        var head = el('div', 'gehead gecard-head');
        var headLeft = el('div', 'gehead-left gecard-head-left');

        // Drag handle
        var handle = el('button', 'drag-handle');
        handle.type = 'button';
        handle.title = 'Drag to reorder category';
        handle.setAttribute('aria-label', 'Drag to reorder category');
        handle.setAttribute('draggable', 'true');
        handle.appendChild(gripIcon(14));
        handle.addEventListener('dragstart', function(ev){
          draggedCat = c;
          ev.dataTransfer.effectAllowed = 'move';
          ev.dataTransfer.setData('text/plain', draft.cats.indexOf(c));
          el_.classList.add('dragging');
        });
        handle.addEventListener('dragend', function(){
          el_.classList.remove('dragging');
          clearCatDragClasses();
          draggedCat = null;
        });
        handle.addEventListener('click', function(ev){ ev.stopPropagation(); });
        headLeft.appendChild(handle);

        // Toggle expander button
        var toggleBtn = el('button', 'gecard-toggle');
        toggleBtn.type = 'button';
        toggleBtn.setAttribute('aria-expanded', c._open ? 'true' : 'false');
        toggleBtn.setAttribute('aria-label', (c._open ? 'Collapse ' : 'Expand ') + (c.label || c.key || 'category'));
        var arrow = el('span', 'expander-arrow');
        toggleBtn.appendChild(arrow);
        headLeft.appendChild(toggleBtn);

        // Category title & slug display (inline label + muted slug pill)
        var titleWrap = el('span', 'gecard-title');
        var labelDisplay = el('span', 'gecard-label-text', c.label || c.key || '(new category)');
        var keyDisplay = el('code', 'geslug-code', c.key || '(assigned on save)');
        titleWrap.appendChild(labelDisplay);
        titleWrap.appendChild(keyDisplay);
        headLeft.appendChild(titleWrap);

        // Runs badge
        var runsMeta = el('span', 'actmeta', ' ' + (c.runs || 0) + ' run' + (c.runs === 1 ? '' : 's'));
        headLeft.appendChild(runsMeta);

        head.appendChild(headLeft);

        // Header action buttons (reorder arrows + delete)
        var headActions = el('div', 'gehead-actions gecard-head-actions');
        headActions.appendChild(orderArrows(draft.cats, c, renderCards));

        if (!c.runs) {
          var del = el('button', 'btn danger', c.deleted ? 'Keep' : 'Delete');
          del.type = 'button';
          del.addEventListener('click', function(ev){
            ev.stopPropagation();
            if (c.isNew) { draft.cats.splice(draft.cats.indexOf(c), 1); renderCards(); return; }
            c.deleted = !c.deleted; renderCards();
          });
          headActions.appendChild(del);
        }
        head.appendChild(headActions);
        el_.appendChild(head);

        // Toggle category expansion
        function toggleOpen(){
          c._open = !c._open;
          el_.classList.toggle('is-open', c._open);
          toggleBtn.setAttribute('aria-expanded', c._open ? 'true' : 'false');
          toggleBtn.setAttribute('aria-label', (c._open ? 'Collapse ' : 'Expand ') + (c.label || c.key || 'category'));
          body.hidden = !c._open;
        }
        head.addEventListener('click', function(ev){
          if (ev.target.closest('button, input, a')) return;
          toggleOpen();
        });
        toggleBtn.addEventListener('click', function(ev){
          ev.stopPropagation();
          toggleOpen();
        });

        // Drop target listeners on el_
        el_.addEventListener('dragover', function(ev){
          if (!draggedCat || draggedCat === c) return;
          ev.preventDefault();
          ev.dataTransfer.dropEffect = 'move';
          var rect = el_.getBoundingClientRect();
          var midY = rect.top + rect.height / 2;
          if (ev.clientY < midY) {
            el_.classList.add('drag-over-top');
            el_.classList.remove('drag-over-bottom');
          } else {
            el_.classList.add('drag-over-bottom');
            el_.classList.remove('drag-over-top');
          }
        });
        el_.addEventListener('dragleave', function(ev){
          if (!el_.contains(ev.relatedTarget)) {
            el_.classList.remove('drag-over-top', 'drag-over-bottom');
          }
        });
        el_.addEventListener('drop', function(ev){
          if (!draggedCat || draggedCat === c) return;
          ev.preventDefault();
          var fromIdx = draft.cats.indexOf(draggedCat);
          var toIdx = draft.cats.indexOf(c);
          if (fromIdx < 0 || toIdx < 0) return;
          var rect = el_.getBoundingClientRect();
          var insertAfter = ev.clientY >= (rect.top + rect.height / 2);
          draft.cats.splice(fromIdx, 1);
          var newTargetIdx = draft.cats.indexOf(c);
          var destIdx = insertAfter ? newTargetIdx + 1 : newTargetIdx;
          draft.cats.splice(destIdx, 0, draggedCat);
          clearCatDragClasses();
          draggedCat = null;
          renderCards();
        });

        // The collapsible body
        var body = el('div', 'gecard-body');
        body.hidden = !c._open;

        if (c.deleted) {
          body.appendChild(el('p', 'rules', 'Marked for deletion; Save removes it.'));
          el_.appendChild(body);
          return el_;
        }

        function field(labelText, tag){
          var lab = el('label', '', labelText);
          var inp = el(tag === 'textarea' ? 'textarea' : 'input');
          lab.appendChild(inp);
          body.appendChild(lab);
          return inp;
        }
        var labelIn = field('Label'); labelIn.value = c.label; labelIn.maxLength = 80;
        labelIn.addEventListener('input', function(){
          c.label = labelIn.value;
          labelDisplay.textContent = c.label || c.key || '(new category)';
          refresh();
        });
        if (!c.isNew) {
          c.newKey = c.newKey || c.key;
          var keyIn = field('Key (lowercase-with-hyphens: the address rankings and links use; runs follow a rename)');
          keyIn.value = c.newKey; keyIn.maxLength = 60; keyIn.pattern = '[a-z0-9]+(-[a-z0-9]+)*';
          keyIn.addEventListener('input', function(){
            c.newKey = keyIn.value.trim();
            keyDisplay.textContent = c.newKey || c.key || '(assigned on save)';
            refresh();
          });
        }
        var ruleIn = field('Rule (markdown)', 'textarea'); ruleIn.value = c.rule; ruleIn.rows = 4; ruleIn.maxLength = 2000;
        ruleIn.addEventListener('input', function(){ c.rule = ruleIn.value; refresh(); });

        // Preview button logic for rules
        var rulePvBtn = el('button', 'btn quiet', 'Preview'); rulePvBtn.type = 'button'; rulePvBtn.style.marginTop = '8px';
        var rulePvBox = el('div', 'ge-rule-pv'); rulePvBox.hidden = true; rulePvBox.style.marginTop = '8px';
        var rulePvContent = el('div', 'rulesmd notes');
        rulePvBox.appendChild(rulePvContent);
        body.appendChild(rulePvBtn);
        body.appendChild(rulePvBox);
        rulePvBtn.addEventListener('click', function(){
          if (rulePvBox.hidden) {
            rulePvBox.hidden = false;
            rulePvContent.textContent = 'Rendering…';
            var fd = new FormData();
            fd.append('notes', ruleIn.value || '');
            fd.append('kind', 'rules');
            post('/api/preview', fd, rulePvBtn).then(function(res){
              rulePvContent.innerHTML = (res.ok && res.j.ok) ? res.j.html : escapeHtml((res.j && res.j.error) || 'preview failed');
            }).catch(function(){ rulePvContent.textContent = 'The archivist is not reachable; the preview needs it.'; });
          } else {
            rulePvBox.hidden = true;
          }
        });

        var metricsRoot = el('div');
        metricsRoot.innerHTML = byId('med-skeleton').innerHTML;
        var metricsBox = metricsRoot.firstElementChild;
        body.appendChild(metricsBox);
        var metricsEd = initMetricsEd(metricsBox, JSON.parse(c.metrics || '[]'));
        c.metricsEd = metricsEd;
        // the baseline takes the editor's own spelling of the same metrics,
        // so an untouched category never reads as changed
        if (!c.isNew) base.cats.forEach(function(b){ if (b.key === c.key && !b.metricsNormalized) { b.metrics = metricsEd.value(); b.metricsNormalized = true; } });
        c.metrics = metricsEd.value();
        metricsBox.addEventListener('input', refresh);
        metricsBox.addEventListener('click', function(){ setTimeout(refresh, 0); });

        // Subcategories section
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
        var draggedSub = null;
        function clearSubDragClasses(){
          subList.querySelectorAll('.gesubcard').forEach(function(node){
            node.classList.remove('drag-over-top', 'drag-over-bottom', 'dragging');
          });
        }

        function subRow(sc){
          var subCard = el('div', 'gesubcard');
          if (sc.deleted) subCard.classList.add('deleted');
          if (sc._open) subCard.classList.add('is-open');

          var sHead = el('div', 'gesub-head');
          var sHeadLeft = el('div', 'gesub-head-left');

          // Drag handle
          var sHandle = el('button', 'drag-handle sub-drag-handle');
          sHandle.type = 'button';
          sHandle.title = 'Drag to reorder subcategory';
          sHandle.setAttribute('aria-label', 'Drag to reorder subcategory');
          sHandle.setAttribute('draggable', 'true');
          sHandle.appendChild(gripIcon(13));
          sHandle.addEventListener('dragstart', function(ev){
            draggedSub = sc;
            ev.dataTransfer.effectAllowed = 'move';
            ev.dataTransfer.setData('text/plain', c.subs.indexOf(sc));
            subCard.classList.add('dragging');
          });
          sHandle.addEventListener('dragend', function(){
            subCard.classList.remove('dragging');
            clearSubDragClasses();
            draggedSub = null;
          });
          sHandle.addEventListener('click', function(ev){ ev.stopPropagation(); });
          sHeadLeft.appendChild(sHandle);

          // Expander button
          var sToggle = el('button', 'gesub-toggle');
          sToggle.type = 'button';
          sToggle.setAttribute('aria-expanded', sc._open ? 'true' : 'false');
          sToggle.setAttribute('aria-label', (sc._open ? 'Collapse ' : 'Expand ') + (sc.label || sc.key || 'subcategory'));
          var sArrow = el('span', 'expander-arrow');
          sToggle.appendChild(sArrow);
          sHeadLeft.appendChild(sToggle);

          // Subcategory title & slug display (inline label + muted slug pill)
          var sTitleWrap = el('span', 'gesub-title');
          var sLabelText = el('span', 'gesub-label-text', sc.label || sc.key || '(new subcategory)');
          var sKeyDisplay = el('code', 'geslug-code', sc.key || '(assigned on save)');
          sTitleWrap.appendChild(sLabelText);
          sTitleWrap.appendChild(sKeyDisplay);
          sHeadLeft.appendChild(sTitleWrap);

          // Runs badge
          var sRunsMeta = el('span', 'actmeta', ' ' + (sc.runs || 0) + ' run' + (sc.runs === 1 ? '' : 's'));
          sHeadLeft.appendChild(sRunsMeta);

          sHead.appendChild(sHeadLeft);

          // Actions
          var sHeadActions = el('div', 'gesub-head-actions');
          sHeadActions.appendChild(orderArrows(c.subs, sc, renderSubs));

          var live = c.subs.filter(function(x){ return !x.deleted; });
          if (!sc.runs || live.length === 1) {
            var sDel = el('button', 'btn danger', sc.deleted ? 'Keep' : 'Delete');
            sDel.type = 'button';
            sDel.addEventListener('click', function(ev){
              ev.stopPropagation();
              if (sc.isNew) { c.subs.splice(c.subs.indexOf(sc), 1); renderSubs(); return; }
              sc.deleted = !sc.deleted; renderSubs();
            });
            sHeadActions.appendChild(sDel);
          }
          sHead.appendChild(sHeadActions);
          subCard.appendChild(sHead);

          function toggleSubOpen(){
            sc._open = !sc._open;
            subCard.classList.toggle('is-open', sc._open);
            sToggle.setAttribute('aria-expanded', sc._open ? 'true' : 'false');
            sToggle.setAttribute('aria-label', (sc._open ? 'Collapse ' : 'Expand ') + (sc.label || sc.key || 'subcategory'));
            sBody.hidden = !sc._open;
          }
          sHead.addEventListener('click', function(ev){
            if (ev.target.closest('button, input, a')) return;
            toggleSubOpen();
          });
          sToggle.addEventListener('click', function(ev){
            ev.stopPropagation();
            toggleSubOpen();
          });

          // DnD events for subCard
          subCard.addEventListener('dragover', function(ev){
            if (!draggedSub || draggedSub === sc) return;
            ev.preventDefault();
            ev.dataTransfer.dropEffect = 'move';
            var rect = subCard.getBoundingClientRect();
            var midY = rect.top + rect.height / 2;
            if (ev.clientY < midY) {
              subCard.classList.add('drag-over-top');
              subCard.classList.remove('drag-over-bottom');
            } else {
              subCard.classList.add('drag-over-bottom');
              subCard.classList.remove('drag-over-top');
            }
          });
          subCard.addEventListener('dragleave', function(ev){
            if (!subCard.contains(ev.relatedTarget)) {
              subCard.classList.remove('drag-over-top', 'drag-over-bottom');
            }
          });
          subCard.addEventListener('drop', function(ev){
            if (!draggedSub || draggedSub === sc) return;
            ev.preventDefault();
            var fromIdx = c.subs.indexOf(draggedSub);
            var toIdx = c.subs.indexOf(sc);
            if (fromIdx < 0 || toIdx < 0) return;
            var rect = subCard.getBoundingClientRect();
            var insertAfter = ev.clientY >= (rect.top + rect.height / 2);
            c.subs.splice(fromIdx, 1);
            var newTargetIdx = c.subs.indexOf(sc);
            var destIdx = insertAfter ? newTargetIdx + 1 : newTargetIdx;
            c.subs.splice(destIdx, 0, draggedSub);
            clearSubDragClasses();
            draggedSub = null;
            renderSubs();
          });

          // Subcategory body
          var sBody = el('div', 'gesub-body');
          sBody.hidden = !sc._open;

          if (sc.deleted) {
            sBody.appendChild(el('p', 'rules', (sc.label || 'Subcategory') + ': marked for deletion'));
          } else {
            var lLab = el('label', '', 'Label');
            var l = el('input'); l.value = sc.label; l.maxLength = 80; l.placeholder = 'label';
            lLab.appendChild(l);
            l.addEventListener('input', function(){
              sc.label = l.value;
              sLabelText.textContent = sc.label || sc.key || '(new subcategory)';
              refresh();
            });

            var rLab = el('label', '', 'Rule fragment (markdown, optional)');
            var r = el('textarea'); r.value = sc.rule; r.maxLength = 2000; r.rows = 2; r.placeholder = 'rule fragment, markdown (optional)';
            rLab.appendChild(r);
            r.addEventListener('input', function(){ sc.rule = r.value; refresh(); });

            sBody.appendChild(lLab);
            sBody.appendChild(rLab);

            // Preview button logic
            var subPvRow = el('div', 'sub-pv-row');
            var subPvBtn = el('button', 'btn quiet', 'Preview'); subPvBtn.type = 'button';
            var subPvBox = el('div', 'ge-subrule-pv'); subPvBox.hidden = true;
            subPvBox.style.marginTop = '6px';
            var subPvContent = el('div', 'rulesmd notes');
            subPvBox.appendChild(subPvContent);
            subPvBtn.addEventListener('click', function(){
              if (subPvBox.hidden) {
                subPvBox.hidden = false;
                subPvContent.textContent = 'Rendering…';
                var fd = new FormData();
                fd.append('notes', r.value || '');
                fd.append('kind', 'rules');
                post('/api/preview', fd, subPvBtn).then(function(res){
                  subPvContent.innerHTML = (res.ok && res.j.ok) ? res.j.html : escapeHtml((res.j && res.j.error) || 'preview failed');
                }).catch(function(){ subPvContent.textContent = 'The archivist is not reachable; the preview needs it.'; });
              } else {
                subPvBox.hidden = true;
              }
            });
            subPvRow.appendChild(subPvBtn);
            subPvRow.appendChild(subPvBox);
            sBody.appendChild(subPvRow);
          }
          subCard.appendChild(sBody);
          subList.appendChild(subCard);
        }

        function renderSubs(){ subList.innerHTML = ''; c.subs.forEach(subRow); refresh(); }
        renderSubs();
        subBox.appendChild(subList);

        var addRow = el('div', 'subadd');
        var addLabel = el('input'); addLabel.placeholder = 'new subcategory label, e.g. any%'; addLabel.maxLength = 80;
        var addBtn = el('button', 'btn leave', '+ Add a subcategory'); addBtn.type = 'button';
        addBtn.addEventListener('click', function(){
          if (!addLabel.value.trim()) { addLabel.focus(); return; }
          c.subs.push({key: '', label: addLabel.value.trim(), rule: '', runs: 0, isNew: true, _open: true, tmp: ++newSeq});
          addLabel.value = '';
          renderSubs();
        });
        addRow.appendChild(addLabel); addRow.appendChild(addBtn);
        subBox.appendChild(addRow);

        body.appendChild(subBox);
        el_.appendChild(body);
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
        draft.cats.push({key: '', label: label.trim(), rule: '', runs: 0, metrics: '[]', subs: [], isNew: true, _open: true, tmp: ++newSeq});
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
      
      // Preview button logic for rules
      var rulesPvBtn = byId('ge-rules-pv-btn');
      var rulesPvBox = byId('ge-rules-pv');
      var rulesPvContent = byId('ge-rules-pv-content');
      if (rulesPvBtn && rulesPvBox && rulesPvContent) {
        rulesPvBtn.addEventListener('click', function(){
          if (rulesPvBox.hidden) {
            rulesPvBox.hidden = false;
            rulesPvContent.textContent = 'Rendering…';
            var fd = new FormData();
            fd.append('notes', byId('ge-rules').value || '');
            fd.append('kind', 'rules');
            post('/api/preview', fd, rulesPvBtn).then(function(res){
              rulesPvContent.innerHTML = (res.ok && res.j.ok) ? res.j.html : escapeHtml((res.j && res.j.error) || 'preview failed');
            }).catch(function(){ rulesPvContent.textContent = 'The archivist is not reachable; the preview needs it.'; });
          } else {
            rulesPvBox.hidden = true;
          }
        });
      }
      
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
