"""View: the home page and the 404 page (renders on import; see views/__init__)."""
import hashlib

from config import OUT
from model import archived_at, nlikes, nvisits, runs
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

def random_selection(all_runs, shown, pool=24, quiet_first=True):
    """The shelf for the runs the other shelves never reach.

    Most liked and Most viewed can only show what already has attention, so
    a run nobody has starred or opened yet has nowhere to appear once it
    falls off Freshly archived. This shelf is where it gets its chance: the
    runs with no stars and no visits come first, then the rest.

    The order written here is a hash of the run id, not chance. A build has
    to give the same bytes for the same archive (every suite leans on that,
    and a shelf that reshuffled on every push would churn the page for
    nothing), so the dice are the reader's: the browser shuffles this pool
    on every visit and keeps the first few (assets/page-home.js). Without
    scripting the pool still stands, just in a fixed order.
    """
    seen = {r['id'] for r in shown}
    left = [r for r in all_runs if r['id'] not in seen]
    quiet = [r for r in left if not nlikes(r) and not nvisits(r)]
    loud = [r for r in left if nlikes(r) or nvisits(r)]
    def scramble(rs):
        return sorted(rs, key=lambda r: hashlib.sha1(r['id'].encode()).hexdigest())
    return (scramble(quiet) + scramble(loud) if quiet_first
            else scramble(left))[:pool]

body = tpl('home.html',
           total_likes=sum(nlikes(r) for r in runs),
           total_views=sum(nvisits(r) for r in runs),
           fresh=fresh_selection(runs, slots=12),
           liked=liked_selection(runs),
           viewed=viewed_selection(runs),
           picks=random_selection(runs, fresh_selection(runs, slots=12)
                                  + liked_selection(runs) + viewed_selection(runs)))
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
                 'logo': SITE_URL + '/assets/avatar-512-dark.png'}]},
     scripts=['page-home.js']))

(OUT / '404.html').write_text(tpl('404.html'))
