"""View: members index (renders on import; see views/__init__)."""
import datetime
import html
import os
import json
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.parse
from config import (
    OUT,
)
from model import (
    ROLES_NOW,
    author_stats,
    authors,
    profile_slug,
)
from render import (
    esc,
    page,
    role_badges,
)

# ---- authors index ----
arows = []
for uname, a in sorted(authors.items(),
                       key=lambda kv: (-author_stats[kv[0]]['author'],
                                       -author_stats[kv[0]]['runs'], kv[0])):
    st_ = author_stats[uname]
    arows.append(f'''<tr onclick="if(!event.target.closest('a'))location='{esc(profile_slug(uname))}/'">
<td><b>{esc(a['username'])}</b>{role_badges(a['username'])}</td>
<td class="num"><span class="starglyph">★</span>{st_['author']}</td>
<td class="num">{st_['runs']}</td><td class="num">{st_['contrib']}</td></tr>''')
committee_now = sorted({ev['user'].lower() for (u, role, scope), ev in ROLES_NOW.items()
                        if role == 'committee'})
body = f'''<header class="ghead"><div><h1>Members</h1>
<p class="authline">Everybody who has logged in here at least once, sorted by author score:
the stars their movies have earned. Click any column to re-sort.</p></div>
</header>
<table class="sortable"><thead><tr><th>Member</th><th class="num"><span class="starglyph">★</span> Author score</th>
<th class="num">Runs</th><th class="num">Contributor score</th></tr></thead>
<tbody>{''.join(arows)}</tbody></table>
<p class="legend">Do you come from another community? <a href="../claim/">Claim your name</a>.</p>
'''
(OUT / 'authors' / 'index.html').write_text(page('Members', body, '../', '', 'Members'))

