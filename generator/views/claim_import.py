"""View: claim import (renders on import; see views/__init__)."""
from config import OUT
from model import ROLES_NOW
from render import page, tpl

# ---- claim page ----
# identity is the Steering Committee's to assess, on both routes: the filed
# claim it answers, and the direct attestation this page offers its members
site_experts = sorted({ev['user'].lower() for (u, role, sc), ev in ROLES_NOW.items()
                       if role == 'committee'})
body = tpl('claim_import_claim.html', site_experts=site_experts)
(OUT / 'claim').mkdir(exist_ok=True)
(OUT / 'claim' / 'index.html').write_text(page('Claim your identity', body, '../',
    seo={'path': 'claim/', 'noindex': True}, scripts=['page-claim.js']))

# ---- self-service import page ----
body = tpl('claim_import_import.html')
(OUT / 'import').mkdir(exist_ok=True)
(OUT / 'import' / 'index.html').write_text(page('Import my runs', body, '../',
    seo={'path': 'import/', 'noindex': True}, scripts=['page-import.js']))
