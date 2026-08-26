// toolAssisted.run — the landing page: the drag-to-scroll shelves and
// the Bluesky feed in the News & Events column. Moved out of app.js:
// these ids (.hwrap, #bskyfeed) exist only on the home page.
import { escapeHtml } from './app.js';

  // ---- shelves: one row, dragged sideways (home page) ----
  // native touch panning is left alone; the mouse gets click-hold-drag,
  // and the faint arrows appear only on the side with more to see
  document.querySelectorAll('.hwrap > .hrow').forEach(function(row){
    var wrap = row.parentElement;
    function makeArrow(dir){
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'harr ' + (dir < 0 ? 'left' : 'right');
      b.textContent = dir < 0 ? '\u2039' : '\u203a';
      b.setAttribute('aria-label', dir < 0 ? 'scroll left' : 'scroll right');
      b.addEventListener('click', function(){
        row.scrollBy({left: dir * row.clientWidth * 0.8, behavior: 'smooth'});
      });
      wrap.appendChild(b);
      return b;
    }
    var left = makeArrow(-1), right = makeArrow(1);
    function paint(){
      var max = row.scrollWidth - row.clientWidth - 1;
      left.hidden = row.scrollLeft <= 0;
      right.hidden = row.scrollLeft >= max;
    }
    row.addEventListener('scroll', paint, {passive: true});
    window.addEventListener('resize', paint);
    paint();
    var down = false, moved = false, startX = 0, startScroll = 0;
    row.addEventListener('pointerdown', function(e){
      if (e.pointerType !== 'mouse' || e.button !== 0) return;
      down = true; moved = false; startX = e.clientX; startScroll = row.scrollLeft;
    });
    row.addEventListener('pointermove', function(e){
      if (!down) return;
      var dx = e.clientX - startX;
      if (!moved && Math.abs(dx) > 4) {
        moved = true;
        row.classList.add('dragging');
        // capture only once it IS a drag: capturing on pointerdown
        // retargets the click at the row and kills the card links
        try { row.setPointerCapture(e.pointerId); } catch (err) {}
      }
      if (moved) row.scrollLeft = startScroll - dx;
    });
    function lift(){
      if (!down) return;
      down = false;
      setTimeout(function(){ moved = false; row.classList.remove('dragging'); }, 0);
    }
    row.addEventListener('pointerup', lift);
    row.addEventListener('pointercancel', lift);
    row.addEventListener('click', function(e){
      if (moved) { e.preventDefault(); e.stopPropagation(); }
    }, true);
  });

  // one Bluesky post, rendered in our own markup
  function bskyPostHtml(item, profileUrl, linkify, since){
    var postView = item.post || {}, record = postView.record || {};
    var rkey = String(postView.uri || '').split('/').pop();
    var url = profileUrl + '/post/' + rkey;
    var card = '';
    var embed = postView.embed || {};
    if (embed.external && embed.external.uri) {
      card = '<a class="bcard" href="' + escapeHtml(embed.external.uri) + '" rel="noopener">'
        + (embed.external.thumb ? '<img src="' + escapeHtml(embed.external.thumb) + '" alt="" loading="lazy">' : '')
        + '<span><b>' + escapeHtml(embed.external.title || embed.external.uri) + '</b>'
        + escapeHtml((embed.external.description || '').slice(0, 90)) + '</span></a>';
    } else if (embed.images && embed.images.length) {
      card = '<a class="bcard" href="' + escapeHtml(url) + '" rel="noopener">'
        + '<img src="' + escapeHtml(embed.images[0].thumb) + '" alt="'
        + escapeHtml(embed.images[0].alt || '') + '" loading="lazy">'
        + '<span><b>Image</b>view on Bluesky</span></a>';
    }
    return '<article class="bpost"><div class="btext">' + linkify(record.text || '')
      + '</div>' + card + '<div class="bmeta"><a href="' + escapeHtml(url) + '" rel="noopener">'
      + since(record.createdAt || postView.indexedAt) + '</a>'
      + '<span>♥ ' + (postView.likeCount || 0) + '</span>'
      + '<span>↻ ' + (postView.repostCount || 0) + '</span></div></article>';
  }

  // ---- Bluesky feed in the News & Events column ----
  // The AT Protocol serves public posts as JSON to anybody (CORS open, no
  // token, no cookies), so the panel renders in our own markup instead of
  // handing the reader to a third-party widget.

  // the Bluesky feed box: fetch the public feed and draw it
  var feedBox = document.getElementById('bskyfeed');
  if (feedBox) {
    var handle = feedBox.dataset.handle || '';
    var profileUrl = 'https://bsky.app/profile/' + handle;
    var since = function(iso){
      var s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
      if (s < 3600) return Math.floor(s / 60) + 'm ago';
      if (s < 86400) return Math.floor(s / 3600) + 'h ago';
      if (s < 2592000) return Math.floor(s / 86400) + 'd ago';
      return new Date(iso).toISOString().slice(0, 10);
    };
    var linkify = function(text){
      return escapeHtml(text).replace(/(https?:\/\/[^\s<]+)/g,
        function(u){ return '<a href="' + u + '" rel="noopener">' + u + '</a>'; });
    };
    var showFeedError = function(){
      feedBox.innerHTML = '<p class="emptynote">Could not reach Bluesky just now. ' +
        'Read the latest at <a href="' + profileUrl + '">@' + escapeHtml(handle) + '</a>.</p>';
    };
    fetch('https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor='
          + encodeURIComponent(handle) + '&limit=10&filter=posts_no_replies')
      .then(function(r){ return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function(d){
        var items = (d && d.feed) || [];
        if (!items.length) {
          feedBox.innerHTML = '<p class="emptynote">No posts yet. Follow ' +
            '<a href="' + profileUrl + '">@' + escapeHtml(handle) + '</a> for announcements.</p>';
          return;
        }
        feedBox.innerHTML = items.map(function(item){
          return bskyPostHtml(item, profileUrl, linkify, since);
        }).join('');
      })
      .catch(showFeedError);
  }

export const page = 'home';
window.TARApp.page = page;
