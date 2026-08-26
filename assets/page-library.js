// toolAssisted.run — the game page, the group page, the games index:
// the small act zone experts and editors use to shape the library
// (rename, thumbnail, category, delete a game; move or delete a group;
// found a new group). Moved out of app.js: these ids exist only here.
import { api, mePromise, viewAsCoverage, note, noteBuilt, actionBtn, post }
  from './app.js';

  // ---- acts that live on the page they are about ----
  // A game page, a group page and the games index each carry one small zone
  // that only opens for the people who may use it. The archivist decides all
  // of this again; showing it here just keeps a form from being a trap.
  function armZone(dataId, zoneId, msgId, forms){
    var dataEl = document.getElementById(dataId);
    if (!dataEl) return;
    var zoneData = JSON.parse(dataEl.textContent);
    mePromise.then(function(d){
      if (d.unreachable || !d.loggedIn) return;
      var who = d.user.toLowerCase();
      var isExpertHere = viewAsCoverage(zoneData.experts || zoneData.siteExperts, who).indexOf(who) >= 0;
      // an editor shapes the library: zones that are library shape open for
      // them too, minus the forms marked as the experts' alone
      var isEditorHere = zoneData.editorZone
        && ((window.TAR || {}).editors || []).indexOf(who) >= 0;
      if (!isExpertHere && !isEditorHere) return;
      var zone = document.getElementById(zoneId);
      var msg = document.getElementById(msgId);
      zone.hidden = false;
      if (!isExpertHere && isEditorHere) {
        var zoneHeading = zone.querySelector('h2');
        if (zoneHeading && /Expert menu/.test(zoneHeading.textContent)) zoneHeading.textContent = 'Editor menu';
      }
      var zoneBtn = document.getElementById(dataId + '-btn');
      if (zoneBtn) zoneBtn.hidden = false;
      var groupMoveForm = document.getElementById('f-groupmove');
      if (groupMoveForm && zoneData.movable) {
        var moveList = groupMoveForm.querySelector('.gmovelist');
        var moveField = groupMoveForm.querySelector('[name=move]');
        var syncMoveField = function(){
          var picked = [];
          moveList.querySelectorAll('input:checked').forEach(function(c){ picked.push(c.value); });
          moveField.value = picked.join(' ');
        };
        var moveRows = zoneData.movable.map(function(g){
          var labelEl = document.createElement('label');
          labelEl.className = 'gmrow';
          var checkbox = document.createElement('input');
          checkbox.type = 'checkbox';
          checkbox.value = g.key;
          checkbox.addEventListener('change', syncMoveField);
          labelEl.appendChild(checkbox);
          labelEl.appendChild(document.createTextNode(' ' + g.title + ' (' + g.key + ')'
            + (g.group ? ' · now in ' + g.group : '')));
          labelEl.dataset.hay = (g.title + ' ' + g.key).toLowerCase();
          moveList.appendChild(labelEl);
          return labelEl;
        });
        var moveFilter = groupMoveForm.querySelector('.gmfilter');
        if (moveFilter) moveFilter.addEventListener('input', function(){
          var n = moveFilter.value.toLowerCase();
          moveRows.forEach(function(r){ r.hidden = !!n && r.dataset.hay.indexOf(n) === -1; });
        });
      }
      forms.forEach(function(spec){
        var form = document.getElementById(spec.id);
        if (!form) return;
        if (spec.expertOnly && !isExpertHere) {
          var fold = form.closest('details');
          (fold || form).hidden = true;
          return;
        }
        form.addEventListener('submit', function(ev){
          ev.preventDefault();
          if (spec.confirm && !window.confirm(spec.confirm)) return;
          post(spec.path, new FormData(form), actionBtn(form))
            .then(function(res){
              if (res.ok && res.j.ok) {
                noteBuilt(msg, spec.done(res.j), res.j.serial);
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
    {id: 'f-gamedelete', path: '/api/game/delete', expertOnly: true,
     confirm: 'Delete this game outright, WITH every run in it? ' +
              'This cannot be undone.',
     done: function(j){ return 'Deleted. ' + (j.runs_deleted && j.runs_deleted.length
       ? j.runs_deleted.length + ' run(s) deleted with it.' : 'It held no runs.'); }}]);
  armZone('groupactdata', 'groupacts', 'groupact-msg', [
    {id: 'f-groupmove', path: '/api/group/edit',
     done: function(j){ return 'Moved. This group now holds ' + j.games.length +
       ' game' + (j.games.length === 1 ? '' : 's') + '.'; }},
    {id: 'f-groupdelete', path: '/api/group/delete',
     confirm: 'Delete this group outright? Its games become ungrouped. ' +
              'This cannot be undone.',
     done: function(j){ return 'Deleted; ' + j.released.length +
       ' game(s) are ungrouped.'; }}]);
  armZone('gamesactdata', 'gamesacts', 'gamesact-msg', [
    {id: 'f-newgroup', path: '/api/group/create',
     done: function(j){ return 'The ' + j.group + ' group exists.'; }}]);

export const page = 'library';
window.TARApp.page = page;
