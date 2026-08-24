"""The View helpers: everything that turns model facts into HTML.

Escaping, the page chrome (page()), chips and badges, wiki-markup
rendering, thumbnails (shipped to the output and registered here), and
the datalist builders behind the pick-never-type rule. Views compose
these; the model never calls them."""
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
import jinja2
from markupsafe import Markup
import model
from config import (
    ARCHIVE,
    ARCHIVE_RAW,
    ARCHIVE_REF,
    ARCHIVIST,
    FORUM,
    OUT,
    SITE_COMMIT,
)
from model import (
    BADGED_ROLES,
    board_date,
    visits_known,
    ROLES_NOW,
    ROLE_LABEL,
    RUN_BY_ID,
    authors,
    canon,
    cat_label,
    console_state,
    contrib_tier,
    medals_of,
    credited,
    eff_state,
    experts_reg,
    games,
    groups,
    groups_of,
    is_member,
    is_ranked,
    is_unclassified,
    live,
    metric_value,
    points,
    profile_slug,
    roles_of,
    run_metric_defs,
    run_seconds,
    runs,
    scopes_over,
)

def role_badges(name):
    """What a member is, as chips, in as few words as the truth allows.

    Steering Committee, Expert, Editor, and the earned Contributor tier.
    Expert says 'Expert' and stops: it used to name the scope, which reads
    well for one game and falls apart for the real case, since scopes need
    not be related, so the chip either lies by naming one of them or turns
    into a list. Contributor is earned rather than granted, by a single act
    on a run. No Founder or Moderator chip.
    """
    # one chip per role, not per scope: three expert scopes is still one Expert
    held = {role for role, scope in roles_of(name)}
    out = [f'<span class="rolechip role-{role}">{ROLE_LABEL[role]}</span>'
           for role in BADGED_ROLES if role in held]
    tier = contrib_tier(points.get(name.lower(), {}).get('points', 0))
    if tier:
        out.append(f'<span class="rolechip role-contrib {tier[1]}" '
                   f'title="{esc(tier[0])}: earned by reproducing, verifying and '
                   f'console-verifying runs">{esc(tier[0])}</span>')
    return ''.join(out)

def group_chip(game_key, rel='../../'):
    """The 'part of' line shown on a game's page."""
    mine = groups_of(game_key)
    if not mine:
        return ''
    links = ', '.join(f'<a href="{rel}groups/{gr["key"]}/">{esc(gr["title"])}</a>'
                      for gr in mine)
    return f'<p class="grpline">Part of {links}</p>'

def expert_line(game_key, rel):
    """Who speaks for this game, closest scope first (#65): its own experts,
    then its group's, marked so, then a quiet count of the wider scopes
    (system and whole-site) whose names, covering everything, say nothing
    about this game in particular. The count carries the names in its
    tooltip; no line renders when nobody holds a game or group scope."""
    own = sorted({e['user'] for e in experts_reg if e['scope'] == game_key},
                 key=str.lower)
    group_scopes = {'group:' + gr['key'] for gr in groups
                    if game_key in gr.get('games', [])}
    shown = {u.lower() for u in own}
    grp = sorted({e['user'] for e in experts_reg if e['scope'] in group_scopes
                  and e['user'].lower() not in shown}, key=str.lower)
    shown |= {u.lower() for u in grp}
    wider = sorted({e['user'] for e in experts_reg
                    if e['scope'] in ('site', game_key.split('/')[0])
                    and e['user'].lower() not in shown}, key=str.lower)
    if not own and not grp:
        return ''
    parts = []
    if own:
        parts.append(' · '.join(member_chip(u, rel) for u in own))
    if grp:
        parts.append(' · '.join(member_chip(u, rel) for u in grp)
                     + ' <span class="actmeta">(group scope)</span>')
    tail = (f' <span class="actmeta scopetail" title="{esc(", ".join(wider))}">'
            f'+{len(wider)} wider-scope</span>' if wider else '')
    return '<p class="authline">Experts: ' + ' · '.join(parts) + tail + '</p>'

def esc(s): return html.escape(str(s), quote=True)


def moment(s):
    """A stamp for log rows: the day, and the clock when the record carries
    one ('2026-08-20 14:32:07', UTC). Day-only records show the day alone."""
    s = str(s or '')
    if 'T' in s:
        return f'{s[:10]} {s[11:19]}'
    return s[:10]

MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
          'August', 'September', 'October', 'November', 'December']

def release_text(released):
    """A release date as people say it, at the precision the record has:
    '1989', 'March 1989' or '3 March 1989'."""
    parts = (released or '').split('-')
    if not parts[0]:
        return ''
    if len(parts) == 1:
        return parts[0]
    month = MONTHS[int(parts[1]) - 1] if 1 <= int(parts[1]) <= 12 else parts[1]
    if len(parts) == 2:
        return f'{month} {parts[0]}'
    return f'{int(parts[2])} {month} {parts[0]}'

def clock(frames, fps):
    total = frames / fps
    ms = int(round((total % 1) * 1000))
    s = int(total) % 60
    m = int(total) // 60 % 60
    h = int(total) // 3600
    body = f'{m:02d}:{s:02d}.{ms:03d}'
    return (f'{h}:' + body) if h else body

# the metrics editor skeleton the creation pages and the game editor share;
# app.js (initMetricsEd) builds the rows and keeps the hidden input as JSON
METRICS_ED = '''<div class="metricsbox metriced">
  <h4 class="medtitle">Metrics</h4>
  <p class="rules fullw medhint">Ranks by up to 4 metrics, in tie-break order, the first shown where a time shows; leave empty for classic: real time, lower wins. A metric named Time is the run's main time, stated by the author (importable from the movie or the encode on demand).</p>
  <div class="mrows"></div>
  <div class="medbtns">
    <button type="button" class="btn quiet med-add">+ Add a metric</button>
  </div>
  <input type="hidden" name="metrics">
</div>'''

def sec_clock(s):
    if s is None:
        return '—'
    ms = int(round((s % 1) * 1000))
    body = f'{int(s) // 60 % 60:02d}:{int(s) % 60:02d}.{ms:03d}'
    return f'{int(s) // 3600}:{body}' if s >= 3600 else body

def run_clock(r):
    return sec_clock(run_seconds(r))

def fmt_metric(v, mdef):
    """One metric value for display: a clock for time-typed metrics, a
    unit-suffixed number otherwise, the dash when nothing is stated."""
    if v is None:
        return '—'
    if mdef['type'] == 'time':
        return sec_clock(v)
    n = f'{v:,.3f}'.rstrip('0').rstrip('.')
    unit = mdef.get('unit')
    return f'{n}<span class="u"> {esc(unit)}</span>' if unit else n

def primary_metric_html(r):
    """The run's primary metric, rendered: what the coalesced browse column,
    thumbnail badges and leaderboards lead with."""
    m = run_metric_defs(r)[0]
    return fmt_metric(metric_value(r, m), m)

def primary_metric_text(r):
    """Same, plain text (thumbnail badges, meta descriptions)."""
    m = run_metric_defs(r)[0]
    v = metric_value(r, m)
    if v is None:
        return '—'
    if m['type'] == 'time':
        return sec_clock(v)
    n = f'{v:,.3f}'.rstrip('0').rstrip('.')
    return f'{n} {m["unit"]}' if m.get('unit') else n

EYE_ICON = ('<svg class="eyeic" viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z"/>'
            '<circle cx="12" cy="12" r="2.6"/></svg>')

def card_views(n):
    """The little eye a card carries, zero included. Visit counts are host
    state, absent on CI and the standby; only there does the eye vanish,
    because an unknowable count shown as 0 would be a lie."""
    if not visits_known:
        return ''
    return ('<span class="cview" title="number of visits">'
            '<svg class="eyeic" viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z"/><circle cx="12" cy="12" r="2.6"/></svg>'.replace(chr(10), '')
            + f'{n:,}</span>')

def chip_views(n):
    """The eye as a heading chip: cumulative visits for a band of games.
    Same rule as card_views: no host state, no chip."""
    if not visits_known:
        return ''
    return ('<span class="chip viewchip" title="number of visits">'
            '<svg class="eyeic" viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z"/>'
            '<circle cx="12" cy="12" r="2.6"/></svg> '
            + f'{n:,}</span>')

def md_html(text):
    """Markdown, the small honest subset category rules use: paragraphs,
    bullet and numbered lists, **bold**, *italic*, `code` and [label](url)
    links. Everything else stays text; nothing raw passes through."""
    def infmt(t):
        t = esc(t)
        t = re.sub(r'\[([^\]]+)\]\((https?://[^\s)]+)\)', r'<a href="\2">\1</a>', t)
        t = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', t)
        t = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'<em>\1</em>', t)
        t = re.sub(r'`([^`]+)`', r'<code>\1</code>', t)
        return t
    out = []
    mode = [None]   # None | 'ul' | 'ol' | 'p'
    def close():
        if mode[0] == 'ul': out.append('</ul>')
        if mode[0] == 'ol': out.append('</ol>')
        if mode[0] == 'p': out.append('</p>')
        mode[0] = None
    for raw in str(text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        line = raw.strip()
        if not line:
            close()
            continue
        m = re.match(r'[-*]\s+(.*)', line)
        if m:
            if mode[0] != 'ul': close(); out.append('<ul>'); mode[0] = 'ul'
            out.append(f'<li>{infmt(m.group(1))}</li>')
            continue
        m = re.match(r'\d+[.)]\s+(.*)', line)
        if m:
            if mode[0] != 'ol': close(); out.append('<ol>'); mode[0] = 'ol'
            out.append(f'<li>{infmt(m.group(1))}</li>')
            continue
        if mode[0] != 'p':
            close(); out.append('<p>'); mode[0] = 'p'
        else:
            out.append(' ')
        out.append(infmt(line))
    close()
    return ''.join(out)

def run_date_cell(r):
    """The run's primary date on a board: the completion date the authors
    stated when they stated one, the submission day otherwise. A future
    history tab wants the same rule."""
    if (r.get('completed') or '').strip():
        return f'<td title="completion date">{esc(board_date(r))}</td>'
    return f'<td title="submission date">{esc(board_date(r))}</td>'

def frames_html(r):
    """The frames cell: a count for a movie, a dash for a video-only run,
    which has no frames because it has no input."""
    if r.get('videoOnly'):
        return '<span class="u" title="video-only: no input movie">video</span>'
    if not r['movie'].get('frames'):
        return '<span class="u" title="the movie format could not be read; time as stated">—</span>'
    return f'<b>{r["movie"]["frames"]:,}</b><span class="u">f</span>'

import wikitext  # noqa: E402  (config put archivist/ on the path)

def wiki_html(text, rel='../../'):
    """The published rendering: the shared dialect with this site's
    cross-references resolved against the archive."""
    return wikitext.wiki_html(text, refs=lambda s: resolve_refs(s, rel))

def resolve_refs(s, rel):
    """Cross-references in written text: [M100001] or tasvideos-style [6012M]
    resolve to a rich run link (game · category in time by authors) with a
    hover thumbnail; unknown tasvideos ids and [1234S] submissions link out;
    [user:Name] links the profile."""
    def run_link(m, tasv_fallback):
        rid = 'M' + m.group(1)
        r = RUN_BY_ID.get(rid)
        if r:
            pm = primary_metric_text(r)
            label = (f"{r['_game']['title']} · {cat_label(r)}"
                     + (f' in {pm}' if pm != '—' else '')
                     + f" by {', '.join(a['user'] for a in r['authors'])}")
            tu = thumb_url(r)
            card = (f'<span class="refcard"><img src="{esc(tu)}" loading="lazy" alt=""></span>'
                    if tu else '')
            return f'<a class="runref" href="{rel}runs/{rid}/">{html.escape(label)}{card}</a>'
        if tasv_fallback:
            return (f'<a class="runref ext" href="https://tasvideos.org/{m.group(1)}M">'
                    f'{m.group(1)}M (TASVideos)</a>')
        return m.group(0)
    s = re.sub(r'\[M([0-9]+)\]', lambda m: run_link(m, False), s)
    s = re.sub(r'\[([0-9]+)M\]', lambda m: run_link(m, True), s)
    s = re.sub(r'\[([0-9]+)S\]',
               r'<a class="runref ext" href="https://tasvideos.org/\1S">\1S (TASVideos submission)</a>', s)
    # the same refs written with their own label: [10255S|GTA2 movies]
    s = re.sub(r'\[([0-9]+[SM])\|([^\]]+)\]',
               r'<a class="runref ext" href="https://tasvideos.org/\1">\2</a>', s)
    def user_link(m):
        name = m.group(1).strip()
        if is_member(name):
            return f'<a class="au" href="{rel}authors/{profile_slug(name)}/">{html.escape(name)}</a>'
        return f'<span class="au">{html.escape(name)}</span>'
    s = re.sub(r'\[user:([A-Za-z0-9. _-]{2,40})\]', user_link, s)
    return s

def inline(s, rel='../../'):
    return wikitext.inline(s, refs=lambda t: resolve_refs(t, rel))

def author_chip(name, rel):
    """A credit. Members link to their profile; everyone else is their name,
    exactly as the run spells it, and nothing else. Nobody gets a page here
    they never asked for."""
    if is_member(name):
        cur = authors[canon(name)]['username']
        return f'<a class="au" href="{rel}authors/{esc(profile_slug(cur))}/">{esc(name)}</a>'
    return f'<span class="au">{esc(name)}</span>'

def member_chip(name, rel):
    """A community member acting on a run (reproducer/verifier)."""
    a = authors.get(canon(name))
    if a and a.get('claimed'):
        return f'<a class="au" href="{rel}authors/{esc(profile_slug(a["username"]))}/">{esc(name)}</a>'
    return f'<span class="au">{esc(name)}</span>'

CW_LABELS = {'mature-violence': 'Mature / violent content',
             'sexual': 'Sexual content',
             'photosensitivity': 'Photosensitivity warning: flashing lights',
             'strong-language': 'Strong language'}

SHIPPED_THUMBS = {}      # run id -> the file name we serve it under

SHIPPED_GAME_THUMBS = {} # game key -> the expert-set game face, if any

SHIPPED_SHOTS = {}       # (run id, screenshot path) -> the file name we serve

def shot_url(r, rel_path):
    """A reproduction or console-verification proof, served by us.

    Same reason as the thumbnails: these are images a page loads, and raw
    .githubusercontent is not something to load images from.
    """
    src = r['_dir'] / rel_path
    key = (r['id'], rel_path)
    if key not in SHIPPED_SHOTS:
        if not src.is_file():
            return f'{ARCHIVE_RAW}/games/{r["_game"]["key"]}/runs/{r["id"]}/{rel_path}'
        dest = OUT / 'shots'
        dest.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r'[^A-Za-z0-9._-]+', '-', rel_path)
        name = f'{r["id"]}-{safe}'
        shutil.copy2(src, dest / name)
        SHIPPED_SHOTS[key] = name
    return f'/shots/{SHIPPED_SHOTS[key]}'

def ship_thumbnails():
    """Copy every run's thumbnail into the build.

    They used to be hotlinked from raw.githubusercontent, which is not an image
    CDN and says so: a page carrying 230 of them started answering 429 for
    everybody, from every address, and the whole site looked broken. They are
    32 MB in total, which the old 10 MB hosting plan could not have taken and
    Pages does not notice. Serving them ourselves also drops a third party from
    every page load.
    """
    dest = OUT / 'thumbs'
    dest.mkdir(parents=True, exist_ok=True)
    for r in runs:
        t = r.get('thumbnail')
        if not t:
            continue
        src = r['_dir'] / t
        if not src.is_file():
            continue
        name = f"{r['id']}{pathlib.Path(t).suffix.lower()}"
        shutil.copy2(src, dest / name)
        SHIPPED_THUMBS[r['id']] = name
    # a game an expert gave its own face to
    for g_ in games.values():
        t = g_.get('thumbnail')
        if not t:
            continue
        src = ARCHIVE / 'games' / g_['key'] / t
        if not src.is_file():
            continue
        name = f"g-{g_['key'].replace('/', '-')}{pathlib.Path(t).suffix.lower()}"
        shutil.copy2(src, dest / name)
        SHIPPED_GAME_THUMBS[g_['key']] = name

def thumb_url(r):
    t = r.get('thumbnail')
    if not t:
        return None
    # root-relative: the same string works from a page at any depth, and the
    # site is always served from the root of its domain
    shipped = SHIPPED_THUMBS.get(r['id'])
    if shipped:
        return f'/thumbs/{shipped}'
    return f'{ARCHIVE_RAW}/games/{r["_game"]["key"]}/runs/{r["id"]}/{t}'

def thumb_alt(r):
    """What the thumbnail is, for image search and screen readers."""
    g = r['_game']
    who = ', '.join(a['user'] for a in r['authors'])
    return f'{g["title"]} ({g["system"].upper()}) TAS by {who}'

def thumb_html(r, dur=''):
    """Card thumbnail: the frame derived from the encode, with the system code
    beneath as the fallback while it loads (or for runs still missing one).
    Sexual-content flags blur it behind the 18+ gate."""
    tu = thumb_url(r)
    nsfw = 'sexual' in r.get('contentWarnings', [])
    img = (f'<img class="{"nsfwblur" if nsfw else ""}" src="{esc(tu)}" alt="{esc(thumb_alt(r))}" loading="lazy">'
           if tu else '')
    badge = '<span class="nsfw18">18+</span>' if nsfw else ''
    return (f'<span class="thumb"><span class="sys">{esc(r["_game"]["system"].upper())}</span>'
            f'{img}{badge}{dur}</span>')

def console_tick(r):
    """Column cell for the third signal: a mark when the run has been played
    back on hardware (here or, for imports, on TASVideos), a neutral dash
    otherwise. Absence is never a failure."""
    cs = console_state(r)
    if cs == 'imported':
        return ('<span class="tick console" title="Console verified at the trusted '
                'site it was imported from">✓</span>')
    if cs == 'community':
        n = len(live(r.get('consoleVerifications', [])))
        return (f'<span class="tick console" title="Played back on original hardware '
                f'by {n} member{"s" if n != 1 else ""}">✓</span>')
    if cs == 'not-applicable':
        why = ('Video-only: no input movie to play back on hardware' if r.get('videoOnly')
               else 'Not a system that is played back on hardware')
        return f'<span class="tick none na" title="{why}">·</span>'
    return '<span class="tick none" title="Not played back on hardware yet (optional)">—</span>'

FULL_TICK = '<span class="tick full" title="Verified: a member confirmed the goal is met">✓</span>'

NONE_TICK = '<span class="tick none" title="Not yet: this run is pending">—</span>'

def tick(state):
    # an import is verified, full stop: where it was verified is the run
    # page's Status box to tell, not a badge (stored enum stays 'imported')
    if state in ('imported', 'community', 'verified'): return FULL_TICK
    return NONE_TICK

def console_chip(r):
    cs = console_state(r)
    if cs == 'none':
        return ''
    label = ('Console verified' if cs == 'community' else 'Console verified (at source)')
    return f' <span class="chip consolechip">✓ {label}</span>'

def state_chip(r):
    rs, vs = eff_state(r)
    if is_unclassified(r):
        return '<span class="chip unclchip">Unclassified</span>'
    if is_ranked(r):
        return '<span class="chip verchip">Verified</span>'
    return '<span class="chip pendchip">Pending</span>'

def medals(name):
    """The member's medals as little discs, each naming its achievement in
    its tooltip (issue #59); nothing when they hold none."""
    out = [f'<span class="medal medal-{metal}" title="{esc(words)}" '
           f'aria-label="{esc(words)}">{esc(mark)}</span>'
           for key, metal, mark, words in medals_of(name)]
    return (' <span class="medals">' + ''.join(out) + '</span>') if out else ''

def badge_chip(pts):
    """Nothing: the milestone tiers are retired (the medals carry the
    honors), and on lists where everybody contributed the plain badge would
    say nothing."""
    return ''

EXPERT_NAMES_JS = json.dumps(sorted({e['user'].lower() for e in experts_reg}))

EDITOR_NAMES_JS = json.dumps(sorted({ev['user'].lower()
                                     for (u, role, sc), ev in ROLES_NOW.items()
                                     if role == 'editor'}))

COMMITTEE_NAMES_JS = json.dumps(sorted({ev['user'].lower()
                                        for (u, role, sc), ev in ROLES_NOW.items()
                                        if role == 'committee'}))

FOUNDER_NAMES_JS = json.dumps(sorted({ev['user'].lower()
                                      for (u, role, sc), ev in ROLES_NOW.items()
                                      if role == 'founder'}))

NAV_LINKS = [('browse/', 'Runs'), ('games/', 'Games'), ('authors/', 'Members'),
             ('contribute/', 'Contribute'), ('tools/', 'Tools'), ('submit/', 'Submit'),
             ('|', ''),   # separator: in-site links end here
             (FORUM, 'Forum'), ('https://discord.gg/VsKDT9XB6u', 'Discord')]

def dl_members(exclude=()):
    """Members as a datalist, minus the ones the form would refuse anyway.

    The pattern on top of the pattern: a registered thing is picked from a
    list you can type into, and the list never offers a choice the archivist
    would answer with an error. Offering it teaches people the form is not to
    be trusted."""
    low = {e.lower() for e in exclude}
    opts = ''.join(f'<option value="{esc(a["username"])}"></option>'
                   for a in sorted(authors.values(), key=lambda x: x['username'].lower())
                   if a['username'].lower() not in low)
    return f'<datalist id="dl-members">{opts}</datalist>'

def dl_heldnames():
    """Credited names nobody has claimed: what the claim page offers."""
    held = sorted((v for k, v in credited.items() if k not in authors), key=str.lower)
    opts = ''.join(f'<option value="{esc(n)}"></option>' for n in held)
    return f'<datalist id="dl-heldnames">{opts}</datalist>'

def dl_games():
    """Games a new group could take: ungrouped, and neither refused nor
    removed. A game already in a group would be refused (a game belongs to
    one), so it is not offered."""
    grouped = {gk for gr in groups for gk in gr.get('games', [])}
    opts = ''.join(f'<option value="{esc(k)}" label="{esc(g["title"])}"></option>'
                   for k, g in sorted(games.items())
                   if k not in grouped and not g.get('rejected') and not g.get('removed'))
    return f'<datalist id="dl-games">{opts}</datalist>'

SITE_URL = 'https://toolassisted.run'
DEFAULT_IMAGE = SITE_URL + '/assets/avatar-512-dark.png'

def seo_head(seo):
    """The search-engine and share-card head block. `seo` carries:
    path (site-relative, ends with /), description, image (absolute URL),
    ld (a list of JSON-LD objects), noindex (bool), type (og:type).
    Every public page gets a canonical, a description, Open Graph and a
    Twitter card; content pages add structured data on top."""
    if not seo:
        return ''
    url = SITE_URL + '/' + seo.get('path', '').lstrip('/')
    desc = esc(seo.get('description', ''))
    img = esc(seo.get('image') or DEFAULT_IMAGE)
    out = []
    if seo.get('noindex'):
        out.append('<meta name="robots" content="noindex">')
    out.append(f'<link rel="canonical" href="{esc(url)}">')
    if desc:
        out.append(f'<meta name="description" content="{desc}">')
    out.append(f'<meta property="og:site_name" content="toolAssisted.run">')
    out.append(f'<meta property="og:type" content="{esc(seo.get("type", "website"))}">')
    out.append(f'<meta property="og:url" content="{esc(url)}">')
    out.append(f'<meta property="og:title" content="{esc(seo.get("title", ""))}">')
    if desc:
        out.append(f'<meta property="og:description" content="{desc}">')
    out.append(f'<meta property="og:image" content="{img}">')
    out.append('<meta name="twitter:card" content="summary_large_image">')
    out.append(f'<meta name="twitter:title" content="{esc(seo.get("title", ""))}">')
    if desc:
        out.append(f'<meta name="twitter:description" content="{desc}">')
    out.append(f'<meta name="twitter:image" content="{img}">')
    for obj in seo.get('ld', []):
        out.append('<script type="application/ld+json">'
                   + json.dumps(obj, ensure_ascii=False).replace('<', '\\u003c')
                   + '</script>')
    return '\n'.join(out)

def breadcrumb_ld(items):
    """items: [(name, site-relative path), ...] from the home down."""
    return {'@context': 'https://schema.org', '@type': 'BreadcrumbList',
            'itemListElement': [
                {'@type': 'ListItem', 'position': i + 1, 'name': n,
                 'item': SITE_URL + '/' + p.lstrip('/')} for i, (n, p) in enumerate(items)]}

def page(title, body, rel='', crumb='', active='', head_extra='', wide=False,
         seo=None, full_title=False):
    """The site chrome around a page body: head, nav, footer (templates/base.html)."""
    full = title if full_title else title + ' · toolAssisted.run'
    return tpl('base.html', title=full, body=body, rel=rel, crumb=crumb, active=active,
               head_extra=head_extra, wide=wide,
               seo_block=seo_head(dict(seo, title=seo.get('title') or full)) if seo else '')


# ---------------- templates ----------------
# Every page is a Jinja2 template under generator/templates/, rendered with
# autoescaping on: a template writes {{ value }} and gets text, always. The
# helpers above that build HTML (chips, ticks, thumbnails, datalists) are
# exposed to templates as safe, so {{ tick(state) }} is markup and {{ title }}
# is not; nothing else is trusted. Views prepare data and call tpl().
TEMPLATES = pathlib.Path(__file__).resolve().parent / 'templates'

_HTML_HELPERS = (
    'role_badges group_chip expert_line card_views chip_views md_html run_date_cell '
    'frames_html wiki_html inline author_chip member_chip thumb_html console_tick '
    'tick console_chip state_chip badge_chip medals dl_members dl_heldnames dl_games '
    'primary_metric_html seo_head fmt_metric').split()
_TEXT_HELPERS = (
    'moment clock sec_clock run_clock release_text primary_metric_text thumb_url '
    'thumb_alt shot_url breadcrumb_ld').split()
_HTML_CONSTANTS = 'METRICS_ED FULL_TICK NONE_TICK EYE_ICON'.split()
_TEXT_CONSTANTS = ('CW_LABELS NAV_LINKS SITE_URL DEFAULT_IMAGE EXPERT_NAMES_JS EDITOR_NAMES_JS '
                   'COMMITTEE_NAMES_JS FOUNDER_NAMES_JS ARCHIVE_RAW ARCHIVE_REF ARCHIVIST '
                   'FORUM SITE_COMMIT').split()

def _safe(fn):
    def wrapped(*a, **k):
        return Markup(fn(*a, **k))
    wrapped.__name__ = fn.__name__
    return wrapped

def _env():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)),
                             autoescape=True, trim_blocks=True, lstrip_blocks=True,
                             keep_trailing_newline=True, undefined=jinja2.StrictUndefined)
    g = globals()
    for name in _HTML_HELPERS: env.globals[name] = _safe(g[name])
    for name in _TEXT_HELPERS: env.globals[name] = g[name]
    for name in _HTML_CONSTANTS: env.globals[name] = Markup(g[name])
    for name in _TEXT_CONSTANTS: env.globals[name] = g[name]
    # the model's derivations are text facts, never markup; templates may ask
    # them directly (nlikes(r), eff_state(r)) instead of having every view
    # precompute a parallel structure
    for name, obj in vars(model).items():
        if not name.startswith('_') and name not in env.globals:
            env.globals[name] = obj
    env.globals['json_blob'] = json_blob
    env.globals['Markup'] = Markup
    env.filters['tojson_blob'] = json_blob
    return env

def json_blob(data):
    """JSON for an embedded <script type="application/json"> block: '<' is
    escaped so the data can never close its own tag."""
    return Markup(json.dumps(data).replace('<', chr(92) + 'u003c'))

_ENV = None

def tpl(name, **ctx):
    """Render templates/<name> with ctx; HTML helpers are already in scope."""
    global _ENV
    if _ENV is None: _ENV = _env()
    return _ENV.get_template(name).render(**ctx)

