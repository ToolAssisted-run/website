// toolAssisted.run — tools page client module:
// site-wide expert and editor curation of the authoritative TAS tools catalog
import { mePromise, el, setMark, post, viewAsCoverage, viewAsActive, waitBuilt, escapeHtml } from './app.js';

var expertMenu = document.getElementById('toolexpertmenu');
var emuDataEl = document.getElementById('toolemudata');
var siteExpertsEl = document.getElementById('siteexpertsdata');

if (expertMenu && emuDataEl) {
  var catalog = null;
  try {
    catalog = JSON.parse(emuDataEl.textContent);
  } catch(e) {
    catalog = { systems: {}, catalog: [] };
  }
  catalog.systems = catalog.systems || {};
  catalog.catalog = catalog.catalog || catalog.presets || [];

  var siteExperts = [];
  try {
    if (siteExpertsEl) siteExperts = JSON.parse(siteExpertsEl.textContent);
  } catch(e) {}

  mePromise.then(function(d){
    if (!d || !d.loggedIn) {
      if (expertMenu) expertMenu.hidden = true;
      document.querySelectorAll('.tool-act-col, .cat-add-btn').forEach(function(elem){
        elem.hidden = true;
      });
      return;
    }
    var who = (d.user || '').toLowerCase();
    var T = window.TAR || {};

    // Gated strictly to site-wide experts and editors (respecting View As role switcher)
    var siteExpertsLower = (siteExperts || []).map(function(x){ return (x || '').toLowerCase(); });
    var effectiveSiteExperts = viewAsCoverage(siteExpertsLower, who);
    var isSiteExpert = effectiveSiteExperts.map(function(x){ return (x || '').toLowerCase(); }).indexOf(who) >= 0;
    var isEditor = (!viewAsActive() || viewAsActive() === 'editor') &&
                   (T.editors || []).map(function(x){ return (x || '').toLowerCase(); }).indexOf(who) >= 0;

    if (!isSiteExpert && !isEditor) {
      if (expertMenu) expertMenu.hidden = true;
      document.querySelectorAll('.tool-act-col, .cat-add-btn').forEach(function(elem){
        elem.hidden = true;
      });
      return;
    }

    if (expertMenu) {
      expertMenu.hidden = false;
      if (!isSiteExpert && isEditor) {
        var h2 = expertMenu.querySelector('h2');
        if (h2 && /Expert menu/.test(h2.textContent)) h2.textContent = 'Editor menu';
      }
    }

    // Unhide actions column and Add buttons for authorized users
    document.querySelectorAll('.tool-act-col, .cat-add-btn').forEach(function(elem){
      elem.hidden = false;
    });

    if (location.hash === '#toolexpertmenu' || location.hash === '#cat-savebar') {
      try { expertMenu.scrollIntoView({ behavior: 'smooth' }); } catch(e) {}
    }

    initCatalogCurator();
  });
}

function initCatalogCurator(){
  var rawList = (catalog && (catalog.catalog || catalog.presets)) || [];
  var originalCatalog = JSON.parse(JSON.stringify(rawList));
  var currentTools = JSON.parse(JSON.stringify(rawList));

  var toolsById = {};
  var originalById = {};

  function reindex(){
    toolsById = {};
    currentTools.forEach(function(t){
      if (t && t.id) toolsById[t.id.toLowerCase()] = t;
    });
    originalById = {};
    originalCatalog.forEach(function(t){
      if (t && t.id) originalById[t.id.toLowerCase()] = t;
    });
  }
  reindex();

  var pendingEl = document.getElementById('cat-pending');
  var pendingListEl = document.getElementById('cat-pending-list');
  var reasonInp = document.getElementById('cat-curate-reason');
  var saveBtn = document.getElementById('cat-curate-save');
  var mark = document.getElementById('cat-curate-mark');
  var msg = document.getElementById('cat-curate-msg');

  var dlg = document.getElementById('cat-tool-dlg');
  var dlgTitle = document.getElementById('cat-dlg-title');
  var dlgDesc = document.getElementById('cat-dlg-desc');
  var toolMode = document.getElementById('cat-tool-mode');
  var toolKind = document.getElementById('cat-tool-kind');
  var toolId = document.getElementById('cat-tool-id');
  var toolName = document.getElementById('cat-tool-name');
  var toolUrl = document.getElementById('cat-tool-url');
  var toolGame = document.getElementById('cat-tool-game');
  var wrapGame = document.getElementById('cat-wrap-game');
  var toolSysDisp = document.getElementById('cat-tool-sys-disp');
  var wrapSysDisp = document.getElementById('cat-wrap-sys-disp');
  var toolFormats = document.getElementById('cat-tool-formats');
  var toolAliases = document.getElementById('cat-tool-aliases');
  var toolMulti = document.getElementById('cat-tool-multi');
  var toolCores = document.getElementById('cat-tool-cores');
  var toolParsed = document.getElementById('cat-tool-parsed');
  var lblMulti = document.getElementById('cat-lbl-multi');
  var lblCores = document.getElementById('cat-lbl-cores');
  var toolErr = document.getElementById('cat-tool-err');
  var cancelBtn = document.getElementById('cat-tool-cancel');
  var applyBtn = document.getElementById('cat-tool-apply');
  var openAddBtn = document.getElementById('cat-open-add-btn');

  function updateKindVisibility(){
    var k = toolKind ? toolKind.value : 'emulator';
    if (k === 'game_tool') {
      if (wrapGame) wrapGame.hidden = false;
      if (wrapSysDisp) wrapSysDisp.hidden = true;
      if (lblMulti) lblMulti.hidden = true;
      if (lblCores) lblCores.hidden = true;
    } else {
      if (wrapGame) wrapGame.hidden = true;
      if (wrapSysDisp) wrapSysDisp.hidden = false;
      if (lblMulti) lblMulti.hidden = false;
      if (lblCores) lblCores.hidden = false;
    }
  }
  if (toolKind) toolKind.addEventListener('change', updateKindVisibility);

  function openAddDialog(defaultKind){
    if (!dlg) return;
    if (toolErr) toolErr.hidden = true;
    if (dlgTitle) dlgTitle.textContent = 'Add tool to catalog';
    if (dlgDesc) dlgDesc.textContent = 'Define a new TAS tool to be supported in the site-wide catalog.';
    if (toolMode) toolMode.value = 'add';

    if (toolId) {
      toolId.value = '';
      toolId.disabled = false;
    }
    if (toolKind) toolKind.value = defaultKind || 'emulator';
    if (toolName) toolName.value = '';
    if (toolUrl) toolUrl.value = '';
    if (toolGame) toolGame.value = '';
    if (toolSysDisp) toolSysDisp.value = '';
    if (toolFormats) toolFormats.value = '';
    if (toolAliases) toolAliases.value = '';
    if (toolMulti) toolMulti.checked = false;
    if (toolCores) toolCores.checked = false;
    if (toolParsed) toolParsed.checked = true;

    updateKindVisibility();
    if (typeof dlg.showModal === 'function') dlg.showModal();
    else dlg.setAttribute('open', '');
  }

  function openEditDialog(tool){
    if (!dlg || !tool) return;
    if (toolErr) toolErr.hidden = true;
    if (dlgTitle) dlgTitle.textContent = 'Edit TAS tool: ' + (tool.name || tool.id);
    if (dlgDesc) dlgDesc.textContent = 'Update properties and configuration for ' + (tool.name || tool.id) + '.';
    if (toolMode) toolMode.value = 'edit';

    if (toolId) {
      toolId.value = tool.id || '';
      toolId.disabled = true;
    }
    if (toolKind) toolKind.value = tool.kind || 'emulator';
    if (toolName) toolName.value = tool.name || '';
    if (toolUrl) toolUrl.value = tool.url || '';
    if (toolGame) toolGame.value = tool.game || '';
    if (toolSysDisp) toolSysDisp.value = tool.systems_display || '';
    if (toolFormats) toolFormats.value = tool.formats || tool.format || '';
    if (toolAliases) toolAliases.value = (tool.aliases || []).join(', ');
    if (toolMulti) toolMulti.checked = !!tool.multi;
    if (toolCores) toolCores.checked = !!tool.has_cores;
    if (toolParsed) toolParsed.checked = (tool.parsed !== false);

    updateKindVisibility();
    if (typeof dlg.showModal === 'function') dlg.showModal();
    else dlg.setAttribute('open', '');
  }

  if (openAddBtn) {
    openAddBtn.addEventListener('click', function(e){
      e.preventDefault();
      openAddDialog('emulator');
    });
  }

  document.querySelectorAll('.cat-add-btn').forEach(function(b){
    b.addEventListener('click', function(e){
      e.preventDefault();
      openAddDialog(b.getAttribute('data-kind') || 'emulator');
    });
  });

  if (cancelBtn && dlg) {
    cancelBtn.addEventListener('click', function(e){
      e.preventDefault();
      if (typeof dlg.close === 'function') dlg.close();
      else dlg.removeAttribute('open');
    });
  }

  var IC_EDIT = '<svg class="tool-btn-ic" viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg>';
  var IC_REMOVE = '<svg class="tool-btn-ic" viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>';
  var IC_RESTORE = '<svg class="tool-btn-ic" viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M12.5 8c-2.65 0-5.05.99-6.9 2.6L2 7v9h9l-3.62-3.62c1.39-1.16 3.16-1.88 5.12-1.88 3.54 0 6.55 2.31 7.6 5.5l2.37-.78C21.08 11.03 17.15 8 12.5 8z"/></svg>';

  function wireRow(row){
    var tid = (row.getAttribute('data-tool-id') || '').toLowerCase();
    if (!tid) return;
    var editBtn = row.querySelector('.cat-edit-btn');
    if (editBtn && !editBtn._wired) {
      editBtn._wired = true;
      editBtn.addEventListener('click', function(e){
        e.preventDefault();
        if (editBtn.disabled) return;
        var t = toolsById[tid];
        if (t) openEditDialog(t);
      });
    }
    var delBtn = row.querySelector('.cat-del-btn');
    if (delBtn && !delBtn._wired) {
      delBtn._wired = true;
      delBtn.addEventListener('click', function(e){
        e.preventDefault();
        var t = toolsById[tid];
        if (!t) return;
        if (t._isNew) {
          currentTools = currentTools.filter(function(x){ return x.id.toLowerCase() !== tid; });
          row.remove();
          reindex();
          refresh();
        } else {
          t._deleted = !t._deleted;
          if (t._deleted) {
            row.classList.add('row-deleted');
            delBtn.classList.remove('danger');
            delBtn.classList.add('quiet');
            delBtn.innerHTML = IC_RESTORE;
            delBtn.title = 'Restore tool';
            delBtn.setAttribute('aria-label', 'Restore tool');
            if (editBtn) editBtn.disabled = true;
          } else {
            row.classList.remove('row-deleted');
            delBtn.classList.remove('quiet');
            delBtn.classList.add('danger');
            delBtn.innerHTML = IC_REMOVE;
            delBtn.title = 'Remove tool';
            delBtn.setAttribute('aria-label', 'Remove tool');
            if (editBtn) editBtn.disabled = false;
          }
          refresh();
        }
      });
    }
  }

  // Wire initial static rows
  document.querySelectorAll('tr[data-tool-id]').forEach(wireRow);

  if (applyBtn && dlg) {
    applyBtn.addEventListener('click', function(e){
      e.preventDefault();
      if (toolErr) toolErr.hidden = true;

      var tid = (toolId.value || '').trim().toLowerCase();
      var tname = (toolName.value || '').trim();
      var tkind = toolKind.value || 'emulator';
      var turl = (toolUrl.value || '').trim();
      var tgame = (toolGame.value || '').trim();
      var tsysdisp = (toolSysDisp ? toolSysDisp.value : '').trim();
      var tfmts = (toolFormats.value || '').trim();
      var taliases = (toolAliases.value || '').split(',').map(function(s){ return s.trim(); }).filter(Boolean);

      if (!tid || !/^[a-z0-9._-]+$/.test(tid)) {
        if (toolErr) {
          toolErr.textContent = 'Tool ID must be lowercase alphanumeric with hyphens, periods, or underscores.';
          toolErr.hidden = false;
        }
        return;
      }
      if (!tname) {
        if (toolErr) {
          toolErr.textContent = 'Display name is required.';
          toolErr.hidden = false;
        }
        return;
      }
      if (tkind === 'game_tool' && !tgame) {
        if (toolErr) {
          toolErr.textContent = 'Target game / engine name is required for game-specific tools.';
          toolErr.hidden = false;
        }
        return;
      }

      var mode = toolMode.value;
      if (mode === 'add') {
        var existing = currentTools.find(function(x){ return x.id.toLowerCase() === tid; });
        if (existing) {
          if (toolErr) {
            toolErr.textContent = 'A tool with ID "' + tid + '" already exists in the catalog.';
            toolErr.hidden = false;
          }
          return;
        }
        var newTool = {
          id: tid,
          name: tname,
          kind: tkind,
          url: turl,
          parsed: !!toolParsed.checked,
          _isNew: true
        };
        if (tkind === 'game_tool') {
          newTool.game = tgame;
          if (tfmts) newTool.format = tfmts;
        } else {
          if (tsysdisp) newTool.systems_display = tsysdisp;
          if (tfmts) newTool.formats = tfmts;
          if (toolMulti.checked) newTool.multi = true;
          if (toolCores.checked) newTool.has_cores = true;
        }
        if (taliases.length) newTool.aliases = taliases;

        currentTools.push(newTool);
        reindex();

        // Create and append row to appropriate table
        var tableBody = null;
        if (tkind === 'legacy') {
          var tHistorical = document.getElementById('tbl-historical');
          if (tHistorical) tableBody = tHistorical.querySelector('tbody');
        } else if (tkind === 'game_tool') {
          var tGame = document.getElementById('tbl-game-tools');
          if (tGame) tableBody = tGame.querySelector('tbody');
        } else {
          var tEmu = document.getElementById('tbl-emulators');
          if (tEmu) tableBody = tEmu.querySelector('tbody');
        }

        if (tableBody) {
          var tr = document.createElement('tr');
          tr.setAttribute('data-tool-id', newTool.id);
          tr.classList.add('row-added');

          var nameCell = el('td', '');
          var b = el('b', '');
          if (newTool.url) {
            var a = el('a', '', newTool.name);
            a.href = newTool.url;
            b.appendChild(a);
          } else {
            b.textContent = newTool.name;
          }
          nameCell.appendChild(b);
          tr.appendChild(nameCell);

          var dispText = '—';
          if (tkind === 'game_tool') dispText = newTool.game || '';
          else if (newTool.systems_display) dispText = newTool.systems_display;
          else if (newTool.multi) dispText = 'multi-system';
          var col2 = el('td', '', dispText);
          tr.appendChild(col2);

          var fmtVal = newTool.formats || newTool.format || '';
          var col3 = el('td', tkind === 'game_tool' ? '' : 'num');
          if (fmtVal) col3.appendChild(el('code', '', fmtVal));
          tr.appendChild(col3);

          var col4 = el('td', 'num');
          col4.innerHTML = newTool.parsed !== false ? '<span class="tick-yes">\u2713</span>' : '<span class="tick-no">\u2014</span>';
          tr.appendChild(col4);

          var col5 = el('td', 'tool-act-col num');
          var edBtn = el('button', 'btn quiet cat-edit-btn');
          edBtn.type = 'button';
          edBtn.setAttribute('data-id', newTool.id);
          edBtn.title = 'Edit tool';
          edBtn.setAttribute('aria-label', 'Edit tool');
          edBtn.innerHTML = IC_EDIT;
          var rmBtn = el('button', 'btn danger cat-del-btn');
          rmBtn.type = 'button';
          rmBtn.setAttribute('data-id', newTool.id);
          rmBtn.title = 'Remove tool';
          rmBtn.setAttribute('aria-label', 'Remove tool');
          rmBtn.innerHTML = IC_REMOVE;
          col5.appendChild(edBtn);
          col5.appendChild(document.createTextNode(' '));
          col5.appendChild(rmBtn);
          tr.appendChild(col5);

          tableBody.appendChild(tr);
          wireRow(tr);
        }
      } else {
        var targetTool = currentTools.find(function(x){ return x.id.toLowerCase() === tid; });
        if (targetTool) {
          targetTool.name = tname;
          targetTool.kind = tkind;
          targetTool.url = turl;
          targetTool.parsed = !!toolParsed.checked;
          if (tkind === 'game_tool') {
            targetTool.game = tgame;
            targetTool.format = tfmts;
            delete targetTool.formats;
            delete targetTool.multi;
            delete targetTool.has_cores;
            delete targetTool.systems_display;
          } else {
            targetTool.formats = tfmts;
            if (tsysdisp) targetTool.systems_display = tsysdisp;
            else delete targetTool.systems_display;
            delete targetTool.game;
            delete targetTool.format;
            if (toolMulti.checked) targetTool.multi = true;
            else delete targetTool.multi;
            if (toolCores.checked) targetTool.has_cores = true;
            else delete targetTool.has_cores;
          }
          if (taliases.length) targetTool.aliases = taliases;
          else delete targetTool.aliases;

          // Update existing row in DOM
          var row = document.querySelector('tr[data-tool-id="' + tid + '"]');
          if (row && row.cells.length >= 4) {
            // Col 0: Name
            row.cells[0].innerHTML = '<b>' + (targetTool.url ? '<a href="' + escapeHtml(targetTool.url) + '">' + escapeHtml(targetTool.name) + '</a>' : escapeHtml(targetTool.name)) + '</b>';
            // Col 1: System / Game
            if (targetTool.kind === 'game_tool') {
              row.cells[1].textContent = targetTool.game || '';
            } else if (targetTool.systems_display) {
              row.cells[1].textContent = targetTool.systems_display;
            } else if (targetTool.multi) {
              row.cells[1].textContent = 'multi-system';
            } else {
              row.cells[1].textContent = '—';
            }
            // Col 2: Format
            var curFmt = targetTool.formats || targetTool.format || '';
            row.cells[2].innerHTML = curFmt ? '<code>' + escapeHtml(curFmt) + '</code>' : '';
            // Col 3: Parsed
            row.cells[3].innerHTML = targetTool.parsed !== false ? '<span class="tick-yes">\u2713</span>' : '<span class="tick-no">\u2014</span>';

            // Mark modified if changed from original
            var orig = originalById[tid];
            var isModified = false;
            if (orig) {
              if (orig.name !== targetTool.name || orig.url !== targetTool.url ||
                  (orig.formats || orig.format || '') !== (targetTool.formats || targetTool.format || '') ||
                  (targetTool.game && orig.game !== targetTool.game) ||
                  (orig.systems_display || '') !== (targetTool.systems_display || '') ||
                  (targetTool.parsed !== false) !== (orig.parsed !== false) ||
                  !!targetTool.multi !== !!orig.multi ||
                  !!targetTool.has_cores !== !!orig.has_cores) {
                isModified = true;
              }
            }
            if (isModified) row.classList.add('row-modified');
            else row.classList.remove('row-modified');
          }
        }
      }

      if (typeof dlg.close === 'function') dlg.close();
      else dlg.removeAttribute('open');

      refresh();
    });
  }

  var dirty = false;

  function computeDiff(){
    var ops = [];
    currentTools.forEach(function(t){
      var tid = t.id.toLowerCase();
      if (t._isNew) {
        var kLabel = t.kind === 'legacy' ? 'old format' : (t.kind === 'game_tool' ? 'game tool' : 'emulator');
        ops.push({
          type: 'add',
          id: t.id,
          what: 'Added ' + (t.name || t.id) + ' (' + t.id + ', ' + kLabel + ')'
        });
      } else if (t._deleted) {
        ops.push({
          type: 'delete',
          id: t.id,
          what: 'Removed ' + (t.name || t.id) + ' (' + t.id + ')'
        });
      } else {
        var orig = originalById[tid];
        if (orig) {
          var changes = [];
          if ((t.name || '') !== (orig.name || '')) changes.push('name \u2192 "' + t.name + '"');
          if ((t.kind || 'emulator') !== (orig.kind || 'emulator')) changes.push('category \u2192 ' + (t.kind === 'legacy' ? 'old format' : t.kind));
          if ((t.url || '') !== (orig.url || '')) changes.push('url');
          if ((t.game || '') !== (orig.game || '')) changes.push('game \u2192 "' + t.game + '"');
          if ((t.systems_display || '') !== (orig.systems_display || '')) {
            changes.push('systems display override \u2192 ' + (t.systems_display ? '"' + t.systems_display + '"' : 'auto'));
          }
          var tFmt = t.formats || t.format || '';
          var oFmt = orig.formats || orig.format || '';
          if (tFmt !== oFmt) changes.push('format \u2192 "' + tFmt + '"');
          if (!!t.multi !== !!orig.multi) changes.push('multi-system: ' + (t.multi ? 'yes' : 'no'));
          if (!!t.has_cores !== !!orig.has_cores) changes.push('cores: ' + (t.has_cores ? 'yes' : 'no'));
          if ((t.parsed !== false) !== (orig.parsed !== false)) changes.push('parsed: ' + (t.parsed ? 'yes' : 'no'));
          var tAliases = (t.aliases || []).slice().sort().join(',');
          var oAliases = (orig.aliases || []).slice().sort().join(',');
          if (tAliases !== oAliases) changes.push('aliases');

          if (changes.length) {
            ops.push({
              type: 'modify',
              id: t.id,
              what: 'Updated ' + (t.name || t.id) + ' (' + t.id + '): ' + changes.join(', ')
            });
          }
        }
      }
    });

    originalCatalog.forEach(function(orig){
      var found = currentTools.find(function(x){ return x.id.toLowerCase() === orig.id.toLowerCase(); });
      if (!found) {
        ops.push({
          type: 'delete',
          id: orig.id,
          what: 'Removed ' + (orig.name || orig.id) + ' (' + orig.id + ')'
        });
      }
    });

    return ops;
  }

  function refresh(){
    var ops = computeDiff();
    dirty = ops.length > 0;
    if (!pendingEl) return;
    if (!ops.length) {
      pendingEl.textContent = 'No changes yet';
      if (pendingListEl) {
        pendingListEl.innerHTML = '';
        pendingListEl.hidden = true;
      }
      if (saveBtn) saveBtn.disabled = true;
    } else {
      pendingEl.textContent = ops.length + ' change' + (ops.length === 1 ? '' : 's') + ' pending: ' +
        ops.map(function(o){ return o.what; }).join('; ');
      if (pendingListEl) {
        pendingListEl.innerHTML = '';
        ops.forEach(function(o){
          var li = el('li', '', o.what);
          pendingListEl.appendChild(li);
        });
        pendingListEl.hidden = false;
      }
      if (saveBtn) saveBtn.disabled = false;
    }
  }

  // Save changes via POST /api/emulators/edit
  if (saveBtn) {
    saveBtn.addEventListener('click', function(e){
      e.preventDefault();
      if (msg) msg.hidden = true;
      var reason = (reasonInp ? reasonInp.value : '').trim();
      if (reason.length < 8) {
        if (msg) {
          msg.textContent = 'Please provide a public reason of at least 8 characters explaining the changes.';
          msg.hidden = false;
        }
        if (reasonInp) reasonInp.focus();
        return;
      }

      var ops = computeDiff();
      if (!ops.length) return;

      saveBtn.disabled = true;
      setMark(mark, 'busy', 'Saving catalog changes…');

      var cleanCatalog = currentTools.filter(function(t){ return !t._deleted; }).map(function(t){
        var clone = JSON.parse(JSON.stringify(t));
        delete clone._isNew;
        delete clone._deleted;
        return clone;
      });

      var payload = {
        system: 'default',
        catalog: JSON.stringify(cleanCatalog),
        reason: reason
      };

      post('/api/emulators/edit', payload)
        .then(function(res){
          saveBtn.disabled = false;
          if (!res || !res.ok) {
            setMark(mark, 'err', (res && res.error) || 'Could not save catalog changes.');
            if (msg) {
              msg.textContent = (res && res.error) || 'Error saving changes.';
              msg.hidden = false;
            }
            return;
          }
          dirty = false;
          setMark(mark, 'ok', 'Saved ✓ Rebuilding site…');
          if (reasonInp) reasonInp.value = '';
          waitBuilt(res.serial || 'latest', function(){
            location.reload();
          });
        })
        .catch(function(err){
          saveBtn.disabled = false;
          setMark(mark, 'err', err.message || 'Network error.');
          if (msg) {
            msg.textContent = err.message || 'Network error.';
            msg.hidden = false;
          }
        });
    });
  }

  window.addEventListener('beforeunload', function(ev){
    if (!dirty) return;
    ev.preventDefault();
    ev.returnValue = '';
  });

  refresh();
}

var page = { initCatalogCurator: initCatalogCurator };
window.TARApp = window.TARApp || {};
window.TARApp.page = page;
export { page };
