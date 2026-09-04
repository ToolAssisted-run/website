const DESKTOP_QUERY = '(min-width: 901px)';
const PORTRAIT_QUERY = '(orientation: portrait)';
const MAX_DELTA = 200;
const MIN_WIDTH = 220;

function initRunVideoFloat() {
  const stage = document.getElementById('run-stage-player');
  const player = document.getElementById('run-player');
  const frame = player && player.querySelector('iframe');
  if (!stage || !player || !frame) return;

  const placeholder = document.createComment('run player position');
  const shell = document.createElement('div');
  const bar = document.createElement('div');
  const title = document.createElement('span');
  const close = document.createElement('button');
  const grip = document.createElement('span');
  const defaults = {width: 0, height: 0, right: 18, bottom: 18};
  let floating = false;
  let activated = false;
  let closed = false;
  let provider = providerFor(frame.src);
  const activationShield = document.createElement('button');

  shell.className = 'run-video-float';
  shell.hidden = true;
  bar.className = 'run-video-float-bar';
  title.className = 'run-video-float-title';
  title.textContent = 'Run encode';
  close.className = 'run-video-float-close';
  close.type = 'button';
  close.setAttribute('aria-label', 'Close floating video');
  close.textContent = '×';
  grip.className = 'run-video-float-grip';
  bar.append(title, close);
  shell.append(bar, grip);
  document.body.appendChild(shell);
  player.parentNode.insertBefore(placeholder, player);
  activationShield.type = 'button';
  activationShield.className = 'run-video-activate-shield';
  activationShield.setAttribute('aria-label', 'Start video');
  player.appendChild(activationShield);

  function isDesktop() {
    return window.matchMedia(DESKTOP_QUERY).matches;
  }

  function isPortrait() {
    return window.matchMedia(PORTRAIT_QUERY).matches;
  }

  function isLandscapeMobile() {
    return !isDesktop() && !isPortrait();
  }

  function defaultGeometry() {
    const aside = document.querySelector('.cols > aside');
    const width = aside ? aside.getBoundingClientRect().width : 290;
    defaults.width = Math.max(MIN_WIDTH, Math.round(width));
    defaults.height = Math.round(defaults.width * 9 / 16) + 30;
  }

  function resetGeometry() {
    defaultGeometry();
    shell.style.width = defaults.width + 'px';
    shell.style.height = defaults.height + 'px';
    shell.style.left = '';
    shell.style.top = '';
    shell.style.right = defaults.right + 'px';
    shell.style.bottom = defaults.bottom + 'px';
  }

  function pause() {
    if (provider === 'youtube') {
      frame.contentWindow.postMessage(JSON.stringify({event: 'command', func: 'pauseVideo', args: []}), '*');
    } else if (provider === 'vimeo') {
      frame.contentWindow.postMessage({method: 'pause'}, '*');
    } else if (provider === 'dailymotion') {
      frame.contentWindow.postMessage(JSON.stringify({method: 'pause'}), '*');
    }
  }

  function play() {
    if (provider === 'youtube') {
      frame.contentWindow.postMessage(JSON.stringify({event: 'command', func: 'playVideo', args: []}), '*');
    } else if (provider === 'vimeo') {
      frame.contentWindow.postMessage({method: 'play'}, '*');
    } else if (provider === 'dailymotion') {
      frame.contentWindow.postMessage(JSON.stringify({method: 'play'}), '*');
    }
  }

  function showFloat() {
    if (isLandscapeMobile()) {
      shell.hidden = true;
      return;
    }
    if (!floating) {
      floating = true;
      shell.hidden = false;
      shell.insertBefore(player, grip);
      shell.classList.add('is-floating');
    }
    shell.hidden = false;
  }

  function restoreInline() {
    if (!floating) return;
    placeholder.parentNode.insertBefore(player, placeholder.nextSibling);
    floating = false;
    shell.hidden = true;
    shell.classList.remove('is-floating');
  }

  function onPlay() {
    const wasClosed = closed;
    if (closed) {
      closed = false;
      activated = true;
      resetGeometry();
    }
    activated = true;
    if (wasClosed || !isStageVisible()) showFloat();
  }

  function isStageVisible() {
    const rect = stage.getBoundingClientRect();
    return rect.bottom > 0 && rect.top < window.innerHeight;
  }

  function providerFor(url) {
    if (/youtube(-nocookie)?\.com|youtu\.be/.test(url)) return 'youtube';
    if (/vimeo\.com/.test(url)) return 'vimeo';
    if (/dailymotion\.com/.test(url)) return 'dailymotion';
    return '';
  }

  function messageIsPlay(data) {
    if (!data) return false;
    if (typeof data === 'string') {
      try { data = JSON.parse(data); } catch (e) { return /playing|play/i.test(data); }
    }
    return data.event === 'play' || data.event === 'playing' ||
      data.info === 1 || data.method === 'playProgress' ||
      data.playerState === 1 || data.event === 'video_play';
  }

  function enableProviderEvents() {
    const url = new URL(frame.src);
    if (provider === 'youtube') {
      url.searchParams.set('enablejsapi', '1');
      url.searchParams.set('origin', window.location.origin);
    } else if (provider === 'vimeo') {
      url.searchParams.set('api', '1');
    }
    frame.src = url.toString();
  }

  function activateFallback() {
    if (!activated) {
      activated = true;
      closed = false;
    }
  }

  function maybeFloat() {
    if (!activated || closed) return;
    if (isLandscapeMobile()) {
      shell.hidden = true;
    } else if (!isStageVisible()) {
      showFloat();
    } else if (floating && !closed) {
      restoreInline();
    }
  }

  function resizeStart(ev, edges) {
    if (!isDesktop() || !floating) return;
    ev.preventDefault();
    ev.stopPropagation();
    const rect = shell.getBoundingClientRect();
    const startX = ev.clientX;
    const startY = ev.clientY;
    const startWidth = rect.width;
    const startHeight = rect.height;
    const startLeft = rect.left;
    const startTop = rect.top;
    function move(moveEv) {
      const dx = moveEv.clientX - startX;
      const dy = moveEv.clientY - startY;
      const widthDelta = edges.left ? -dx : edges.right ? dx : 0;
      const heightDelta = edges.top ? -dy : edges.bottom ? dy : 0;
      const width = Math.max(MIN_WIDTH, Math.min(defaults.width + MAX_DELTA,
        Math.max(defaults.width - MAX_DELTA, startWidth + widthDelta)));
      const height = Math.max(150, Math.min(defaults.height + MAX_DELTA,
        Math.max(defaults.height - MAX_DELTA, startHeight + heightDelta)));
      shell.style.width = Math.round(width) + 'px';
      shell.style.height = Math.round(height) + 'px';
      if (edges.left) shell.style.left = Math.round(startLeft + dx) + 'px';
      if (edges.top) shell.style.top = Math.round(startTop + dy) + 'px';
      if (edges.left || edges.top) {
        shell.style.right = '';
        shell.style.bottom = '';
      }
    }
    function stop() {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', stop);
    }
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', stop, {once: true});
  }

  function dragStart(ev) {
    if (!isDesktop() || !floating || ev.target === close) return;
    ev.preventDefault();
    const rect = shell.getBoundingClientRect();
    const offsetX = ev.clientX - rect.left;
    const offsetY = ev.clientY - rect.top;
    function move(moveEv) {
      const left = Math.max(0, Math.min(window.innerWidth - rect.width,
        moveEv.clientX - offsetX));
      const top = Math.max(0, Math.min(window.innerHeight - rect.height,
        moveEv.clientY - offsetY));
      shell.style.left = Math.round(left) + 'px';
      shell.style.top = Math.round(top) + 'px';
      shell.style.right = '';
      shell.style.bottom = '';
    }
    function stop() {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', stop);
    }
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', stop, {once: true});
  }

  close.addEventListener('click', function() {
    pause();
    closed = true;
    restoreInline();
    shell.hidden = true;
  });
  shell.addEventListener('pointerdown', function(ev) {
    if (ev.target === close || ev.target === bar || bar.contains(ev.target)) return;
    const rect = shell.getBoundingClientRect();
    const edge = 10;
    const edges = {
      left: ev.clientX - rect.left <= edge,
      right: rect.right - ev.clientX <= edge,
      top: ev.clientY - rect.top <= edge,
      bottom: rect.bottom - ev.clientY <= edge
    };
    if (edges.left || edges.right || edges.top || edges.bottom) {
      resizeStart(ev, edges);
    } else {
      dragStart(ev);
    }
  });
  enableProviderEvents();
  player.addEventListener('pointerdown', activateFallback);
  activationShield.addEventListener('pointerdown', function() {
    activateFallback();
    play();
    activationShield.remove();
  });
  window.addEventListener('message', function(ev) {
    if (messageIsPlay(ev.data)) onPlay();
  });
  window.addEventListener('scroll', maybeFloat, {passive: true});
  window.addEventListener('resize', function() {
    if (isLandscapeMobile()) {
      shell.hidden = true;
    } else if (floating) {
      shell.hidden = false;
    }
    if (!floating) defaultGeometry();
  });
  window.matchMedia(PORTRAIT_QUERY).addEventListener('change', maybeFloat);
  defaultGeometry();
  resetGeometry();
}

export { initRunVideoFloat };
