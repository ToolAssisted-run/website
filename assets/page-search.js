// toolAssisted.run — dedicated search page client script.
import { escapeHtml, rel, versionQuery, createPaginator, scoreString } from './app.js';

var searchIndex = null;
var fetchPromise = null;

function loadIndex() {
  if (searchIndex) return Promise.resolve(searchIndex);
  if (!fetchPromise) {
    var url = (rel || '') + 'assets/search-index.json' + (versionQuery || '');
    fetchPromise = fetch(url)
      .then(function(r){
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function(data){
        searchIndex = data;
        return data;
      })
      .catch(function(err){
        fetchPromise = null;
        throw err;
      });
  }
  return fetchPromise;
}

function querySearchIndex(data, rawQuery) {
  var needle = (rawQuery || '').trim().toLowerCase();
  if (!needle) {
    return { games: [], groups: [], systems: [], authors: [], runs: [] };
  }

  // 1. Systems: match title or slug, sorted by match strength
  var systems = (data.systems || []).map(function(sys){
    var sName = scoreString(sys.n, needle);
    var sKey = scoreString(sys.k, needle) * 1.2;
    return { item: sys, score: Math.max(sName, sKey) };
  }).filter(function(x){ return x.score > 0; })
    .sort(function(a, b){ return b.score - a.score || a.item.n.localeCompare(b.item.n); })
    .map(function(x){ return x.item; });

  // 2. Game groups: match title or title of any game in group, sorted by match strength
  var groups = (data.groups || []).map(function(gr){
    var sTitle = scoreString(gr.t, needle);
    var sMembers = 0;
    if (gr.gt && gr.gt.length) {
      for (var i = 0; i < gr.gt.length; i++) {
        var sm = scoreString(gr.gt[i], needle);
        if (sm > sMembers) sMembers = sm;
      }
    }
    var sKey = scoreString(gr.k, needle);
    return { item: gr, score: Math.max(sTitle, sMembers * 0.75, sKey * 0.8) };
  }).filter(function(x){ return x.score > 0; })
    .sort(function(a, b){ return b.score - a.score || (b.item.gc || b.item.c || 0) - (a.item.gc || a.item.c || 0) || a.item.t.localeCompare(b.item.t); })
    .map(function(x){ return x.item; });

  // 3. Games: match title and group title, sorted by match strength
  var games = (data.games || []).map(function(g){
    var sTitle = scoreString(g.t, needle);
    var sGroups = 0;
    if (g.g && g.g.length) {
      for (var i = 0; i < g.g.length; i++) {
        var sg = scoreString(g.g[i], needle);
        if (sg > sGroups) sGroups = sg;
      }
    }
    var sKey = scoreString(g.k, needle);
    return { item: g, score: Math.max(sTitle, sGroups * 0.75, sKey * 0.8) };
  }).filter(function(x){ return x.score > 0; })
    .sort(function(a, b){ return b.score - a.score || a.item.t.localeCompare(b.item.t); })
    .map(function(x){ return x.item; });

  // 4. Authors: match username only, sorted by match strength
  var authors = (data.authors || []).map(function(a){
    return { item: a, score: scoreString(a.u, needle) };
  }).filter(function(x){ return x.score > 0; })
    .sort(function(a, b){ return b.score - a.score || (b.item.r || 0) - (a.item.r || 0) || a.item.u.localeCompare(b.item.u); })
    .map(function(x){ return x.item; });

  // 5. Runs: match title, cat, author username, run ID, metric, sorted by match strength
  var runs = (data.runs || []).map(function(r){
    var sTitle = scoreString(r.t, needle);
    var sCat = scoreString(r.c, needle);
    var sAuthors = 0;
    if (r.a && r.a.length) {
      for (var i = 0; i < r.a.length; i++) {
        var sa = scoreString(r.a[i], needle);
        if (sa > sAuthors) sAuthors = sa;
      }
    }
    var sId = (r.id && r.id.toLowerCase() === needle) ? 1000 : (r.id && r.id.toLowerCase().indexOf(needle) === 0 ? 600 : 0);
    return { item: r, score: Math.max(sTitle, sCat * 0.8, sAuthors * 0.85, sId) };
  }).filter(function(x){ return x.score > 0; })
    .sort(function(a, b){ return b.score - a.score || (b.item.stars || 0) - (a.item.stars || 0) || String(b.item.d).localeCompare(String(a.item.d)); })
    .map(function(x){ return x.item; });

  return {
    games: games,
    groups: groups,
    systems: systems,
    authors: authors,
    runs: runs
  };
}

function chipHtml(state){
  return {
    verified: '<span class="chip verchip">Verified</span>',
    unclassified: '<span class="chip unclchip">Unclassified</span>',
    pending: '<span class="chip pendchip">Pending</span>'
  }[state] || '';
}

function initSearchPage() {
  if (typeof document === 'undefined') return;
  var input = document.getElementById('search-input');
  if (!input) return;
  var tabsContainer = document.getElementById('search-tabs');
  var loadingEl = document.getElementById('search-loading');
  var emptyEl = document.getElementById('search-empty');
  var noResultsEl = document.getElementById('search-no-results');
  var resultsContainer = document.getElementById('search-results');

  var secSystems = document.getElementById('sec-systems');
  var secGroups = document.getElementById('sec-groups');
  var secGames = document.getElementById('sec-games');
  var secAuthors = document.getElementById('sec-authors');
  var secRuns = document.getElementById('sec-runs');

  var resSystems = document.getElementById('res-systems');
  var resGroups = document.getElementById('res-groups');
  var resGames = document.getElementById('res-games');
  var resAuthors = document.getElementById('res-authors');
  var resRuns = document.getElementById('res-runs');
  var runsBrowseLink = document.getElementById('runs-browse-link');

  var pagBoxSystems = document.getElementById('pag-systems');
  var pagBoxGroups = document.getElementById('pag-groups');
  var pagBoxGames = document.getElementById('pag-games');
  var pagBoxAuthors = document.getElementById('pag-authors');
  var pagBoxRuns = document.getElementById('pag-runs');

  var searchStr = typeof location !== 'undefined' ? location.search : '';
  var params = new URLSearchParams(searchStr);
  var initialQ = params.get('q') || '';
  var activeTab = params.get('tab') || 'all';

  if (input) input.value = initialQ;

  function syncUrl(q, tab) {
    try {
      if (typeof location !== 'undefined' && typeof history !== 'undefined' && history.replaceState) {
        var u = new URL(location.href);
        if (q) u.searchParams.set('q', q);
        else u.searchParams.delete('q');
        if (tab && tab !== 'all') u.searchParams.set('tab', tab);
        else u.searchParams.delete('tab');
        history.replaceState(null, '', u.toString());
      }
    } catch(e){}
  }

  function setTab(tab) {
    activeTab = tab || 'all';
    document.querySelectorAll('.stab').forEach(function(btn){
      var match = btn.dataset.tab === activeTab;
      btn.classList.toggle('on', match);
      btn.setAttribute('aria-selected', match ? 'true' : 'false');
    });
    renderSections();
    syncUrl(input ? input.value.trim() : '', activeTab);
  }

  var currentResults = { games: [], groups: [], systems: [], authors: [], runs: [] };

  function renderSystems(page, size) {
    if (!resSystems) return;
    var start = (page - 1) * size;
    var slice = currentResults.systems.slice(start, start + size);
    resSystems.innerHTML = slice.map(function(sys){
      return '<a class="search-card search-card-system" href="' + (rel || '') + 'systems/' + escapeHtml(sys.k) + '/">' +
        '<div class="search-card-title">' + escapeHtml(sys.n) + '</div>' +
        '<div class="search-card-sub">' + (sys.gc || 0) + ' game' + (sys.gc === 1 ? '' : 's') + ' · ' + (sys.rc || 0) + ' run' + (sys.rc === 1 ? '' : 's') + '</div>' +
        '</a>';
    }).join('');
  }

  function renderGroups(page, size) {
    if (!resGroups) return;
    var start = (page - 1) * size;
    var slice = currentResults.groups.slice(start, start + size);
    resGroups.innerHTML = slice.map(function(gr){
      return '<a class="search-card search-card-group" href="' + (rel || '') + 'groups/' + escapeHtml(gr.k) + '/">' +
        '<div class="search-card-title">' + escapeHtml(gr.t) + '</div>' +
        '<div class="search-card-sub">' + (gr.gc || gr.c || 0) + ' game' + ((gr.gc || gr.c) === 1 ? '' : 's') + ' · ' + (gr.rc || 0) + ' run' + (gr.rc === 1 ? '' : 's') + '</div>' +
        '</a>';
    }).join('');
  }

  function renderGames(page, size) {
    if (!resGames) return;
    var start = (page - 1) * size;
    var slice = currentResults.games.slice(start, start + size);
    resGames.innerHTML = slice.map(function(g){
      var grpHtml = (g.g && g.g.length)
        ? '<div class="search-card-partof" title="part of ' + escapeHtml(g.g[0]) + '">part of ' + escapeHtml(g.g[0]) + '</div>'
        : '';
      return '<a class="search-card search-card-game" href="' + (rel || '') + 'games/' + escapeHtml(g.k) + '/">' +
        '<div class="search-card-title">' + escapeHtml(g.t) + '</div>' +
        grpHtml +
        '<div class="search-card-sys"><span class="chip" title="' + escapeHtml(g.sn) + '">' + escapeHtml(g.sn) + '</span></div>' +
        '</a>';
    }).join('');
  }

  function renderAuthors(page, size) {
    if (!resAuthors) return;
    var start = (page - 1) * size;
    var slice = currentResults.authors.slice(start, start + size);
    resAuthors.innerHTML = slice.map(function(a){
      return '<a class="search-card search-card-author" href="' + (rel || '') + 'authors/' + escapeHtml(a.p) + '/">' +
        '<div class="search-card-title">@' + escapeHtml(a.u) + '</div>' +
        '<div class="search-card-sub">' + a.r + ' run' + (a.r === 1 ? '' : 's') + (a.s > 0 ? ' · ★' + a.s : '') + '</div>' +
        '</a>';
    }).join('');
  }

  function renderRuns(page, size) {
    if (!resRuns) return;
    var start = (page - 1) * size;
    var slice = currentResults.runs.slice(start, start + size);
    resRuns.innerHTML = slice.map(function(r){
      return '<tr onclick="location=\'' + (rel || '') + 'runs/' + escapeHtml(r.id) + '/\'">' +
        '<td><b>' + escapeHtml(r.t) + '</b><span class="bcat">' + escapeHtml(r.c) + '</span></td>' +
        '<td class="bsys">' + escapeHtml(r.sn) + '</td>' +
        '<td>' + escapeHtml(r.a.join(', ')) + '</td>' +
        '<td class="bsys">' + escapeHtml(r.m) + '</td>' +
        '<td class="num">' + r.res + '</td>' +
        '<td class="num"><span class="starglyph">★</span>' + r.stars + '</td>' +
        '<td class="bsys bdate">' + escapeHtml(r.d) + '</td>' +
        '<td>' + chipHtml(r.st) + '</td></tr>';
    }).join('');
  }

  var pagSystems = createPaginator({
    key: 'search-systems',
    container: pagBoxSystems,
    sizes: [12, 24, 36, 48],
    defaultSize: 12,
    itemLabel: 'system',
    onChange: function(p, s){ renderSystems(p, s); }
  });

  var pagGroups = createPaginator({
    key: 'search-groups',
    container: pagBoxGroups,
    sizes: [12, 24, 36, 48],
    defaultSize: 12,
    itemLabel: 'game group',
    onChange: function(p, s){ renderGroups(p, s); }
  });

  var pagGames = createPaginator({
    key: 'search-games',
    container: pagBoxGames,
    sizes: [12, 24, 36, 48],
    defaultSize: 12,
    itemLabel: 'game',
    onChange: function(p, s){ renderGames(p, s); }
  });

  var pagAuthors = createPaginator({
    key: 'search-authors',
    container: pagBoxAuthors,
    sizes: [12, 24, 36, 48],
    defaultSize: 12,
    itemLabel: 'author',
    onChange: function(p, s){ renderAuthors(p, s); }
  });

  var pagRuns = createPaginator({
    key: 'search-runs',
    container: pagBoxRuns,
    sizes: [10, 20, 30, 40, 50],
    defaultSize: 10,
    itemLabel: 'run',
    onChange: function(p, s){ renderRuns(p, s); }
  });

  function renderSections() {
    var q = input ? input.value.trim() : '';
    if (!q) {
      emptyEl.hidden = false;
      noResultsEl.hidden = true;
      resultsContainer.hidden = true;
      return;
    }

    emptyEl.hidden = true;
    var totalMatches = currentResults.games.length + currentResults.groups.length +
                       currentResults.systems.length + currentResults.authors.length +
                       currentResults.runs.length;

    if (totalMatches === 0) {
      noResultsEl.hidden = false;
      noResultsEl.textContent = 'No results found for "' + q + '".';
      resultsContainer.hidden = true;
      return;
    }

    noResultsEl.hidden = true;
    resultsContainer.hidden = false;

    // Visibility per tab
    var showAll = activeTab === 'all';
    secSystems.hidden = !( (showAll || activeTab === 'systems') && currentResults.systems.length > 0 );
    secGroups.hidden = !( (showAll || activeTab === 'groups') && currentResults.groups.length > 0 );
    secGames.hidden = !( (showAll || activeTab === 'games') && currentResults.games.length > 0 );
    secAuthors.hidden = !( (showAll || activeTab === 'authors') && currentResults.authors.length > 0 );
    secRuns.hidden = !( (showAll || activeTab === 'runs') && currentResults.runs.length > 0 );
  }

  function executeSearch() {
    var q = input ? input.value.trim() : '';
    if (!q) {
      currentResults = { games: [], groups: [], systems: [], authors: [], runs: [] };
      updateCounts(0, 0, 0, 0, 0);
      pagSystems.setTotal(0); pagSystems.setPage(1);
      pagGroups.setTotal(0); pagGroups.setPage(1);
      pagGames.setTotal(0); pagGames.setPage(1);
      pagAuthors.setTotal(0); pagAuthors.setPage(1);
      pagRuns.setTotal(0); pagRuns.setPage(1);
      renderSections();
      syncUrl('', activeTab);
      return;
    }

    loadingEl.hidden = false;
    loadIndex().then(function(data){
      loadingEl.hidden = true;
      currentResults = querySearchIndex(data, q);

      updateCounts(
        currentResults.games.length,
        currentResults.groups.length,
        currentResults.systems.length,
        currentResults.authors.length,
        currentResults.runs.length
      );

      if (runsBrowseLink) {
        runsBrowseLink.href = (rel || '') + 'browse/?q=' + encodeURIComponent(q);
      }

      pagSystems.setTotal(currentResults.systems.length);
      pagSystems.setPage(1);
      pagGroups.setTotal(currentResults.groups.length);
      pagGroups.setPage(1);
      pagGames.setTotal(currentResults.games.length);
      pagGames.setPage(1);
      pagAuthors.setTotal(currentResults.authors.length);
      pagAuthors.setPage(1);
      pagRuns.setTotal(currentResults.runs.length);
      pagRuns.setPage(1);

      renderSections();
      syncUrl(q, activeTab);
    }).catch(function(err){
      loadingEl.hidden = true;
      noResultsEl.hidden = false;
      noResultsEl.textContent = 'Failed to load search data. Please try again.';
    });
  }

  function updateCounts(ngames, ngroups, nsystems, nauthors, nruns) {
    var total = ngames + ngroups + nsystems + nauthors + nruns;
    var elAll = document.getElementById('scnt-all');
    var elGames = document.getElementById('scnt-games');
    var elGroups = document.getElementById('scnt-groups');
    var elSystems = document.getElementById('scnt-systems');
    var elAuthors = document.getElementById('scnt-authors');
    var elRuns = document.getElementById('scnt-runs');

    if (elAll) elAll.textContent = total;
    if (elGames) elGames.textContent = ngames;
    if (elGroups) elGroups.textContent = ngroups;
    if (elSystems) elSystems.textContent = nsystems;
    if (elAuthors) elAuthors.textContent = nauthors;
    if (elRuns) elRuns.textContent = nruns;

    var hSystems = document.getElementById('hcnt-systems');
    var hGroups = document.getElementById('hcnt-groups');
    var hGames = document.getElementById('hcnt-games');
    var hAuthors = document.getElementById('hcnt-authors');
    var hRuns = document.getElementById('hcnt-runs');

    if (hSystems) hSystems.textContent = '(' + nsystems + ')';
    if (hGroups) hGroups.textContent = '(' + ngroups + ')';
    if (hGames) hGames.textContent = '(' + ngames + ')';
    if (hAuthors) hAuthors.textContent = '(' + nauthors + ')';
    if (hRuns) hRuns.textContent = '(' + nruns + ')';
  }

  if (tabsContainer) {
    tabsContainer.addEventListener('click', function(ev){
      var btn = ev.target.closest('.stab');
      if (btn && btn.dataset.tab) {
        setTab(btn.dataset.tab);
      }
    });
  }

  if (input) {
    input.addEventListener('input', executeSearch);
  }

  // Pre-load on focus
  if (input) {
    input.addEventListener('focus', function(){
      loadIndex().catch(function(){});
    });
  }

  setTab(activeTab);
  if (initialQ) {
    executeSearch();
  }
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initSearchPage);
  } else {
    initSearchPage();
  }
}

export { loadIndex, querySearchIndex };
