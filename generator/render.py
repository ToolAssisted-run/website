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
from config import (
    BETA,
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
    visits_known,
    ROLES_NOW,
    ROLE_LABEL,
    RUN_BY_ID,
    authors,
    canon,
    cat_label,
    console_state,
    contrib_tier,
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

    Two survive, and neither is a governance role. Expert says 'Expert' and
    stops: it used to name the scope, which reads well for one game and falls
    apart for the real case, since scopes need not be related, so the chip
    either lies by naming one of them or turns into a list. Contributor is
    earned rather than granted, by a single act on a run.
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
    """Who speaks for this game: its own experts and its group' experts.

    Site-wide scope is deliberately left out. It covers every game here, so
    printing it on every game page says nothing about this one and would put
    the same names under thousands of titles.
    """
    reach = {game_key} | {'group:' + gr['key'] for gr in groups
                          if game_key in gr.get('games', [])}
    who = sorted({e['user'] for e in experts_reg if e['scope'] in reach},
                 key=str.lower)
    if not who:
        return ''
    return ('<p class="authline">Experts: '
            + ' · '.join(member_chip(u, rel) for u in who) + '</p>')

def note_experts(game_key):
    """Expert-notes eligibility is deliberately narrower: the game's or the
    game group's experts only, never site-wide scope."""
    reach = scopes_over(game_key) - {'site'}
    return sorted({e['user'].lower() for e in experts_reg if e['scope'] in reach})

def esc(s): return html.escape(str(s), quote=True)


def moment(s):
    """A stamp for log rows: the day, and the clock when the record carries
    one ('2026-08-20 14:32:07', UTC). Day-only records show the day alone."""
    s = str(s or '')
    if 'T' in s:
        return f'{s[:10]} {s[11:19]}'
    return s[:10]

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
  <p class="rules fullw">What does this category rank by? Up to 4 metrics; their order is the
  tie-break hierarchy and the first one is shown everywhere a time shows today. Skip this
  entirely for a classic category: real time, lower is better.</p>
  <div class="mrows"></div>
  <div class="medbtns">
    <button type="button" class="btn quiet med-add">+ Add a metric</button>
    <label class="cwlab"><input type="checkbox" class="med-time"> Include real time
    (derived from the movie or the video; never typed by authors)</label>
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

def run_date_cell(r):
    """The run's primary date on a board: the completion date the authors
    stated when they stated one, the submission day otherwise. A future
    history tab wants the same rule."""
    c = (r.get('completed') or '').strip()
    if c:
        return f'<td title="completion date">{esc(c)}</td>'
    return f'<td title="submission date">{esc((r.get("submitted") or "")[:10])}</td>'

def frames_html(r):
    """The frames cell: a count for a movie, a dash for a video-only run,
    which has no frames because it has no input."""
    if r.get('videoOnly'):
        return '<span class="u" title="video-only: no input movie">video</span>'
    return f'<b>{r["movie"]["frames"]:,}</b><span class="u">f</span>'

def wiki_html(text, rel='../../'):
    """Renderer for the wiki dialect used in author notes — the subset of
    tasvideos' TextFormattingRules that actually occurs in the corpus, plus
    this site's cross-references (see /formatting/)."""
    out = []
    in_ul = in_ol = in_table = in_code = in_quote = False
    code_buf = []

    def close_blocks(quote_too=True):
        nonlocal in_ul, in_ol, in_table, in_quote
        if in_ul: out.append('</ul>'); in_ul = False
        if in_ol: out.append('</ol>'); in_ol = False
        if in_table: out.append('</tbody></table></div>'); in_table = False
        if quote_too and in_quote: out.append('</blockquote>'); in_quote = False

    for line in text.splitlines():
        l = line.rstrip()
        s = l.strip()
        if in_code:
            if s.upper().startswith('%%END_EMBED'):
                out.append(f'<pre class="codebox"><code>{esc(chr(10).join(code_buf))}</code></pre>')
                code_buf = []
                in_code = False
            else:
                code_buf.append(l)
            continue
        if s.upper().startswith('%%SRC_EMBED'):
            close_blocks()
            in_code = True
            continue
        if s.upper().startswith(('%%QUOTE_END', '%%END_QUOTE')):
            close_blocks(quote_too=False)
            if in_quote:
                out.append('</blockquote>')
                in_quote = False
            continue
        if s.upper().startswith('%%QUOTE'):
            close_blocks()
            who = s[7:].strip()
            out.append('<blockquote class="wquote">'
                       + (f'<p class="qwho">{inline(who, rel)}:</p>' if who else ''))
            in_quote = True
            continue
        if s.upper().startswith('%%TAB_END') or s.upper() == '%%TAB':
            continue
        if s.upper().startswith('%%TAB '):
            close_blocks()
            out.append(f'<h4>{inline(s[6:], rel)}</h4>')
            continue
        if s == '%%TOC%%' or s.upper().startswith('%%DIV'):
            continue
        m = re.fullmatch(r'\[module:youtube\|v=([\w-]+)\]', s)
        if m:
            close_blocks()
            out.append(f'<div class="notes-embed"><iframe src="https://www.youtube-nocookie.com/embed/{m.group(1)}" allowfullscreen loading="lazy"></iframe></div>')
            continue
        if re.match(r'^-{4,}$', s):
            close_blocks()
            out.append('<hr>')
            continue
        if l.startswith('>'):  # disclaimer blockquote is rendered separately
            close_blocks()
            continue
        m = re.match(r'^(!{1,3})\s*(.*)', l)
        if m:
            close_blocks()
            lvl = {3: 'h3', 2: 'h3', 1: 'h4'}[len(m.group(1))]
            out.append(f'<{lvl}>{inline(m.group(2), rel)}</{lvl}>')
            continue
        if s.startswith('||') or (s.startswith('|') and s.endswith('|') and len(s) > 1):
            if not in_table:
                close_blocks(quote_too=False)
                out.append('<div class="tblwrap"><table><tbody>')
                in_table = True
            if s.startswith('||'):
                cells = s.strip('|').split('||')
                out.append('<tr>' + ''.join(f'<th>{inline(c.strip(), rel)}</th>' for c in cells) + '</tr>')
            else:
                cells = s.strip('|').split('|')
                out.append('<tr>' + ''.join(f'<td>{inline(c.strip(), rel)}</td>' for c in cells) + '</tr>')
            continue
        elif in_table:
            out.append('</tbody></table></div>')
            in_table = False
        m = re.match(r'^\*+\s*(.*)', l)
        if m:
            if in_ol: out.append('</ol>'); in_ol = False
            if not in_ul: out.append('<ul>'); in_ul = True
            out.append(f'<li>{inline(m.group(1), rel)}</li>')
            continue
        m = re.match(r'^#+\s+(.*)', l)
        if m:
            if in_ul: out.append('</ul>'); in_ul = False
            if not in_ol: out.append('<ol>'); in_ol = True
            out.append(f'<li>{inline(m.group(1), rel)}</li>')
            continue
        if not s:
            close_blocks(quote_too=False)
            continue
        if in_ul: out.append('</ul>'); in_ul = False
        if in_ol: out.append('</ol>'); in_ol = False
        out.append(f'<p>{inline(l, rel)}</p>')
    if in_code and code_buf:  # unterminated embed: still show it
        out.append(f'<pre class="codebox"><code>{esc(chr(10).join(code_buf))}</code></pre>')
    close_blocks()
    return '\n'.join(out)

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
    s = esc(s)
    s = s.replace('%%%', '<br>')
    s = re.sub(r'__(.+?)__', r'<b>\1</b>', s)
    s = re.sub(r"''(.+?)''", r'<em>\1</em>', s)
    # a link to an image renders as the image itself, linked to the original;
    # display only, the stored notes stay exactly as written. Runs first,
    # while the text carries no generated markup to trip over; the lookbehind
    # leaves [url|label] links (a labelled link was asked for) alone.
    img = r'https?://[^\s\|\]\[]+\.(?:png|jpe?g|gif|webp)(?:\?[^\s\|\]\[]*)?'
    s = re.sub(rf'\[({img})\]',
               r'<a href="\1"><img class="noteimg" src="\1" alt="" loading="lazy"></a>',
               s, flags=re.I)
    s = re.sub(rf'(?<!["\[|=])\b({img})',
               r'<a href="\1"><img class="noteimg" src="\1" alt="" loading="lazy"></a>',
               s, flags=re.I)
    s = re.sub(r'\[module:youtube\|v=([\w-]+)\]',
               r'<a href="https://youtu.be/\1">▶ video</a>', s)
    s = resolve_refs(s, rel)
    # TASVideos wiki-relative links ([=Path|label], [=Path]) point at the site
    # the notes were written on; early imports carry plenty and they rendered
    # as broken literal text here
    s = re.sub(r'\[=/?([^\]|]*)\|([^\]]+)\]',
               r'<a href="https://tasvideos.org/\1">\2</a>', s)
    s = re.sub(r'\[=/?([^\]|\s]+)\]',
               r'<a href="https://tasvideos.org/\1">\1</a>', s)
    # the same links written bare, without the '=': only known TASVideos
    # path roots, so bracketed prose is never touched
    s = re.sub(r'\[((?:UserFiles|GameResources|Forum|HomePages)/[^\]|\s]+)\|([^\]]+)\]',
               r'<a href="https://tasvideos.org/\1">\2</a>', s)
    s = re.sub(r'\[((?:UserFiles|GameResources|Forum|HomePages)/[^\]|\s]+)\]',
               r'<a href="https://tasvideos.org/\1">\1</a>', s)
    s = re.sub(r'\[(https?://[^\s\|\]]+)\|([^\]]+)\]', r'<a href="\1">\2</a>', s)
    s = re.sub(r'\[(https?://[^\s\]]+)\]', r'<a href="\1">\1</a>', s)
    return s

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

def thumb_html(r, dur=''):
    """Card thumbnail: the frame derived from the encode, with the system code
    beneath as the fallback while it loads (or for runs still missing one).
    Sexual-content flags blur it behind the 18+ gate."""
    tu = thumb_url(r)
    nsfw = 'sexual' in r.get('contentWarnings', [])
    img = (f'<img class="{"nsfwblur" if nsfw else ""}" src="{esc(tu)}" alt="" loading="lazy">'
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
        return ('<span class="tick none" title="Video-only: no input movie to play '
                'back on hardware">—</span>')
    return '<span class="tick none" title="Not played back on hardware yet (optional)">—</span>'

IMPORTED_TICK = '<span class="tick imported" title="Imported: verified and reproduced at the trusted site it came from, before joining this archive">✓</span>'

FULL_TICK = '<span class="tick full" title="Verified: the goal is confirmed; permanent">✓</span>'

PROV_TICK = '<span class="tick prov" title="Verified: the community confirmed the goal is met; ranked. An expert verification makes it permanent">✓</span>'

NONE_TICK = '<span class="tick none" title="Not yet: this run is pending">—</span>'

def tick(state):
    if state == 'imported': return IMPORTED_TICK
    if state in ('community', 'confirmed'): return FULL_TICK
    if state == 'provisional': return PROV_TICK
    return NONE_TICK

def console_chip(r):
    cs = console_state(r)
    if cs == 'none':
        return ''
    label = ('Console verified' if cs == 'community' else 'Console verified (at source)')
    return f' <span class="chip consolechip">✓ {label}</span>'

def state_chip(r):
    rs, vs = eff_state(r)
    if rs == 'imported':
        return '<span class="chip importedchip">Imported</span>'
    if is_unclassified(r):
        return '<span class="chip unclchip">Unclassified</span>'
    if is_ranked(r):
        return '<span class="chip verchip">Verified</span>'
    return '<span class="chip pendchip">Pending</span>'

def badge_chip(pts):
    """The milestone chip, for lists where everybody is already a contributor
    and a plain 'Contributor' on every row would say nothing."""
    tier = contrib_tier(pts)
    if not tier or tier[1] == 'tier-first':
        return ''
    return f' <span class="rolechip role-contrib {tier[1]}">{esc(tier[0])}</span>'

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

def page(title, body, rel='', crumb='', active='', head_extra='', wide=False):
    links = ''.join(
        '<span class="navsep"></span>' if href == '|' else
        f'<a class="nl{" on" if label == active else ""}" '
        f'href="{href if href.startswith("http") else rel + href}">{label}</a>'
        for href, label in NAV_LINKS)
    betabar = ('<div class="betabar"><span class="betatag">beta</span> '
               'This site is in open beta and under heavy development. Expect rough edges, '
               'and please report anything broken or missing at '
               '<a href="https://github.com/ToolAssisted-run/website/issues" target="_blank" rel="noopener noreferrer">website issues</a>.</div>'
               if BETA else '')
    return f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · toolAssisted.run</title>
<script>try{{var t=localStorage.getItem('tar-theme');if(t)document.documentElement.dataset.theme=t}}catch(e){{}}</script>
<link rel="stylesheet" href="{rel}assets/style.css?v={SITE_COMMIT or 'dev'}">
<link rel="icon" type="image/png" sizes="32x32" href="{rel}assets/icon-32.png">
<link rel="icon" type="image/png" sizes="512x512" href="{rel}assets/icon-512.png">
<link rel="apple-touch-icon" href="{rel}assets/avatar-512-dark.png">
{head_extra}</head><body>
{betabar}<nav class="nav"><a class="brand" href="{rel if rel else './'}" aria-label="toolAssisted.run home"><img class="brandlogo logo-light" src="{rel}assets/logo-light.svg" alt="toolAssisted.run"><img class="brandlogo logo-dark" src="{rel}assets/logo-dark.svg" alt="toolAssisted.run"></a>
<button class="navtoggle" id="navtoggle" aria-label="Open menu" aria-expanded="false"
 aria-controls="navlinks"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg></button>
<div class="navlinks" id="navlinks">{links}</div>
<form class="navsearch" action="{rel}browse/" method="get">
<input type="search" name="q" placeholder="Search runs…" aria-label="Search runs"></form>
<span id="navauth"></span>
<button id="navoffline" class="nl navoff" hidden title="This site cannot reach the archivist right now, so logging in, submitting and contributing will not work. Reading works fine. It is usually a firewall, a VPN or a content blocker between you and forum.toolassisted.run. Click to try again.">archivist unreachable</button></nav>
<script>window.TAR = {{api: '{ARCHIVIST}', rel: '{rel}', v: '{SITE_COMMIT or 'dev'}', experts: {EXPERT_NAMES_JS}, editors: {EDITOR_NAMES_JS}, committee: {COMMITTEE_NAMES_JS}, founders: {FOUNDER_NAMES_JS}}}</script>
<script src="{rel}assets/app.js?v={SITE_COMMIT or 'dev'}" defer></script>
<div class="wrap{' wrapfull' if wide else ''}">{f'<div class="crumb">{crumb}</div>' if crumb else ''}
{body}</div>
<footer><div class="fmain">toolAssisted.run{' · beta' if BETA else ''} · generated from
<a href="https://github.com/ToolAssisted-run/archive">the archive</a>{f' · build <a href="https://github.com/ToolAssisted-run/website/commit/{SITE_COMMIT}">{SITE_COMMIT}</a>' if SITE_COMMIT else ''}</div>
<div class="fsoc">
<a class="soc soc-bluesky" href="https://bsky.app/profile/toolassisted.run" title="Bluesky" aria-label="Bluesky"></a>
<a class="soc soc-github" href="https://github.com/ToolAssisted-run" title="GitHub" aria-label="GitHub"></a>
<a class="soc soc-discord" href="https://discord.gg/VsKDT9XB6u" title="Discord" aria-label="Discord"></a>
<a class="soc soc-forum" href="{FORUM}" title="Forum" aria-label="Forum"></a></div>
<div class="fpol">
<a href="https://github.com/ToolAssisted-run#1-community-principles">Community Principles</a> ·
<a href="https://github.com/ToolAssisted-run#2-governance">Governance</a> ·
<a href="https://github.com/ToolAssisted-run#3-terms-of-use">Terms of Use</a> ·
<a href="https://github.com/ToolAssisted-run#4-code-of-conduct">Code of Conduct</a> ·
<a href="https://github.com/ToolAssisted-run#5-privacy-policy">Privacy Policy</a> ·
<a href="{rel}policy/site-log/">Site log</a></div>
</footer>
</body></html>'''

