"""View: contribute (renders on import; see views/__init__)."""
from config import OUT
from model import (
    eff_state,
    is_unclassified,
    points,
    repro_bounty,
    runs,
    verify_bounty,
    systems,
)
from render import page, tpl

# ---- contribute page ----
need_repro = sorted([r for r in runs if eff_state(r)[0] == 'none' and not r.get('videoOnly')],
                    key=lambda r: repro_bounty(r), reverse=True)
need_verify = sorted([r for r in runs
                      if eff_state(r)[0] != 'imported' and not is_unclassified(r)
                      and eff_state(r)[1] == 'none'],
                     key=lambda r: verify_bounty(r), reverse=True)
# the system filter serves the list that needs a machine: reproduction (an
# emulator you can run). Verifying only takes watching a video, so no filter
# there. Show every known system as a clickable option.
worklist_systems = sorted(systems.keys())
open_cases = [(r, c) for r in runs for c in r.get('cases', []) if c['status'] == 'open']
# The board says who has done the most; this says what was done last.
# Ten, not five: act dates are day-granular, so a busy day ties and the
# oldest ids fall off first; five was small enough for one day to evict a
# member's act before anybody saw it.
LATEST_N = 10
ACT_ICON = {'first reproduction': ('reproduced', '↻'), 'reproduction': ('reproduced', '↻'),
            'verification': ('verified', '✓')}
ACT_VERB = {'first reproduction': 'first-reproduced',
            'reproduction': 'reproduced', 'verification': 'verified'}

def act_verb(desc):
    """Past-tense wording of an act description, longest phrase first."""
    for key in ('first reproduction', 'reproduction', 'verification'):
        desc = desc.replace(key, ACT_VERB[key])
    return desc

# the worklist's own acts: what the ledger files under curation
latest_acts = [
    {'date': a['date'], 'pts': a['pts'], 'run': a['run'], 'who': who, 'verb': act_verb(a['desc']),
     'cls': ACT_ICON.get(a['desc'], ('verified', '✓'))[0],
     'icon': ACT_ICON.get(a['desc'], ('verified', '✓'))[1]}
    for a, who in sorted(
        ((a, p['user']) for p in points.values() for a in p['acts']
         if a['source'] == 'curation' and a['date']),
        key=lambda aw: (aw[0]['date'], aw[0]['run']['id']), reverse=True)[:LATEST_N]]
body = tpl('contribute.html', need_repro=need_repro, need_verify=need_verify,
           worklist_systems=worklist_systems, open_cases=open_cases,
           latest_acts=latest_acts)
(OUT / 'contribute').mkdir(exist_ok=True)
(OUT / 'contribute' / 'index.html').write_text(page(
    'Contribute: verify and reproduce TAS runs', body, '../', '', 'Contribute',
    seo={'path': 'contribute/',
         'description': ('The public worklist: verify and reproduce tool-assisted speedruns, '
                         'earn tAr points, no assignment needed; and the full table of '
                         'what earns tAr points.')},
    styles=['page-contribute.css']), encoding='utf-8')
