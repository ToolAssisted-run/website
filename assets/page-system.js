// toolAssisted.run — system page client module:
// curation of emulator presets, version presets, cores, and quick chips
import { mePromise, el, setMark, post, viewAsCoverage, viewAsActive } from './app.js';

var expertMenu = document.getElementById('sysexpertmenu');
var curateFold = document.getElementById('sys-emu-curate');
var emuDataEl = document.getElementById('sysemudata');
var sysKeyEl = document.getElementById('syskeydata');
var sysExpertsEl = document.getElementById('sysexpertsdata');
var siteExpertsEl = document.getElementById('siteexpertsdata');

if ((expertMenu || curateFold) && emuDataEl && sysKeyEl) {
  var catalog = null;
  try {
    catalog = JSON.parse(emuDataEl.textContent);
  } catch(e) {
    catalog = { systems: {}, catalog: [] };
  }
  catalog.systems = catalog.systems || {};
  catalog.catalog = catalog.catalog || catalog.presets || [];

  var skey = '';
  try {
    skey = JSON.parse(sysKeyEl.textContent);
  } catch(e) {
    skey = (sysKeyEl.textContent || '').trim().replace(/^"|"$/g, '');
  }

  var sysExperts = [];
  try {
    if (sysExpertsEl) sysExperts = JSON.parse(sysExpertsEl.textContent);
  } catch(e) {}

  var siteExperts = [];
  try {
    if (siteExpertsEl) siteExperts = JSON.parse(siteExpertsEl.textContent);
  } catch(e) {}

  mePromise.then(function(d){
    if (!d || !d.loggedIn) {
      if (expertMenu) expertMenu.hidden = true;
      if (curateFold) curateFold.hidden = true;
      return;
    }
    var who = (d.user || '').toLowerCase();
    var T = window.TAR || {};

    // Check authority: strictly system experts covering this system, site-wide experts, or editors.
    // Respect View As role switcher (plain members or masked roles cannot view).
    var allExperts = (sysExperts || []).concat(siteExperts || []).map(function(x){ return (x || '').toLowerCase(); });
    var effectiveExperts = viewAsCoverage(allExperts, who);
    var isCoveringExpert = effectiveExperts.map(function(x){ return (x || '').toLowerCase(); }).indexOf(who) >= 0;
    var isEditor = (!viewAsActive() || viewAsActive() === 'editor') &&
                   (T.editors || []).map(function(x){ return (x || '').toLowerCase(); }).indexOf(who) >= 0;

    if (!isCoveringExpert && !isEditor) {
      if (expertMenu) expertMenu.hidden = true;
      if (curateFold) curateFold.hidden = true;
      return;
    }

    // Authorized curator: reveal Expert menu box
    if (expertMenu) {
      expertMenu.hidden = false;
      if (!isCoveringExpert && isEditor) {
        var h2 = expertMenu.querySelector('h2');
        if (h2 && /Expert menu/.test(h2.textContent)) h2.textContent = 'Editor menu';
      }
    }
    if (curateFold) curateFold.hidden = false;

    if (location.hash === '#sys-emu-curate' || location.hash === '#sysexpertmenu') {
      if (curateFold) curateFold.open = true;
      try { (curateFold || expertMenu).scrollIntoView({ behavior: 'smooth' }); } catch(e) {}
    }

    initCurationUI();
  });

  function initCurationUI(){
    var qcChipsBox = document.getElementById('sys-qc-chips');
    var qcNewInp = document.getElementById('sys-qc-new');
    var qcAddBtn = document.getElementById('sys-qc-add');
    var mappedList = document.getElementById('sys-mapped-tools-list');
    var catalogSelect = document.getElementById('sys-tool-catalog-select');
    var catalogAddBtn = document.getElementById('sys-tool-catalog-add');
    var reasonInp = document.getElementById('sys-curate-reason');
    var saveBtn = document.getElementById('sys-curate-save');
    var msgEl = document.getElementById('sys-curate-msg');

    if (!catalog.systems[skey]) {
      var defQc = (catalog.systems['default'] && catalog.systems['default'].quick_chips) || [];
      catalog.systems[skey] = {
        quick_chips: defQc.slice(),
        tools: []
      };
    }
    var sysData = catalog.systems[skey];
    sysData.quick_chips = sysData.quick_chips || [];
    sysData.tools = sysData.tools || [];
    var currentQuickChips = sysData.quick_chips;

    // Helper: find tool in catalog
    function findToolDef(id){
      for (var i = 0; i < catalog.catalog.length; i++) {
        if (catalog.catalog[i].id === id) return catalog.catalog[i];
      }
      return { id: id, name: id, kind: 'emulator' };
    }

    // System tools are strictly emulators and legacy formats; game-specific tools cannot be mapped to systems
    var currentTools = sysData.tools.filter(function(t){
      return findToolDef(t.id).kind !== 'game_tool';
    });
    sysData.tools = currentTools;

    // ---- Quick Chips Rendering ----
    function renderQuickChips(){
      if (!qcChipsBox) return;
      qcChipsBox.innerHTML = '';
      if (!currentQuickChips.length) {
        qcChipsBox.appendChild(el('p', 'rules fullw', 'No quick chips configured for this system.'));
        return;
      }
      currentQuickChips.forEach(function(chipText, idx){
        var chip = el('span', 'emu-chip', chipText);
        var xBtn = el('button', 'sys-qc-chip-x', '×');
        xBtn.type = 'button';
        xBtn.title = 'Remove quick chip';
        xBtn.addEventListener('click', function(e){
          e.preventDefault();
          currentQuickChips.splice(idx, 1);
          renderQuickChips();
        });
        chip.appendChild(xBtn);
        qcChipsBox.appendChild(chip);
      });
    }

    if (qcAddBtn && qcNewInp) {
      qcAddBtn.addEventListener('click', function(e){
        e.preventDefault();
        var val = qcNewInp.value.trim();
        if (val && currentQuickChips.indexOf(val) < 0) {
          currentQuickChips.push(val);
          qcNewInp.value = '';
          renderQuickChips();
        }
      });
      qcNewInp.addEventListener('keydown', function(e){
        if (e.key === 'Enter') {
          e.preventDefault();
          qcAddBtn.click();
        }
      });
    }

    // ---- Mapped Tools List Rendering ----
    function renderMappedTools(){
      if (!mappedList) return;
      mappedList.innerHTML = '';

      var kindOrder = { 'emulator': 1, 'legacy': 2, 'game_tool': 3 };
      currentTools.sort(function(a, b){
        var defA = findToolDef(a.id), defB = findToolDef(b.id);
        var ka = kindOrder[defA.kind] || 99, kb = kindOrder[defB.kind] || 99;
        if (ka !== kb) return ka - kb;
        return (defA.name || a.id).localeCompare(defB.name || b.id);
      });

      if (!currentTools.length) {
        mappedList.appendChild(el('p', 'rules fullw', 'No TAS tools mapped to this system yet.'));
      }

      currentTools.forEach(function(t, idx){
        var def = findToolDef(t.id);
        var card = el('div', 'sys-tool-card');

        var header = el('div', 'sys-tool-header');
        var title = el('div', 'sys-tool-title');
        title.appendChild(el('b', '', def.name || t.id));

        var kindName = def.kind === 'emulator' ? 'Emulator' : (def.kind === 'legacy' ? 'Legacy' : 'Game tool');
        title.appendChild(el('span', 'chip', kindName));
        if (def.formats || def.format) {
          title.appendChild(el('code', '', def.formats || def.format));
        }
        header.appendChild(title);

        var unmapBtn = el('button', 'btn quiet emu-unmap-btn', 'Remove from system');
        unmapBtn.type = 'button';
        unmapBtn.addEventListener('click', function(e){
          e.preventDefault();
          currentTools.splice(idx, 1);
          renderMappedTools();
          renderCatalogSelect();
        });
        header.appendChild(unmapBtn);
        card.appendChild(header);

        var fields = el('div', 'sys-tool-fields');

        // Version presets field (per-system)
        var verField = el('div', 'sys-tool-field');
        verField.appendChild(el('label', '', 'Version presets (comma-separated):'));
        var verInp = el('input', 'sys-text-input');
        verInp.type = 'text';
        verInp.value = (t.versions || []).join(', ');
        verInp.placeholder = 'e.g. 2.11.1, 2.9.1';
        verInp.addEventListener('input', function(){
          t.versions = verInp.value.split(',').map(function(s){ return s.trim(); }).filter(Boolean);
        });
        verField.appendChild(verInp);
        fields.appendChild(verField);

        // Cores field — ONLY for emulators with cores (has_cores: true)
        if (def.has_cores) {
          var coreField = el('div', 'sys-tool-field');
          coreField.appendChild(el('label', '', 'Cores for this system (comma-separated):'));
          var coreInp = el('input', 'sys-text-input');
          coreInp.type = 'text';
          coreInp.value = (t.cores || []).join(', ');
          coreInp.placeholder = 'e.g. NesHawk, QuickNES';
          coreInp.addEventListener('input', function(){
            t.cores = coreInp.value.split(',').map(function(s){ return s.trim(); }).filter(Boolean);
          });
          coreField.appendChild(coreInp);
          fields.appendChild(coreField);
        }

        card.appendChild(fields);
        mappedList.appendChild(card);
      });
    }

    // ---- Catalog Dropdown (Map another tool) ----
    function renderCatalogSelect(){
      if (!catalogSelect) return;
      catalogSelect.innerHTML = '';

      var defOpt = document.createElement('option');
      defOpt.value = '';
      defOpt.textContent = 'Select a tool to map…';
      catalogSelect.appendChild(defOpt);

      var mappedIds = currentTools.map(function(t){ return t.id; });
      var unmapped = catalog.catalog.filter(function(p){
        return mappedIds.indexOf(p.id) < 0 && p.kind !== 'game_tool';
      });

      var emus = unmapped.filter(function(p){ return p.kind === 'emulator'; });
      var leg = unmapped.filter(function(p){ return p.kind === 'legacy'; });

      function addGroup(label, list){
        if (!list.length) return;
        var grp = document.createElement('optgroup');
        grp.label = label;
        list.sort(function(a, b){ return a.name.localeCompare(b.name); });
        list.forEach(function(p){
          var opt = document.createElement('option');
          opt.value = p.id;
          opt.textContent = p.name + (p.formats ? ' (' + p.formats + ')' : '');
          grp.appendChild(opt);
        });
        catalogSelect.appendChild(grp);
      }

      addGroup('Emulator-based TAS tools', emus);
      addGroup('Legacy emulator formats', leg);
    }

    if (catalogAddBtn && catalogSelect) {
      catalogAddBtn.addEventListener('click', function(e){
        e.preventDefault();
        var selectedId = catalogSelect.value;
        if (!selectedId) return;

        var def = findToolDef(selectedId);
        if (def.kind === 'game_tool') return;
        var newTool = { id: selectedId, versions: [] };
        if (def.has_cores) newTool.cores = [];
        currentTools.push(newTool);
        renderMappedTools();
        renderCatalogSelect();
      });
    }

    // ---- Save Changes Handler ----
    if (saveBtn) {
      saveBtn.addEventListener('click', function(e){
        e.preventDefault();
        var reason = (reasonInp ? reasonInp.value : '').trim();
        if (reason.length < 8 || reason.length > 500) {
          if (msgEl) {
            msgEl.hidden = false;
            msgEl.className = 'rules fullw enc-bad';
            msgEl.textContent = 'Please enter a public reason between 8 and 500 characters.';
          }
          setMark(saveBtn, 'bad', 'Reason required');
          return;
        }

        var fd = new FormData();
        fd.append('system', skey);
        fd.append('quick_chips', JSON.stringify(currentQuickChips));
        fd.append('tools', JSON.stringify(currentTools));
        fd.append('reason', reason);

        post('/api/emulators/edit', fd, saveBtn).then(function(res){
          if (res.ok && res.j && res.j.ok) {
            if (msgEl) {
              msgEl.hidden = false;
              msgEl.className = 'rules fullw enc-good';
              msgEl.textContent = 'Saved changes for ' + skey + '. The site will rebuild with the updated presets shortly.';
            }
            setMark(saveBtn, 'ok', 'Saved');
            if (reasonInp) reasonInp.value = '';
          } else {
            var errMsg = (res.j && res.j.error) || 'Failed to save changes';
            if (msgEl) {
              msgEl.hidden = false;
              msgEl.className = 'rules fullw enc-bad';
              msgEl.textContent = errMsg;
            }
            setMark(saveBtn, 'bad', errMsg);
          }
        });
      });
    }

    // Initial render
    renderQuickChips();
    renderMappedTools();
    renderCatalogSelect();
  }
}
