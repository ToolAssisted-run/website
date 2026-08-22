"""View: the site's own policy pages (renders on import; see views/__init__).
The constitution lives in the .github repository; these are the site's
practical notices that the forms link to."""
from config import OUT
from render import page, tpl

(OUT / 'policy' / 'co-authors').mkdir(parents=True, exist_ok=True)
TITLE = 'Multiple author submission policy'
(OUT / 'policy' / 'co-authors' / 'index.html').write_text(page(
    TITLE, tpl('policy_coauthors.html'), '../../',
    tpl('policy_crumb.html', title=TITLE).strip(),
    seo={'path': 'policy/co-authors/',
         'description': 'What submitting or importing a collaborative TAS work to '
                        'toolAssisted.run means: attribution, consent of co-authors, '
                        'and what may never be submitted.'}))
