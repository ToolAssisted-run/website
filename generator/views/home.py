"""View: the home page and the 404 page (renders on import; see views/__init__)."""
from config import OUT
from model import archived_at, is_pending, nlikes, nvisits, runs
from render import SITE_URL, page, tpl


def fresh_selection(all_runs, slots=8):
    """The last runs to arrive, newest first. No balancing: the shelf answers
    "what was just added", so a bulk import legitimately fills it while it is
    the newest thing here."""
    return sorted(all_runs, key=lambda r: (archived_at(r), r.get('submitted') or '',
                                           r['id']), reverse=True)[:slots]

def liked_selection(all_runs, slots=12):
    """The most-starred runs, ties to the newer arrival. Nothing with zero
    stars: an empty shelf says more than a shelf of unliked filler."""
    liked = [r for r in all_runs if nlikes(r) > 0]
    return sorted(liked, key=lambda r: (nlikes(r), archived_at(r),
                                        r.get('submitted') or '', r['id']),
                  reverse=True)[:slots]

def viewed_selection(all_runs, slots=12):
    """The most-visited runs, ties to the newer arrival; nothing unseen."""
    seen = [r for r in all_runs if nvisits(r) > 0]
    return sorted(seen, key=lambda r: (nvisits(r), archived_at(r),
                                       r.get('submitted') or '', r['id']),
                  reverse=True)[:slots]

body = tpl('home.html',
           pending_count=sum(1 for r in runs if is_pending(r)),
           fresh=fresh_selection(runs, slots=12),
           liked=liked_selection(runs),
           viewed=viewed_selection(runs))
(OUT / 'index.html').write_text(page(
    'toolAssisted.run · the open archive of tool-assisted speedruns', body, full_title=True,
    seo={'path': '',
         'description': ('An open community archive of tool-assisted speedruns, score attacks '
                         'and superplays: every run preserved the moment it arrives, ranked by '
                         'community verification, with encodes and movie files.'),
         'ld': [{'@context': 'https://schema.org', '@type': 'WebSite',
                 'name': 'toolAssisted.run', 'url': SITE_URL + '/',
                 'potentialAction': {'@type': 'SearchAction',
                                     'target': SITE_URL + '/browse/?q={search_term_string}',
                                     'query-input': 'required name=search_term_string'}},
                {'@context': 'https://schema.org', '@type': 'Organization',
                 'name': 'toolAssisted.run', 'url': SITE_URL + '/',
                 'logo': SITE_URL + '/assets/avatar-512-dark.png'}]}))

(OUT / '404.html').write_text(tpl('404.html'))
