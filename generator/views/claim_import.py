"""View: claim import (renders on import; see views/__init__)."""
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
    ARCHIVIST,
    FORUM,
    OUT,
)
from model import (
    ROLES_NOW,
)
from render import (
    dl_heldnames,
    dl_members,
    page,
)

# ---- claim page ----
# identity is the Steering Committee's to assess, on both routes: the filed
# claim it answers, and the direct attestation this page offers its members
site_experts = sorted({ev['user'].lower() for (u, role, sc), ev in ROLES_NOW.items()
                       if role == 'committee'})
body = f'''<header class="ghead"><div><h1>Claim your identity</h1>
<p class="authline">To protect the identity of authors from other TAS sites, their names are
held for them here: nobody else can take one. Claiming links yours to your account, hands
you the held username, and lets you bring your own runs over.</p></div></header>
<div class="policy">
<p><b>Ask here, and the Steering Committee answers.</b> Say what shows the name is yours: a
post from the account that name belongs to, a channel hosting your encodes, an account we have
known for years. The Committee alone assesses identity
(<a href="https://github.com/ToolAssisted-run#3-terms-of-use">Terms 3.8</a>): no expert, no
moderator, nobody else may hand a held name to anybody. You are told either way, and the
answer is public in the <a href="../policy/site-log/#claims">site log</a>.</p>
<p>There is a second route to the same judges: a Committee member may <b>attest</b> an
identity they can vouch for directly, naming themselves and what they checked. That is their
judgement on the record rather than a claim you filed, and it is logged separately.</p>
<p><b>An obfuscated form of your email address is shown to the Steering Committee while
your claim is open.</b> Not the whole address: <code>johndoe@email.com</code> reaches
them as <code>jo***oe@e****.com</code>. It is enough for somebody to tell whether it is
the address that author would have, which is the point of it, and not enough to write to you
or to work out who you are from nothing.
(<a href="https://github.com/ToolAssisted-run#5-privacy-policy">Privacy 5.4.1</a>.) Neither
form is written into the archive, shown on the site, or kept anywhere: it is worked out at
the moment they look. Filing a claim is how you agree to that; if you would rather not, ask
on the forum instead and a site-wide expert can attest it without a claim being filed
here.</p>
<p><b>A ban elsewhere is not a status here.</b> It neither blocks a claim nor follows you
(<a href="https://github.com/ToolAssisted-run#1-community-principles">Principles 1.4,
1.5</a>).</p>
<p><b>Once claimed</b>, the username is yours. If the name comes from a site we can read
from, your profile also grows an <b>Import my runs</b> button. You pick which of your
publications come over, co-authored ones included; importing a co-authored work is your
responsibility (<a href="https://github.com/ToolAssisted-run#3-terms-of-use">Terms
3.7</a>).</p>
</div>
<div id="claim-form-wrap" class="actzone" hidden>
<form id="f-claim" class="actform">
  <h3>Claim a name</h3>
  <p class="rules">One open claim at a time. While it is open the Committee sees a masked
  form of your forum email address, as above.</p>
  <label>Name you are claiming</label><input name="identity" list="dl-heldnames" required
         placeholder="type to find the held name">
  <label>What shows it is yours (public)</label>
  <textarea name="evidence" rows="3" required
            placeholder="e.g. I posted this request from that account at the source site"></textarea>
  <button class="btn">File</button>
</form>
<p id="claim-msg" class="actmsg" hidden></p></div>
<p class="statline" id="claim-login" hidden><a class="btn" href="{ARCHIVIST}/login">Log in to
claim a name</a></p>
<p class="statline" style="margin-top:18px"><a class="btn quiet" href="{FORUM}">Ask on the forum</a></p>
<script type="application/json" id="siteexperts">{json.dumps(site_experts).replace('<', chr(92) + 'u003c')}</script>
<div id="attest-wrap" class="actzone" hidden>
<form id="f-attest" class="actform">
  <h3>Attest an identity (Steering Committee)</h3>
  <p class="rules">You are vouching for this, publicly and by name. Say what you actually
  checked; the method is published in the site log beside your name, and it can be
  challenged.</p>
  <label>Forum account</label><input name="member" list="dl-members" required
         placeholder="type to find a member">
  <label>Name being claimed</label><input name="identity" list="dl-heldnames" required
         placeholder="type to find the held name">
  <label>How you verified it (required, public)</label>
  <textarea name="method" rows="3" required
            placeholder="e.g. posted the request from the account that name belongs to"></textarea>
  <button class="btn">Attest</button>
</form>
<p id="attest-msg" class="actmsg" hidden></p></div>
{dl_members()}
{dl_heldnames()}'''
(OUT / 'claim').mkdir(exist_ok=True)
(OUT / 'claim' / 'index.html').write_text(page('Claim your identity', body, '../', seo={'path': 'claim/', 'noindex': True}))

# ---- self-service import page ----
body = '''<header class="ghead"><div><h1>Import my runs</h1>
<p class="authline">Bring your published runs from other TAS / speedrun sites into the
archive. Voluntary, repeatable, yours.</p></div></header>
<div class="policy fullw">
<p><b>What happens.</b> Your movie files and your own submission notes enter this archive
credited to you and marked <b>Imported</b>, distributed under their original Creative
Commons license and linking back to where they were published. Judge and staff text is
stripped at the boundary; only your words travel. Publication descriptions are never used,
and the import reads from a maintained snapshot, never from the source site itself.</p>
<p><b>You pick.</b> Nothing is imported unpicked: the scan lists what the snapshot holds
for you, and only the runs you tick come over. Only runs not yet archived are ever
added; nothing is overwritten or duplicated. Come back any time to pick up runs you
publish elsewhere later.</p>
<p><b>Co-authored works are yours to answer for.</b> You can tick a run you made with
others, and importing it is <b>your responsibility</b>: you are saying your co-authors are
fine with it being here. The run credits every author and records you as the importer. If a
co-author objects, any author can have it withdrawn, and all authors together can have it
permanently erased
(<a href="https://github.com/ToolAssisted-run#1-community-principles">Principles</a>).</p>
<p><b>Requirements.</b> Log in via the forum and <a href="../claim/">claim your
identity</a> first.</p>
</div>
<div class="actmsg" id="imp-msg" hidden></div>
<div class="dimrow" id="imp-sources" hidden><span class="dimname">Import from</span>
<button class="btn" id="imp-scan">TASVideos</button></div>
<div id="imp-ctl" hidden>
<p class="statline" id="imp-scanline"></p>
<p class="impstart">
  <button class="btn quiet" id="imp-solo" hidden>Select my solo runs</button>
  <button class="btn quiet" id="imp-clear" hidden>Clear</button>
  <button class="btn" id="imp-run" hidden></button></p>
<div class="rules fullw implist" id="imp-list" hidden></div>
</div>
<div id="imp-prog" hidden>
<div class="pbar"><div class="pfill" id="imp-fill"></div></div>
<p class="statline"><b id="imp-count"></b></p>
<pre class="implog" id="imp-log"></pre>
</div>'''
(OUT / 'import').mkdir(exist_ok=True)
(OUT / 'import' / 'index.html').write_text(page('Import my runs', body, '../', seo={'path': 'import/', 'noindex': True}))

