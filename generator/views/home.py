"""View: the home page and the 404 page (renders on import; see views/__init__)."""
import hashlib
import json

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

PICKS_SHOWN = 18     # cards on the shelf: three rows of six
PICKS_SHARD = 48     # cards in one shard file the browser draws them from

def random_selection(all_runs, shown):
    """The pool for the runs the other shelves never reach.

    Most liked and Most viewed can only show what already has attention, so
    a run nobody has starred or opened yet has nowhere to appear once it
    falls off Freshly archived. This shelf is where it gets its chance: the
    pool is every run with no stars and no visits, and only while fewer than
    two shelves' worth of those remain does it take in the rest as well,
    quiet ones first. Imported runs are never in it: the shelf shows what
    members brought here, not the seeded corpus.

    The order written here is a hash of the run id, not chance. A build has
    to give the same bytes for the same archive (every suite leans on that,
    and a shelf that reshuffled on every push would churn the page for
    nothing), so the dice are the reader's: the browser picks one shard of
    the pool at random and keeps a random few of it (assets/page-home.js).
    """
    seen = {r['id'] for r in shown}
    left = [r for r in all_runs if r['id'] not in seen
            and r.get('status', {}).get('reproduced') != 'imported']
    quiet = [r for r in left if not nlikes(r) and not nvisits(r)]
    loud = [r for r in left if nlikes(r) or nvisits(r)]
    def scramble(rs):
        return sorted(rs, key=lambda r: hashlib.sha1(r['id'].encode()).hexdigest())
    if len(quiet) >= 2 * PICKS_SHOWN:
        return scramble(quiet)
    return scramble(quiet) + scramble(loud)

WALL_COLS, WALL_ROWS = 11, 8   # the hero wall: tiles across, tiles down
WALL_TILE = (160, 90)          # one tile, 16:9 like the thumbnails
WALL_GAP = 8

def wall_selection(all_runs, slots=WALL_COLS * WALL_ROWS):
    """The runs whose frames make the hero wall: one per game, so the wall
    shows the breadth of the archive, in an order fixed by the run id (the
    same archive builds the same wall). Fewer games than tiles repeat."""
    seen, chosen = set(), []
    for r in sorted(all_runs, key=lambda r: hashlib.sha1(r['id'].encode()).hexdigest()):
        t = r.get('thumbnail')
        if not t or r['_game']['key'] in seen or not (r['_dir'] / t).is_file():
            continue
        seen.add(r['_game']['key'])
        chosen.append(r)
    return [chosen[i % len(chosen)] for i in range(slots)] if chosen else []

def write_hero_wall(wall_runs):
    """Compose the wall into one image and return its URL, or None.

    One file, not 88 thumbnails: the wall sits darkened behind the welcome
    and is only ever seen whole, so a single small JPEG carries it and the
    home page costs one request more, not a hundred. The URL carries a stamp
    of the bytes because the wall moves with the archive. Without Pillow
    (it is in CI and on the host) the hero simply has no wall.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    if not wall_runs:
        return None
    tw, th = WALL_TILE
    wall = Image.new('RGB', (WALL_COLS * (tw + WALL_GAP), WALL_ROWS * (th + WALL_GAP)),
                     (5, 8, 15))
    for i, r in enumerate(wall_runs):
        try:
            with Image.open(r['_dir'] / r['thumbnail']) as im:
                tile = ImageOps.fit(im.convert('RGB'), WALL_TILE, Image.Resampling.LANCZOS)
        except OSError:
            continue
        x, y = i % WALL_COLS, i // WALL_COLS
        wall.paste(tile, (x * (tw + WALL_GAP) + WALL_GAP // 2, y * (th + WALL_GAP) + WALL_GAP // 2))
    out = OUT / 'assets' / 'herowall.jpg'
    out.parent.mkdir(parents=True, exist_ok=True)
    wall.save(out, 'JPEG', quality=60, optimize=True, progressive=True)
    return 'assets/herowall.jpg?v=' + hashlib.sha1(out.read_bytes()).hexdigest()[:12]

def movie_totals(all_runs):
    """The emulator readout over the hero: every frame and every rerecord in
    the archive's movies, summed from what each movie file states."""
    frames = rerecords = 0
    for r in all_runs:
        m = r.get('movie') or {}
        if isinstance(m.get('frames'), int):
            frames += m['frames']
        if isinstance(m.get('rerecords'), int):
            rerecords += m['rerecords']
    return frames, rerecords

def write_pick_shards(pool):
    """Write the pool as shard files of ready-made cards, and return how many
    there are and a stamp of their content for the shard URLs.

    The whole pool in one file would weigh every home page visit by a
    hundred kilobytes and more; one shard is a few. Shards are dealt round
    the pool (run i goes to shard i mod n) so each mixes the whole of it.
    The cards are the same markup the shelves render, so the browser only
    inserts them. The stamp is the content's, not the site commit's: the
    pool moves with the archive, which the site commit does not follow.
    """
    shard_dir = OUT / 'assets' / 'picks'
    shard_dir.mkdir(parents=True, exist_ok=True)
    count = -(-len(pool) // PICKS_SHARD)
    stamp = hashlib.sha1()
    for k in range(count):
        cards = [str(tpl('_pick_card.html', r=r)).strip() for r in pool[k::count]]
        blob = json.dumps(cards, ensure_ascii=False, separators=(',', ':'))
        (shard_dir / f'{k}.json').write_text(blob, encoding='utf-8')
        stamp.update(blob.encode())
    return count, stamp.hexdigest()[:12]

_shown = (fresh_selection(runs, slots=12) + liked_selection(runs)
          + viewed_selection(runs))
_pool = random_selection(runs, _shown)
_shards, _shard_stamp = write_pick_shards(_pool)
_wall = write_hero_wall(wall_selection(runs))
_frames, _rerecords = movie_totals(runs)

body = tpl('home.html',
           total_likes=sum(nlikes(r) for r in runs),
           total_views=sum(nvisits(r) for r in runs),
           fresh=fresh_selection(runs, slots=12),
           liked=liked_selection(runs),
           viewed=viewed_selection(runs),
           picks=_pool[:PICKS_SHOWN], picks_shown=PICKS_SHOWN,
           picks_shards=_shards, picks_stamp=_shard_stamp,
           hero_wall=_wall, total_frames=_frames, total_rerecords=_rerecords)
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
    scripts=['page-home.js'], styles=['page-home.css']), encoding='utf-8')

(OUT / '404.html').write_text(tpl('404.html'), encoding='utf-8')
