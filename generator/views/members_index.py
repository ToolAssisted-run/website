"""View: members index (renders on import; see views/__init__)."""
from config import OUT
from model import author_stats, authors
from render import page, tpl

# ---- authors index ----
# sorted by author score, then run count, then name
members = [(uname, a, author_stats[uname])
           for uname, a in sorted(authors.items(),
                                  key=lambda kv: (-author_stats[kv[0]]['author'],
                                                  -author_stats[kv[0]]['runs'], kv[0]))]
body = tpl('members_index.html', members=members)
(OUT / 'authors' / 'index.html').write_text(page(
    'Members and TAS authors', body, '../', '', 'Members',
    seo={'path': 'authors/',
         'description': 'The members and authors of toolAssisted.run, with their runs, stars and roles.'}), encoding='utf-8')
