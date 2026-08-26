// toolAssisted.run — a member's own profile page: the news 'load
// more' and the owner-only 'Import runs' button. Moved out of app.js:
// these ids (.newsmore, #selfimport) exist only on an author page.
import { api, mePromise } from './app.js';

  // ---- member news: ten shown, the rest a quiet click away ----
  document.querySelectorAll('.newsmore').forEach(function(b){
    b.addEventListener('click', function(){
      var rest = b.parentElement.querySelector('.newsrest');
      if (rest) rest.hidden = false;
      b.remove();
    });
  });

  // ---- self-service TASVideos import ----
  // profile: reveal the owner-only "Import runs" button
  var selfImportBtn = document.getElementById('selfimport');
  if (selfImportBtn && api) {
    mePromise.then(function(me){
      if (me.loggedIn && me.user &&
          me.user.toLowerCase() === (selfImportBtn.dataset.author || '').toLowerCase()) {
        selfImportBtn.hidden = false;
      }
    });
  }

export const page = 'member';
window.TARApp.page = page;
