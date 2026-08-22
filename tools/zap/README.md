# Security scanning with OWASP ZAP

Never scan the live service: an active scan submits junk, and the real
archivist would archive it for good. `target.py` starts a throwaway
archivist instead (the test suite's hermetic setup: a lightened copy of the
archive in a scratch git repo with a local bare origin, a mock forum, no
Discord, no e-mail) and prints its URL and submitter key; everything it
touches is deleted when it exits.

    PYTHONPATH=<dir with flask> python3 tools/zap/target.py ~/ToolAssisted-archive
    # ZAP_TARGET=http://127.0.0.1:NNNNN  ZAP_KEY=zap-scan-key

Then run ZAP (the Linux package plus a JRE, nothing installed system-wide)
with an automation plan that seeds every route (GETs, and POSTs with the
key and `dry_run=1`), waits for the passive scan, runs the active scan on
the context and writes `traditional-html` and `traditional-json` reports:

    zap.sh -cmd -autorun plan.yaml -dir <scratch home> -silent

First pass (2026-08-22): the two "High" alerts were false positives (a
timing-based "SQLite injection" against a service with no database; "path
traversal" on `user`, which the username rule refuses, probed by hand). The
medium/low ones (missing nosniff, frame, CSP, referrer headers) were fixed
in nginx (`infra/nginx/hardening.conf`, included by both server blocks)
and in the archivist's after_request hook. Cloudflare masks the `Server`
header at the edge.
