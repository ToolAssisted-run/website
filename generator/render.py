"""The View helpers: everything that turns model facts into HTML.

Escaping, the page chrome (page()), chips and badges, wiki-markup
rendering, thumbnails (shipped to the output and registered here), and
the datalist builders behind the pick-never-type rule. Views compose
these; the model never calls them."""
import datetime
import hashlib
import html
import os
import json
import pathlib
import re
import shutil
import urllib.parse
import jinja2
from markupsafe import Markup
from fast_blurhash import encode_blurhash_file, blurhash_to_data_url, get_data_url_for_file
import model
from config import (
    ARCHIVE,
    ARCHIVE_RAW,
    ARCHIVE_REF,
    ARCHIVIST,
    FORUM,
    MONTH_ABBR,
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
    contrib_tier,
    points_of,
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

    Steering Committee, Expert, and the earned Contributor badge.
    Expert says 'Expert' and stops: it used to name the scope, which reads
    well for one game and falls apart for the real case, since scopes need
    not be related, so the chip either lies by naming one of them or turns
    into a list. Contributor is earned rather than granted, by the first
    tAr point. No Founder or Moderator chip.
    """
    # one chip per role, not per scope: three expert scopes is still one Expert
    held = {role for role, scope in roles_of(name)}
    out = [f'<span class="rolechip role-{role}">{ROLE_LABEL[role]}</span>'
           for role in BADGED_ROLES if role in held]
    tier = contrib_tier(points_of(name))
    if tier:
        out.append(f'<span class="rolechip role-contrib {tier[1]}" '
                   f'title="{esc(tier[0])}: has earned tAr points here">'
                   f'{esc(tier[0])}</span>')
    return ''.join(out)

def group_chip(game_key, rel='../../'):
    """The 'part of' line shown on a game's page."""
    mine = groups_of(game_key)
    if not mine:
        return ''
    links = ', '.join(f'<a href="{rel}groups/{gr["key"]}/">{esc(gr["title"])}</a>'
                      for gr in mine)
    return f'<p class="grpline">Part of {links}</p>'

def scope_badge(label, users, rel):
    """A segmented pill badge for an expert tier: scope label prefix in one
    color segment, list of experts in a second segment."""
    dot = '<span class="u-sep">·</span>'
    chips = ''.join(
        f'<span class="u-item">{member_chip(u, rel)}{dot if i < len(users) - 1 else ""}</span>'
        for i, u in enumerate(users))
    return (f'<span class="scopebadge">'
            f'<span class="scopetag">{esc(label)}</span>'
            f'<span class="scopeusers">{chips}</span>'
            f'</span>')

def expert_line(game_key, rel):
    """Who speaks for this game, closest scope first: its own experts (Game),
    then its group's (Group), then its system's (System), followed by a quiet
    count of site-wide experts. No line renders when nobody holds a game, group,
    or system scope."""
    own = sorted({e['user'] for e in experts_reg if e['scope'] == game_key},
                 key=str.lower)
    group_scopes = {'group:' + gr['key'] for gr in groups
                    if game_key in gr.get('games', [])}
    shown = {u.lower() for u in own}
    grp = sorted({e['user'] for e in experts_reg if e['scope'] in group_scopes
                  and e['user'].lower() not in shown}, key=str.lower)
    shown |= {u.lower() for u in grp}
    sys_scope = game_key.split('/')[0]
    sysexp = sorted({e['user'] for e in experts_reg if e['scope'] == sys_scope
                     and e['user'].lower() not in shown}, key=str.lower)
    shown |= {u.lower() for u in sysexp}
    site = sorted({e['user'] for e in experts_reg if e['scope'] == 'site'
                   and e['user'].lower() not in shown}, key=str.lower)
    if not own and not grp and not sysexp:
        return ''
    parts = []
    if own:
        parts.append(scope_badge('Game', own, rel))
    if grp:
        parts.append(scope_badge('Group', grp, rel))
    if sysexp:
        parts.append(scope_badge('System', sysexp, rel))
    tail = (f' <span class="actmeta scopetail" title="{esc(", ".join(site))}">'
            f'+{len(site)} site-wide</span>' if site else '')
    return (f'<p class="authline authline-experts">'
            f'<span class="exp-label">Experts:</span>'
            f'<span class="scopegrid">{" ".join(parts)}{tail}</span>'
            f'</p>')

def esc(s): return html.escape(str(s), quote=True)


def format_date(s):
    """Standardized military date: DD-Mmm-YYYY (e.g. 15-Sep-2026)."""
    if not s:
        return ''
    s = str(s).strip()
    if 'T' in s:
        s = s.split('T')[0]
    if ' ' in s:
        s = s.split(' ')[0]
    parts = s.split('-')
    if len(parts) == 3 and len(parts[0]) == 4:
        try:
            m_idx = int(parts[1]) - 1
            if 0 <= m_idx < 12:
                day_digits = ''.join(c for c in parts[2] if c.isdigit())
                if day_digits:
                    day = str(int(day_digits)).zfill(2)
                    return f'{day}-{MONTH_ABBR[m_idx]}-{parts[0]}'
        except (ValueError, IndexError):
            pass
    return s


def moment(s):
    """A stamp for log rows: the day, and the clock when the record carries
    one ('20-Aug-2026 14:32:07', UTC). Day-only records show the day alone."""
    s = str(s or '').strip()
    if 'T' in s:
        d, t = s.split('T', 1)
        t_clean = t.rstrip('Z')
        return f'{format_date(d[:10])} {t_clean[:8]}'
    if ' ' in s:
        d, t = s.split(' ', 1)
        return f'{format_date(d[:10])} {t[:8]}'
    return format_date(s[:10])

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
  <p class="rules fullw medhint">Ranks by up to 4 metrics, in tie-break order, the first shown where a time shows; leave empty for classic: real time, lower wins. A metric named Time is the run's classic time, stated by the author (importable from the movie or the encode on demand).</p>
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

# the home page's other counters, drawn like the eye: a frame-advance mark
# for runs, a pad for games, a chip for systems, a head for authors
def _stat_icon(paths):
    return f'<svg class="static" viewBox="0 0 24 24" aria-hidden="true">{paths}</svg>'
RUNS_ICON = _stat_icon('<path d="M5 5l10 7-10 7z"/><path d="M19 5v14"/>')
GAMES_ICON = _stat_icon('<rect x="2" y="7" width="20" height="11" rx="5"/>'
                        '<path d="M7 10.5v4M5 12.5h4"/>'
                        '<circle cx="16" cy="11" r="0.8"/><circle cx="18" cy="14" r="0.8"/>')
SYSTEMS_ICON = _stat_icon('<rect x="6" y="6" width="12" height="12" rx="2"/>'
                          '<path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>')
AUTHORS_ICON = _stat_icon('<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-7 8-7s8 3 8 7"/>')

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
    d = board_date(r)
    formatted = format_date(d)
    if (r.get('completed') or '').strip():
        return f'<td data-sort="{esc(d)}" title="completion date">{esc(formatted)}</td>'
    return f'<td data-sort="{esc(d)}" title="submission date">{esc(formatted)}</td>'

def frames_html(r):
    """The frames cell: a count for a movie, a dash for a video-only run,
    which has no frames because it has no input."""
    if r.get('videoOnly'):
        return '<span class="u" title="video-only: no input movie">video</span>'
    if not r['movie'].get('frames'):
        return '<span class="u" title="the movie format could not be read; time as stated">—</span>'
    return f'<b>{r["movie"]["frames"]:,}</b><span class="u">f</span>'

import markup  # noqa: E402  (config put archivist/ on the path)
import notes_upgrade  # noqa: E402
import providers  # noqa: E402
import wikitext  # noqa: E402

def wiki_html(text, rel='../../'):
    """The published rendering: the shared dialect with this site's
    cross-references resolved against the archive."""
    return wikitext.wiki_html(text, refs=lambda s: resolve_refs(s, rel))

def run_ref_html(number, rel):
    """A run written about in text: game · category in time by authors, with a
    hover thumbnail; None when the archive has no such run."""
    rid = 'M' + str(number)
    r = RUN_BY_ID.get(rid)
    if not r:
        return None
    pm = primary_metric_text(r)
    label = (f"{r['_game']['title']} · {cat_label(r)}"
             + (f' in {pm}' if pm != '—' else '')
             + f" by {', '.join(a['user'] for a in r['authors'])}")
    tu = thumb_url(r)
    bh = thumb_blurhash(r)
    bh_attr = f' data-blurhash="{esc(bh)}"' if bh else ''
    durl = thumb_data_url(r) if bh else ''
    style_attr = f' style="background-image:url(&#39;{durl}&#39;);background-size:cover;"' if durl else ''
    card = (f'<span class="refcard"{style_attr}><img{bh_attr} src="{esc(tu)}" loading="lazy" alt="" onload="this.classList.add(&#39;blurhash-loaded&#39;)"></span>'
            if tu else '')
    return f'<a class="runref" href="{rel}runs/{rid}/">{html.escape(label)}{card}</a>'

def user_ref_html(name, rel, label=None):
    """A member written about in text: their profile when they have one."""
    name = name.strip()
    shown = html.escape(label or name)
    if is_member(name):
        return f'<a class="au" href="{rel}authors/{profile_slug(name)}/">{shown}</a>'
    return f'<span class="au">{shown}</span>'

class SiteRefs:
    """The references of the current language, resolved against the archive."""

    def __init__(self, rel):
        self.rel = rel

    def run(self, number, label):
        found = run_ref_html(number, self.rel)
        if found is not None and not label:
            return found
        if found is not None or RUN_BY_ID.get('M' + str(number)):
            return f'<a class="runref" href="{self.rel}runs/M{number}/">{html.escape(label or "M" + str(number))}</a>'
        return None

    def user(self, name, label):
        return user_ref_html(name, self.rel, label)

def notes_html(r, rel='../../'):
    """A run's notes as published: in the language the run's flag names. The
    archive's own header (an import's disclaimer, the leading quote block) is
    not the author's text and is left out of both."""
    text = r.get('_notes', '')
    if notes_upgrade.is_legacy(r):
        return wiki_html(re.sub(r'^>.*$', '', text, flags=re.M), rel)
    lines = text.split('\n')
    n = 0
    while n < len(lines) and lines[n].startswith('>'):
        n += 1
    return markup.render('\n'.join(lines[n:]), refs=SiteRefs(rel))

def md_html(text):
    """Rules and act notes: the current language, references resolved from
    the site root (these render at several depths)."""
    return markup.render(text, refs=SiteRefs('/'))

def resolve_refs(s, rel):
    """Cross-references in the legacy dialect: [M100001] or [6012M] resolve
    to a rich run link (game · category in time by authors) with a hover
    thumbnail; unknown ones and [1234S] submissions link out; [user:Name]
    links the profile."""
    def run_link(m, tasv_fallback):
        found = run_ref_html(m.group(1), rel)
        if found is not None:
            return found
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
    s = re.sub(r'\[user:([A-Za-z0-9. _-]{2,40})\]', lambda m: user_ref_html(m.group(1), rel), s)
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
SHIPPED_ALT_THUMBS = {}  # (run id, an alternate encode's thumbnail) -> the file name we serve it under

SHIPPED_GAME_THUMBS = {} # game key -> the expert-set game face, if any

SHIPPED_SHOTS = {}       # (run id, screenshot path) -> the file name we serve

def shot_url(r, rel_path):
    """A reproduction proof, served by us.

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
    # the selector thumbnails of a run's alternate encodes, beside the run's own
    for r in runs:
        for enc in (r.get('encodes') or [])[1:]:
            t = enc.get('thumbnail') if isinstance(enc, dict) else None
            if not t or pathlib.PurePosixPath(t).name != t:
                continue
            src = r['_dir'] / t
            if not src.is_file():
                continue
            name = f"{r['id']}-{t.lower()}"
            shutil.copy2(src, dest / name)
            SHIPPED_ALT_THUMBS[(r['id'], t)] = name
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

def run_encodes(r):
    """The encodes a run page can play, the main one first.

    The first encode is the main one: the page opens on it and its thumbnail
    is the run's. Any after it is an alternate the page's selector offers,
    under the title and description its author gave it and a thumbnail of its
    own. An encode on no platform we know cannot be embedded and is left out;
    an alternate that came with no title (an imported run's second link) is
    named for what it is.
    """
    listed = []
    for n, enc in enumerate(r.get('encodes') or []):
        provider = providers.resolve(enc.get('url', '')) if isinstance(enc, dict) else None
        if not provider:
            continue
        main = n == 0
        shipped = SHIPPED_ALT_THUMBS.get((r['id'], enc.get('thumbnail')))
        listed.append({
            'url': enc['url'], 'embed': provider['embed'], 'name': provider['name'],
            'main': main,
            'title': 'Main encode' if main else (enc.get('title') or 'Alternate encode'),
            'description': '' if main else (enc.get('description') or ''),
            'thumb': thumb_url(r) if main else (f'/thumbs/{shipped}' if shipped else None),
        })
    return listed

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

def thumb_blurhash(r):
    """Return Blurhash string for a run's thumbnail, or None."""
    t = r.get('thumbnail')
    if not t or '_dir' not in r:
        return None
    src = r['_dir'] / t
    if not src.is_file():
        return None
    try:
        return encode_blurhash_file(src)
    except Exception:
        return None

def game_blurhash(g):
    """Return Blurhash string for a game's thumbnail face, falling back to newest run."""
    t = g.get('thumbnail')
    if t:
        src = ARCHIVE / 'games' / g['key'] / t
        if src.is_file():
            try:
                bh = encode_blurhash_file(src)
                if bh:
                    return bh
            except Exception:
                pass
    for r in g.get('runs', []):
        if r.get('thumbnail'):
            bh = thumb_blurhash(r)
            if bh:
                return bh
    return None

def shot_blurhash(r, rel_path):
    """Return Blurhash string for a proof screenshot, or None."""
    if '_dir' not in r:
        return None
    src = r['_dir'] / rel_path
    if not src.is_file():
        return None
    try:
        return encode_blurhash_file(src)
    except Exception:
        return None

def thumb_data_url(r):
    """Return data URL (16x9 WebP LQIP) decoded from run thumbnail Blurhash, or None."""
    t = r.get('thumbnail')
    if not t or '_dir' not in r:
        return None
    src = r['_dir'] / t
    if not src.is_file():
        return None
    try:
        return get_data_url_for_file(src, bh=thumb_blurhash(r))
    except Exception:
        return None

def game_data_url(g):
    """Return data URL (16x9 WebP LQIP) decoded from game face Blurhash, or None."""
    t = g.get('thumbnail')
    if t:
        src = ARCHIVE / 'games' / g['key'] / t
        if src.is_file():
            try:
                durl = get_data_url_for_file(src)
                if durl:
                    return durl
            except Exception:
                pass
    for r in g.get('runs', []):
        if r.get('thumbnail'):
            durl = thumb_data_url(r)
            if durl:
                return durl
    return None

def shot_data_url(r, rel_path):
    """Return data URL decoded from screenshot Blurhash, or None."""
    if '_dir' not in r:
        return None
    src = r['_dir'] / rel_path
    if not src.is_file():
        return None
    try:
        return get_data_url_for_file(src)
    except Exception:
        return None

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
    bh = thumb_blurhash(r)
    bh_attr = f' data-blurhash="{esc(bh)}"' if bh else ''
    durl = thumb_data_url(r) if bh else None
    style_attr = f' style="background-image:url(&#39;{durl}&#39;);background-size:cover;"' if durl else ''
    img = (f'<img class="{"nsfwblur" if nsfw else ""}"{bh_attr} src="{esc(tu)}" alt="{esc(thumb_alt(r))}" loading="lazy" onload="this.classList.add(&#39;blurhash-loaded&#39;)">'
           if tu else '')
    badge = '<span class="nsfw18">18+</span>' if nsfw else ''
    return (f'<span class="thumb"{style_attr}><span class="sys">{esc(r["_game"]["system"].upper())}</span>'
            f'{img}{badge}{dur}</span>')

FULL_TICK = '<span class="tick full" title="Verified: a member confirmed the goal is met">✓</span>'

NONE_TICK = '<span class="tick none" title="Not yet: this run is pending">—</span>'

def tick(state):
    # an import is verified, full stop: where it was verified is the run
    # page's Status box to tell, not a badge (stored enum stays 'imported')
    if state in ('imported', 'community', 'verified'): return FULL_TICK
    return NONE_TICK

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

COMMITTEE_NAMES_JS = json.dumps(sorted({ev['user'].lower()
                                        for (u, role, sc), ev in ROLES_NOW.items()
                                        if role == 'committee'}))

FOUNDER_NAMES_JS = json.dumps(sorted({ev['user'].lower()
                                      for (u, role, sc), ev in ROLES_NOW.items()
                                      if role == 'founder'}))

NAV_LINKS = [('browse/', 'Runs'), ('games/', 'Games'), ('authors/', 'Members'),
             ('contribute/', 'Contribute'), ('tools/', 'Tools'), ('submit/', 'Submit'),
             ('|', ''),   # separator: the everyday in-site links end here
             ('about/', 'About Us'), (FORUM, 'Forum'), ('https://discord.gg/VsKDT9XB6u', 'Discord')]

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

INDEXED_PAGES = set()

# ---------------- the walls of run frames behind the pages ----------------
# The front page's hero stands on a wall of the archive's own frames, and
# every other page on a shorter one of its own: a page reads as part of a
# TAS archive before a word of it is read. Each wall is composed at build
# time into one JPEG from one thumbnail per game, so a page costs one image,
# not dozens. The games are dealt in an order fixed by run id (the same
# archive builds the same walls): the home wall takes the first, then each
# page wall the next ones, so no two walls repeat a game while the archive
# has games enough. A page wears the wall its title hashes to. Without
# Pillow (CI and the host have it) there are no walls.
WALL_COLS = 11
WALL_TILE = (160, 90)          # one tile, 16:9 like the thumbnails
WALL_GAP = 8
HOME_WALL_ROWS = 8
PAGE_WALLS, PAGE_WALL_ROWS = 8, 5

def wall_order():
    """One run per game with a thumbnail on disk, in run-id hash order."""
    seen, order = set(), []
    for r in sorted(runs, key=lambda r: hashlib.sha1(r['id'].encode()).hexdigest()):
        t = r.get('thumbnail')
        if not t or r['_game']['key'] in seen or not (r['_dir'] / t).is_file():
            continue
        seen.add(r['_game']['key'])
        order.append(r)
    return order

def wall_slice(order, start, count):
    """count runs of the order from start, round again when it runs out."""
    return [order[(start + i) % len(order)] for i in range(count)] if order else []

def compose_wall(wall_runs, rows, rel_path):
    """Compose the runs' thumbnails into one JPEG at rel_path under the build
    and return its stamped URL (relative to the site root), or None."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    if not wall_runs:
        return None
    tw, th = WALL_TILE
    wall = Image.new('RGB', (WALL_COLS * (tw + WALL_GAP), rows * (th + WALL_GAP)), (5, 8, 15))
    for i, r in enumerate(wall_runs[:WALL_COLS * rows]):
        try:
            with Image.open(r['_dir'] / r['thumbnail']) as im:
                tile = ImageOps.fit(im.convert('RGB'), WALL_TILE, Image.Resampling.LANCZOS)
        except OSError:
            continue
        x, y = i % WALL_COLS, i // WALL_COLS
        wall.paste(tile, (x * (tw + WALL_GAP) + WALL_GAP // 2, y * (th + WALL_GAP) + WALL_GAP // 2))
    out = OUT / rel_path
    out.parent.mkdir(parents=True, exist_ok=True)
    wall.save(out, 'JPEG', quality=60, optimize=True, progressive=True)
    return f'{rel_path}?v=' + hashlib.sha1(out.read_bytes()).hexdigest()[:12]

_PAGE_WALLS = None

def page_walls():
    """The page walls, composed on first use (after the thumbnails ship)."""
    global _PAGE_WALLS
    if _PAGE_WALLS is None:
        order = wall_order()
        home = WALL_COLS * HOME_WALL_ROWS
        per = WALL_COLS * PAGE_WALL_ROWS
        _PAGE_WALLS = [u for u in (compose_wall(wall_slice(order, home + k * per, per),
                                                PAGE_WALL_ROWS, f'assets/walls/{k}.jpg')
                                   for k in range(PAGE_WALLS)) if u]
    return _PAGE_WALLS

def page(title, body, rel='', crumb='', active='', head_extra='', wide=False,
         seo=None, full_title=False, scripts=None, styles=None, wall=True):
    """The site chrome around a page body: head, nav, footer (templates/base.html).
    wall=False leaves the page without a wall of frames (the home page has its own)."""
    full = title if full_title else title + ' · toolAssisted.run'
    if seo and 'path' in seo and not seo.get('noindex'):
        INDEXED_PAGES.add(seo['path'].lstrip('/'))
    walls = page_walls() if wall else []
    pick = int(hashlib.sha1(full.encode()).hexdigest(), 16) % len(walls) if walls else 0
    return tpl('base.html', title=full, body=body, rel=rel, crumb=crumb, active=active,
               head_extra=head_extra, wide=wide,
               page_wall=walls[pick] if walls else None, page_wall_n=pick,
               page_scripts=scripts or ['page-core.js'],
               page_styles=styles or [],
               seo_block=seo_head(dict(seo, title=seo.get('title') or full)) if seo else '')


# ---------------- templates ----------------
# Every page is a Jinja2 template under generator/templates/, rendered with
# autoescaping on: a template writes {{ value }} and gets text, always. The
# helpers above that build HTML (chips, ticks, thumbnails, datalists) are
# exposed to templates as safe, so {{ tick(state) }} is markup and {{ title }}
# is not; nothing else is trusted. Views prepare data and call tpl().
TEMPLATES = pathlib.Path(__file__).resolve().parent / 'templates'

_HTML_HELPERS = (
    'role_badges group_chip expert_line scope_badge card_views chip_views md_html run_date_cell '
    'frames_html wiki_html notes_html inline author_chip member_chip thumb_html '
    'tick state_chip badge_chip medals dl_members dl_games '
    'primary_metric_html seo_head fmt_metric').split()
_TEXT_HELPERS = (
    'format_date moment clock sec_clock run_clock release_text primary_metric_text thumb_url '
    'thumb_alt shot_url breadcrumb_ld thumb_blurhash game_blurhash shot_blurhash '
    'thumb_data_url game_data_url shot_data_url').split()
_HTML_CONSTANTS = ('METRICS_ED FULL_TICK NONE_TICK EYE_ICON RUNS_ICON GAMES_ICON '
                   'SYSTEMS_ICON AUTHORS_ICON').split()
_TEXT_CONSTANTS = ('CW_LABELS NAV_LINKS SITE_URL DEFAULT_IMAGE EXPERT_NAMES_JS '
                   'COMMITTEE_NAMES_JS FOUNDER_NAMES_JS MONTH_ABBR ARCHIVE_RAW ARCHIVE_REF ARCHIVIST '
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
