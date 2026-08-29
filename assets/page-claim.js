// toolAssisted.run — the claim page: file a claim on a held name, and, for
// the Steering Committee, attest an identity directly. These ids live only
// on /claim/, so the wiring lives here rather than in app.js.
import { mePromise, post, note } from './app.js';

  // ---- file a claim: anybody logged in, one at a time ----
  var claimForm = document.getElementById('f-claim');
  if (claimForm) {
    mePromise.then(function(d){
      if (d.unreachable) return;
      if (!d.loggedIn) {
        document.getElementById('claim-login').hidden = false;
        return;
      }
      document.getElementById('claim-form-wrap').hidden = false;
      var msg = document.getElementById('claim-msg');
      claimForm.addEventListener('submit', function(ev){
        ev.preventDefault();
        post('/api/claim/request', new FormData(claimForm),
             claimForm.querySelector('button')).then(function(res){
          if (res.ok && res.j.ok) {
            note(msg, 'Filed. The Steering Committee answers it, and you will hear ' +
                      'either way, by private message on the forum.', true);
            claimForm.hidden = true;
          } else note(msg, res.j.error || 'something went wrong', false);
        });
      });
    });
  }

  // ---- attest an identity (Steering Committee) ----
  var siteExperts = document.getElementById('siteexperts');
  if (siteExperts) {
    var SE = JSON.parse(siteExperts.textContent);
    mePromise.then(function(d){
      if (!d.loggedIn || SE.indexOf(d.user.toLowerCase()) < 0) return;
      var wrap = document.getElementById('attest-wrap');
      var form = document.getElementById('f-attest');
      var msg = document.getElementById('attest-msg');
      wrap.hidden = false;
      form.addEventListener('submit', function(ev){
        ev.preventDefault();
        post('/api/claim/attest', new FormData(form), form.querySelector('button'))
          .then(function(res){
            if (res.ok && res.j.ok) {
              note(msg, 'Attested: ' + res.j.identity + ' is now ' + res.j.member +
                        '. ' + (res.j.rename || ''), true);
              form.hidden = true;
            } else note(msg, res.j.error || 'something went wrong', false);
          });
      });
    });
  }

export const page = 'claim';
window.TARApp.page = page;
