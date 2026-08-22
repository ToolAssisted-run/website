"""View: contribute (renders on import; see views/__init__)."""
from config import OUT
from model import (
    eff_state,
    is_unclassified,
    points,
    repro_bounty,
    runs,
    verify_bounty,
)
from render import page, tpl

# ---- contribute page ----
need_repro = sorted([r for r in runs if eff_state(r)[0] == 'none' and not r.get('videoOnly')],
                    key=lambda r: repro_bounty(r), reverse=True)
need_verify = sorted([r for r in runs
                      if eff_state(r)[0] != 'imported' and not is_unclassified(r)
                      and eff_state(r)[1] == 'none'],
                     key=lambda r: verify_bounty(r), reverse=True)
# the filter serves the reproduction list alone: verifying only takes
# watching a video, so what systems you can RUN is irrelevant there
worklist_systems = sorted({r['_game']['system'] for r in need_repro})
open_cases = [(r, c) for r in runs for c in r.get('cases', []) if c['status'] == 'open']
# The board says who has done the most; this says what was done last.
# Ten, not five: act dates are day-granular, so a busy day ties and the
# oldest ids fall off first; five was small enough for one day to evict a
# member's act before anybody saw it.
LATEST_N = 10
ACT_ICON = {'first reproduction': ('reproduced', '↻'), 'reproduction': ('reproduced', '↻'),
            'verification': ('verified', '✓'), 'console verification': ('console', '✓')}
ACT_VERB = {'console verification': 'played on hardware', 'first reproduction': 'first-reproduced',
            'reproduction': 'reproduced', 'verification': 'verified'}

def act_verb(desc):
    """Past-tense wording of an act description, longest phrase first."""
    for key in ('console verification', 'first reproduction', 'reproduction', 'verification'):
        desc = desc.replace(key, ACT_VERB[key])
    return desc

latest_acts = [
    {'date': date, 'pts': pts, 'run': r, 'who': who, 'verb': act_verb(desc),
     'cls': ACT_ICON.get(desc, ('verified', '✓'))[0],
     'icon': ACT_ICON.get(desc, ('verified', '✓'))[1]}
    for date, desc, pts, r, who in sorted(
        ((date, desc, pts, r, p['user'])
         for p in points.values() for date, desc, pts, r in p['acts'] if date),
        key=lambda a: (a[0], a[3]['id']), reverse=True)[:LATEST_N]]
top = sorted(points.values(), key=lambda p: -p['points'])[:10]
body = tpl('contribute.html', need_repro=need_repro, need_verify=need_verify,
           worklist_systems=worklist_systems, open_cases=open_cases,
           latest_acts=latest_acts, top=top)
(OUT / 'contribute').mkdir(exist_ok=True)
(OUT / 'contribute' / 'index.html').write_text(page(
    'Contribute: verify and reproduce TAS runs', body, '../', '', 'Contribute',
    seo={'path': 'contribute/',
         'description': ('The public worklist: verify and reproduce tool-assisted speedruns, '
                         'earn contributor points, no assignment needed.')}))
