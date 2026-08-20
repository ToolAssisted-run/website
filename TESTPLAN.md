# Test plan

Written 2026-08-15 from a full coverage audit; **implemented the same day**.
All six tiers are done and wired into the CI gate, so every push runs them and
a failure blocks the deploy. This file now records what exists, what each suite
guards, and the few things deliberately left open.

## Ground rules (unbreakable)

- **Hermetic, always**: work on temp copies of the archive, push only to
  scratch LOCAL bare remotes, mock every external service (YouTube, tasvideos,
  the forum) with a local HTTP server. The CI test job holds no FTP or push
  credentials.
- Tests never touch real archive data and never push to any GitHub repository.
- Member-authored content is read-only, including in fixtures derived from it.

## The suites

| Suite | Checks | Guards |
|---|---|---|
| `tests/test_generator.py` | 23 | page rendering per run state, act zones, imported panels |
| `tests/test_output.py` | 71 | output invariants (below) |
| `tests/test_validate.py` | 56 | one negative case per validator rule |
| `tests/test_movieparse.py` | 124 | parser fixtures, fuzz, confusion, bombs |
| `tests/test_derivation.py` | 32 | status/ranking, points, stars, history, news, case parity |
| `tests/test_security.py` | 35 | CSRF, sessions, SSO, traversal, magic bytes, scopes |
| `tests/test_robustness.py` | 12 | git recovery, concurrency, rollback |
| `tests/test_preview_parity.py` | 30 | server vs client markup renderer |
| `tests/test_archivist.py` | 89 | the whole community loop end to end |
| `tests/test_providers.py` | 62 | video platform parsing, refusals, thumbnails |
| `tests/test_layout.py` | 16 | what a browser actually measures on the page |

`tests/mkarchive.py` builds a minimal, fully-controlled archive; the suites
that assert exact numbers or "nothing anywhere contains X" use it so real
member content cannot drown the signal.

**Tier 1 — output invariants**: no dead internal links · every page is
`folder/index.html` · `.htaccess` shipped · assets resolve · cache-busting
token on every page · beta banner present with `ARCHIVE_REF=staging` and
absent on main · hostile author names and notes escaped · malformed markup
still balanced · `[M#]` resolution (known and unknown) · content-warning chips
and the 18+ gate · report anchors · `node --check` on the emitted app.js ·
server/client element contract · zero em dashes or "Legacy" in generated
chrome · every inline page script parses under `node --check` (app.js was the
only script ever checked) · game groups (a page only for a group holding more
than one game here, never a link to a group that has no page, hostile group
title escaped, withdrawn runs absent, group-scoped experts named) · the three
views of the Games page (all three offered, exactly one visible without
javascript, one card per group whose thumbnail is a collage of
distinct games, no game links inside it, one list row per game, every game page
carrying a group line, and an Unclassified card and page holding exactly the
games no group claimed).

**Tier 2 — derivation**: the full (reproductions × verifications ×
invalidated × imported × unclassified) matrix mapped to ranked/pending/history
· exact contributor points including the hard-system bonus, the neglect
escalator and its cap · star sums per game and system · supersession by author
set with frame deltas · author news across co-authors · exhaustive parity
between the archivist's and the validator's case resolution.

**Tier 3 — validator negatives**: 53 mutations of a known-good archive, each
asserting exit 1 *with that rule's own message*, plus a check that a missing
`jsonschema` fails loudly instead of skipping every schema check. Meta-tested
by gutting rules and confirming the suite goes red.

**Tier 4 — parsers**: byte-exact fixtures for 24 of 25 formats (lmp is a
heuristic cascade the fuzz layer covers instead) asserting frames, rerecords,
system and start type · fuzz over empty, tiny, random, truncated and
trailing-garbage input, asserting `parse()` never raises and never reports
negative frames · wrong-extension confusion · a compression bomb.

**Video platforms**: every URL shape each platform hands out, parsed to the
same id · refusals, above all a hostile URL that merely CONTAINS a platform
URL and a lookalike host · embeds are https player URLs · thumbnails via
direct template, via XML API and via JSON API (protocol-relative included) ·
an answer that is not an image, is oversized, or is a `javascript:` URL is
refused · a Niconico submission accepted end to end by the archivist.

**Tier 5 — security**: CSRF matrix over all 14 cookie-authed endpoints ×
{evil, ours, forum, no origin} · forged, expired, truncated and non-numeric
session tokens · SSO bad signature, unknown nonce, missing username · CORS
headers · path-shaped and overlong usernames · traversal game titles · files
whose magic bytes contradict their extension · oversized screenshots and
movies · every accepted YouTube URL shape and rejected host · thumbnail
maxres/hq/failure chain · expert scope (site vs system vs group, own vs other) ·
`/api/game/ratify` · a dozen rejection branches by status code.

**Tier 6 — robustness**: a genuinely wedged checkout heals itself (asserted to
wedge a plain checkout first) · a concurrent writer on the same branch ·
six simultaneous submissions taking distinct ids · a rejected request leaving
the archive byte-identical · preview parity across 14 block constructs.

## What the tests found (all fixed the same day)

- An unknown report kind or content-warning key took the **entire build** down
  (raw dict index).
- Two contributor acts sharing a date, description and payout made the
  profile's contributions table sort compare run dicts: **another build
  killer**, reachable by anyone reproducing two runs in one day.
- Truncated `.wtf` and `.3ct` movies parsed to **negative frame counts**,
  which would have entered the archive as ranking data.
- A crashed request could leave the intake checkout wedged, after which
  **every later submission 500s** until someone intervenes on the server.
- Usernames with spaces or markup produced unreachable profile URLs.
- The preview renderer mis-nested lists inside quotes and escaped fewer
  characters than the server.
- The validator silently skipped all schema checks when `jsonschema` was
  missing, and crashed with a traceback on a malformed `run.json`.
- Game groups (2026-08-16): a game linked to its group's page even when that
  group held only one game here and no page was built, so every such game
  shipped a dead link; and the group page named its experts from the lowercased
  permission list, printing "grp" instead of the member's real name.

## Still open

- **Suite structure**: `test_archivist.py` is still one long `main()` with
  shared state. The newer suites are independent, so this is now cosmetic;
  convert to `unittest` if it starts to bite.
- ~~Product decisions~~ **settled 2026-08-16 and pinned by tests**:
  1. One verification per member per run, permanently: the slot is spent even
     if the act is invalidated. Others may still verify.
     (`test_archivist.py`: invalidated verifier refused, another accepted.)
  2. Unclassified runs count as pending on their reproduction gate only.
     (`test_derivation.py`: home statistic asserted exactly.)
  3. `movie.frames` has `minimum: 0` in the schema.
     (`test_validate.py`: negative frames rejected.)
**Layout**: a real Chrome over the built site. Nothing visible collapses to
zero height · the group mosaic fills and covers its frame at every tile
count · no page scrolls sideways at 360px. Reverting the mosaic fix turns
it red. A missing browser is fatal in CI and a skip locally, so it can
never look green by being absent.

- **Not covered by design**: pixel-level visual regressions, live platform
  behaviour, Discourse-side rendering.
