# toolAssisted.run — Design Document

> The canonical snapshot of the site's rationale: what exists, how it is
> structured, why it works the way it does. It is written so that a person, or
> an AI agent with no other context, can continue the work from here alone.
> **Maintenance rule: this is a snapshot, not a log.** When a decision is made
> or changed, rewrite the section it belongs to so the document always
> describes the present; do not append dated entries. Decision *attribution*
> lives in git history and the site's own public logs, never here.

---

## 1. What this is

toolAssisted.run is an **open community archive of tool-assisted speedruns**
(speedruns, score attacks, superplays). The founding idea: **archival comes
first**; curation emerges from the community afterwards. Every verifiable work
is preserved the moment it arrives, and merit is decided in the open by the
people who care about it.

The community's **constitutional document** (Community Principles §1,
Governance §2, Terms of Use §3, Code of Conduct §4, Privacy Policy §5) lives at
https://github.com/ToolAssisted-run/.github/blob/main/profile/README.md and
**outranks every implementation choice**: when code and constitution disagree,
the code is wrong. The constitution is amended only through its own process
(§2.3.6: hard majority after a 14-day comment period; it takes full force on
2027-01-01, before which changes need no procedure). Cite clauses as `§2.3.4`.

Its pillars, compressed: innovation is first-class (tools documented, never
banned) · archival is immediate and automatic · gaming only, zero politics ·
everybody is welcome; a ban elsewhere is not a status here · we are not the
internet's police · moderation in moderation (logged, appealable, never
automatic) · the organization belongs to everyone (open source, open data) ·
credit is sacred (humans and tools attributed) · free forever (donations fine,
no commerce).

**The repositories:**

| Repo | Holds |
|---|---|
| `ToolAssisted-run/archive` | The data: every run, game, group, member record, role event, log. Facts only. |
| `ToolAssisted-run/website` | This repo: the static-site generator, the archivist service, the frontend, tests. |
| `ToolAssisted-run/.github` | The constitution (`profile/README.md`) and the branding kit (`branding/`). |

Related local paths: the archive checkout at `~/ToolAssisted-archive`; the
TASVideos backup corpus at `~/tasvideos-dumps` (mirrored to a private backup
repo); the TASVideos reference implementation at `~/tasvideos`.

---

## 2. The content hierarchy

```
system  (nes, snes, dos, flash, …)        systems.json: name + default fps
 └─ game  (games/<sys>/<slug>/)           game.json + categories.json
     └─ category option                   per-game dimensions (e.g. Goal),
     │                                    each option a key + label + rule
     └─ run  (runs/M<id>/)                run.json + movie + notes.md + files

group  (groups.json)                      a game family ACROSS systems
                                          (e.g. prince-of-persia); each game
                                          belongs to at most one group
```

- **Groups** gather one family of games across every system it appeared on.
  A game sits in at most one group (validator-enforced). Games no group has
  claimed belong to **Unclassified**, which is *derived, never stored*: the
  archive records facts, not what can be computed. The reserved key
  `unclassified` cannot name a real group. Every group has a page and a card,
  however empty: a group somebody just made IS the state worth seeing.
- **Categories** are per-game dimensions; each option carries a rule fragment,
  rules compose per combination. New options exist the moment they are
  created; experts refine the wording, and delete unused mistakes.
- **The Unclassified category** exists on every game: entertainment,
  experiments, playarounds. No defined goal; the run carries its own mandatory
  `goalDescription` (≤200 chars, shown in the ranking row). These runs cannot
  be verified, can be reproduced, and rank **solely by ★ likes** in their own
  always-visible shelf. They are never "pending".
- **The Uncategorized holding game** (`<sys>/uncategorized`, derived need):
  when a game record is deleted, its runs survive there as unclassified, each
  carrying a factual provenance line as its goal description.
- **Terminology**: always "group", never "series". Say "forum group" when
  Discourse's role groups are meant.

---

## 3. The people hierarchy

A person can relate to the site in these ways, from lightest to heaviest:

- **A credited name**: anyone may be credited on a run's author list,
  including people who have never heard of the site. A credit is text; it gets
  no profile, no record, no page. Honest attribution of every human author is
  the one hard rule (§3.3); tool disclosure is voluntary and encouraged.
- **A member**: somebody with a forum account who has logged into the site at
  least once. First login writes their record in `authors/` (exactly two
  facts: the username, and that they are here; written in a background thread
  so nobody waits on git to log in). Every act on a run is performed by a
  member; the validator enforces that every actor has a record.
- **An author**: a member credited on archived runs. Their profile lists their
  runs, stars, contributions, news, and role history.
- **A contributor**: a member who has earned contributor points (one
  reproduction, verification or console run suffices).

**Roles** (all recorded in `roles.json`, an append-only event log; who holds
what is the fold of it, never stored twice):

| Role | Granted by | Powers |
|---|---|---|
| **Founder** | Recorded role, irrevocable (§2.2.2); succession under §2.3.12 is a new grant, never a rewrite | Public reasoned veto (once per decision, never on moderation, overridable by hard majority); seats/unseats Steering Committee from `/founder/` (every use a public role event + a PM to the person); the only one who may delete a sitting Committee member's record |
| **Steering Committee** | The Founder (directly), or the Committee's own poll | Decides name claims and identity (it ALONE assesses identity — §2.5.8, §3.8); appoints experts at any scope (§2.5.3); records role decisions from Committee polls; deletes member records (never a seated member: the Committee does not eat itself); decides appeals |
| **Expert** (scoped) | Any Committee seat at any scope, or a strictly wider expert scope (downward, never sideways: equal scope cannot clone itself); annulled by Committee poll (simple majority, §2.5.4); resignation needs nobody | See "what experts do" below |
| **Moderator** | Committee poll | Forum moderation; enforce on everyone; may admonish but never remove Founder/Committee |

Committee thresholds (§2): **simple majority** = >50% of votes cast in a 7-day
window, no quorum (grants); **hard majority** = two thirds of all sitting
members (removals, veto override, amendments). The archivist reads decisions
from Discourse polls (`/api/role/decide`, `/api/expert/annul`): the poll must
be restricted to the committee group, public, closed, and meet the threshold
against `committee_size()` counted from `roles.json` — never from a forum
group's member count.

**Expert scopes** nest: `site` → system → `group:<key>` → `sys/slug` (game).
Any covering scope can act. Group experts are assumed experts of the games
inside the group. Authority over a group is derived, not granted: site scope,
the group's own scope, or covering every game in it (an emptied group belongs
to whoever made it). Deleting a game or group revokes the scopes over it in
the same commit: nobody holds authority over a ghost (validated on HELD roles
only; history may name things later deleted).

**What experts do** (every act logged, public, appealable):
invalidate faulty
reproductions/verifications/console verifications · resolve/dismiss reports ·
edit everything in their jurisdiction (see §4, "Edits") · create games in
their group, groups at site scope · file and (site scope) decide removal
requests · delete outright what was never a work · withdraw any run in
scope. Experts never
judge merit: reproduction and verification stay open to all; experts police
the trust layer's quality and curate taxonomy.

**Badges are about the movies** — the site shows exactly two: **Expert**
(unqualified; which scopes is on the member's page) and **Contributor** with
its milestone (Contributor, then 1k/5k/10k/25k — the leaderboard thresholds
themselves, one green chip filling up). Governance roles are recorded and
shown in each member's role log, never as chips beside a name. There is no
Founder badge.

**Standing interpretation, the Founder panel**: the constitution names no
seat/unseat mechanism, and the panel IS that mechanism: the Founder seats and
unseats Committee members through it and is accountable for using it
appropriately (every use is a public role event with a reason, and a PM to
the person). The Committee's own poll route stands beside it. This is a
deliberate reading, not an oversight; do not re-flag it.

**The panels**, reached from the account menu, each shown only to holders:
`/expert/` (your scopes, appoint, step down, pending ratifications, annul),
`/committee/` (open claims with masked emails, whole-site-expert appointment,
record a Committee poll decision), `/founder/` (seat/unseat Committee). Panels
run on need-to-know: no roster tables to browse.

---

## 4. The life of a run

### Submission (`/submit/`, session-authenticated)

Arrives with: game (type-to-find combobox; game context pre-filled and locked
when coming from a game page via `/submit/?game=<key>`; "Game not found? Add a
new game" creates a provisional one) · category (fetched per game from the
archive's raw URL on pick; "+ new category" creates a provisional option) ·
authors (chips picker; may credit non-members as text) · **encode link,
mandatory** (six platforms; the run's thumbnail derives from it) · emulator/
core (optional) · ROM name+sha1 (optional, hashed locally, **the ROM never
leaves the machine**) · movie file, parsed mechanically (25 TASVideos formats,
`archivist/movieparse.py`, GPL-3.0 with TASVideos contributors credited; the
rest of the repo is MIT) — or **video-only** (see below) · optional completion
date (real date, 1980+, not future; shown beside the submission date;
author-editable later) · voluntary content disclosures (mature/violent,
sexual, photosensitivity, strong language; sexual blurs thumbnails behind a
session-scoped 18+ overlay) · text attachments (allowlisted extensions incl.
.xml, UTF-8, ≤128 KB each/≤512 KB total/≤8 files, plus up to 4 extra movie
files) · notes (the author's write-up) · consent: **CC BY 4.0** license grant
plus agreement with all four constitutional sections.

The run is **archived instantly and appears immediately, as pending**. One
commit per run; git history is the public submission log. Duplicates are
refused at intake: same movie bytes (`movie.sha1`), the same work saved again
(same game+category+frames+author set), or for video-only the same encode URL.
Caps: 32 MB at intake (a human decides past that), 100 MB in the validator
(what a git host will hold). Movie frame counts are ≥0 by schema; a movie
whose own frame rate differs from the system default carries `movie.fps`
(times and ranks derive from frames over the movie's own rate).

**Video-only runs**: no input movie exists; the encode IS the run. The
submitter states the time through a segmented h/m/s/ms picker (a format
mistake is impossible; stored as `duration` seconds). Nothing exists to
reproduce, in emulator or on console: both gates are marked `not-applicable`
(a status only video-only runs may carry), the endpoints refuse the acts, and
the page says so plainly. Verification ranks it exactly like any other run.

### States and the ranking gate

**Verification is the ranking gate.** Reproduction gates nothing (it is a
recorded, paid act of assurance); console verification gates nothing.

| Stored enum | Shown as | Meaning |
|---|---|---|
| `none` | Pending | awaiting its first verification |
| `provisional` | **Verified** | one community verification; ranked |
| `confirmed` | **Verified (expert)** | a covering expert verified it; permanent |
| `imported` | Imported | verified+reproduced at the trusted source site, irrevocable |
| `not-applicable` | (explained in place) | video-only runs, repro/console gates |

The stored enum names never change; only display language does. The
expert-ness of a verification is **stamped on the act at act time**
(`expert: true`), because scopes change and facts do not; one `/api/verify`
endpoint serves both. Unclassified runs rank by likes and are never pending.

**One run per author set per category** is derived, not stored: the fastest
ranked run per exact author set counts; slower ones render in a History
subsection with frame deltas. A faster submission supersedes; nothing is
erased. Ranked tables sort by seconds (`run_seconds()` unifies frames/fps and
stated duration).

**Every event carries its arrival second**: beside the human-readable
`date` (day), event records (acts, invalidations, withdrawals, reports,
cases, role events, edits, deletions, claims, removal requests; and
`ratifiedAtTime`/`claimedAtTime` on games, groups and author records) carry
an optional `at` (ISO seconds, UTC), stamped by the archivist at write time.
Boards and logs sort by the moment (`at` falling back to `date`) and display
the day; run arrivals were always second-exact via git commit times. Events
predating the history collapse share the day-only date honestly.

### Acts (all: any member except the run's authors, one per member per
roster, spent forever — invalidation does not refund the slot)

- **Verification**: watched the encode, confirms the stated goal is met.
  Requires an encode. Goal-bound: moving a run to unclassified voids live
  verifications (system-invalidated, on the record).
- **Reproduction**: loaded the movie on their own setup, confirms it syncs.
  Mandatory ending screenshot (png/jpg/webp, magic-checked, ≤512 KB each/8 MB
  per run, never matching the ROM hash); optional notes forming a per-run
  how-to. Goal-free: survives category moves.
- **Console verification**: replayed on original hardware; mandatory public
  proof link, optional hardware/screenshot/notes. Never required; absence is
  a neutral dash. Imported runs inherit the source site's console flag
  (`status.console = 'imported'`).
- **Like** (★): anyone, once per run, never your own (rename-resolved). The
  one reversible act: a second press erases the like outright, no tombstone,
  no log — a like is a mood, not a judgement. Orders the Unclassified shelves.

Author self-acts are refused with names resolved through renames (see §6).
Imported runs refuse all acts: they are irrevocably verified.

### Community notes and roles on a run

Each run page carries reproducer/verifier/expert **role notes** (shared text
per role, editable only by holders of that role on that run; editors listed).

### Disputes (cases)

A dispute opens a case, never auto-disqualifies. The **verifier set is
snapshotted at open time**; only they vote, one each; one open case per run.
Reaffirmations beyond half the snapshot close it; all voting without that
majority, or enough withdrawals to make it impossible, uphold it. A
withdrawal immediately invalidates that member's own verification; an upheld
case invalidates every snapshot verification, deriving the run back to
pending; anyone else may then re-verify. Status holds while a case is open
(banner shown). CI derives case status from the votes: a stored status cannot
lie.

### Reports

Every run has a Report button (members): missing content warnings ·
spam/malicious/deceitful · not credited correctly · licensing/copyright ·
other. Reports get globally unique `R#` ids, live in `reports[]`, are
resolved or dismissed by a covering expert with mandatory public text, and
render with linkable anchors in the site log. Non-members with rights
concerns use **contact@toolassisted.run** (§10).

### Leaving the archive

- **Withdrawal**: any author of the run, or a covering expert, with a
  mandatory public reason. The run leaves every listing; the page becomes a
  tombstone; movie, record and history remain.
- **Erasure**: only when **every** credited author asks (§3.1); movie, notes,
  thumbnail and record all go, permanently. Tombstoned withdrawals with
  `contentRemoved: true` mark works whose files were taken down while the id
  and record stay (ids are never reused).
- **Deletion**: things that were never works — spam, tests, non-TAS,
  mistakes (§3.1.1). Experts delete movies/games/groups outright from the
  page (confirmation dialog + mandatory public reason); the Committee deletes
  member records (refused while they authored runs; a seated member is the
  Founder's alone; the Founder is nobody's). Every deletion lands in
  `deletions.json` and the site log; a deleted game's entry says where its
  runs went (the Uncategorized holding game).

### Edits (the record can be corrected; the history always shows)

- **Authors** revise their own runs (`/api/edit`): notes, emulator, completion
  date, goal description, encode, stated time (video-only), and the author
  list (refused if it would credit somebody who already acted on the run).
- **Experts** correct anything in their jurisdiction (`/api/expert/edit`),
  field by field: a run's stated time, goal (existing options; unclassified
  refused while live verifications exist), encode, goal description, notes,
  movie file (re-parsed, sha1 logged); a game's title and thumbnail
  (validated image ≤256 KB, shown on the page and preferred by the game
  card); a category option's label or rule (target `sys/slug:option`); a
  group's title and composition.
- Every edit, both kinds, is an event in `edits.json` (who/from/to/why;
  author revisions auto-reason "The author's own revision.") and reversible
  through git. Run pages carry a small "change history · N revisions" link to
  the folder's commit history. **What may never be edited by anyone through
  us**: the author list beyond the author's own logged self-edit path (who
  made a thing is moderation's question), and forum posts. Bulk sweeps over
  member content are forbidden absolutely; problems with member content go
  through reports/cases/invalidation.

---

## 5. Games and groups: lifecycle

- **Creation is free and real on arrival**: anyone creates a game (at
  submission or via the combobox) or a category option; it exists the moment
  it is made. **Ratification is retired as a mechanism** (2026-08-20):
  nothing is provisional, nothing waits for a vouch. This is about content
  taxonomy only: **a name claim still waits for the Steering Committee**
  (§6), and removal requests still wait for a site-wide expert; identity and
  removals are judgements, not creations. The counterweight is
  the fast lane: a creation that should not exist is deleted by an expert,
  logged, and reversible through git. Historical `ratifiedBy/At` (and
  `rejected`) fields survive on old records and in the site log's
  ratifications section as the record of who vouched while the mechanism
  existed; the validator keeps only their internal consistency.
- **Groups are acts, not hand edits**: `/api/group/create` (only games you
  already speak for) and `/api/group/edit`; every change logged.
- **Removal is a request, never an act**: filed with a reason, decided by a
  site-wide expert, both names and both reasons public. Granted removal
  unlists; runs and pages stay. (Outright deletion exists separately for
  non-works, §4.)
- **The game editor** (`/games/<key>/edit/`, linked from the game page's
  Expert menu, revealed only to covering experts, enforced server-side):
  identity (rename, thumbnail) and the **category manager**: one card per
  option with label and rule edited in place (public reason required),
  unused options deletable (a category with runs in it is their home and
  cannot be deleted), new options simply added. Endpoints:
  `/api/category/add` (option_key field: 'key' is the auth field) and
  `/api/category/delete`; every act lands in edits.json. Governance acts
  (removal request, delete) appear on both the editor and the game page's
  Expert menu.
- Every game and group page ends with the **Expert menu** (§9) holding the
  governance acts for those entitled; content editing lives on the editor.

### Planned: per-category metrics (decisions locked, build pending)

A category will define what it ranks by. Settled in discussion, to build:

- **Model**: a category option gains `metrics: [{key, label, type: time|number,
  better: lower|higher, unit?}]`; array order IS the tie-break hierarchy and
  the first entry is the primary metric, shown wherever time shows today.
  A reserved key `time` means the derived real time (movie frames/fps or
  stated duration) and is never typed for movie runs. **Absent `metrics`
  means the implicit classic metric** (real time, lower better): zero
  migration. Runs store stated values in `run.json` `metrics: {key: number}`
  (times as seconds).
- **Adding a metric to a category with runs**: every existing run gets an
  explicit empty value (0 / 00:00.000); experts fill them through the logged
  edit paths and the ranking re-sorts as values land. Nothing is unranked.
  `0` renders as the "—" placeholder and sorts LAST at its level regardless
  of direction (a zero is never a winning result), falling through to the
  next metric. **Removing a metric**: the comparator uses what remains;
  stored values are never deleted, they just stop being read.
- **Verification is untouched by metrics, absolutely**: it attests the
  category's goal was achieved; metrics only order the achievers. No value
  edit (author's or expert's) voids a verification; dishonest values are a
  moderation matter like any other.
- **Browse**: the Frames and Time columns coalesce into one untitled column
  showing each run's own primary metric; the "Fastest first" sort is
  REMOVED (shipped 2026-08-21): a flat cross-category list has no honest
  metric ordering. Hierarchy ranking lives on the leaderboards.
- **Submission**: every author-stated metric the category defines is
  required. On category pick, the metric fields appear in a
  **dashed-edge box just below the video-only checkbox**: segmented
  h/m/s/ms inputs for time-types, number boxes with the unit for numbers.
  The video-only stated-time input appears **only if `time` is among the
  category's metrics**; if the category defines no time metric, time is
  never asked for, and movie runs never type time at all (derived).
- **Full-hierarchy tie**: earlier arrival wins (second-exact arrival times).
- Editor: the category manager grows metric rows (label, type, direction,
  unit, reorder, add/remove), logged like every category edit.
- Still under discussion: remainder of the submission corner case (new
  inline categories and metrics; video-only runs in categories without a
  time metric), tie display, per-surface sweep details.

---

## 6. Identity

- **Accounts are forum accounts** (Discourse, DiscourseConnect SSO). An
  account is a username, an email, a password (bcrypt). The site sets one
  session cookie (`tar_session`, signed, 14 days, Secure/HttpOnly/
  SameSite=None) after the SSO round-trip; no cookie before login; no
  analytics anywhere. Signup requires agreeing to the four constitutional
  sections and the age floor (13, or the country's higher digital-consent
  age).
- **Held names**: every author name from the imported corpus (~1,284) is
  reserved in Discourse; nobody can register it but its owner. A name
  credited to an author from ANY other TAS site is held for them.
- **Claims**: `/api/claim/request` files one (one open claim per member);
  the **Steering Committee alone** decides (`/committee/` panel), seeing a
  **masked** form of the requester's forum email (`jo***oe@e****.com`),
  computed live, shown only to those entitled, **never stored**: the
  validator refuses any `@` in `claims.json`. Approval renames the forum
  account to the claimed name, unlocks self-import, and PMs the person;
  denial requires a reason and PMs it. Committee members may also **attest**
  an identity directly, publicly naming how they verified it (the one place
  the archive accepts judgement instead of proof — token-based proof was
  abandoned because it depended on another site's permissions, §3.8). A ban
  or inactivity elsewhere never blocks a claim.
- **A claim supersedes the registration name**: approving deletes the member
  record the old name wrote at first login (validator refuses a name that is
  both a record and another record's `claimedBy`). Nothing recorded under
  the former name is rewritten; instead all three derivation layers resolve
  names through the `claimedBy` map (`canon()`): credits keep their text but
  link, score and self-act-check as the member they became. Live sessions
  follow the rename too (`session_user()` translates), so nobody is stranded
  behind their old cookie.
- **Nobody gets a profile they never asked for**: records exist only for
  members; a mere credit stays text (this was enforced retroactively by
  deleting seeded records and collapsing the archive history that held them).

---

## 7. Points and gamification

Two currencies, recomputed at every build from the rosters, never stored:
**author score** (★ likes on your runs) and **contributor points**. Weights
(provisional, marked so on the site): first reproduction 100 + 2/day the run
sat waiting (cap +200) · later reproductions 25 · hard-to-reproduce systems
(flagged in systems.json) +50 on any reproduction · verification 20 ·
console verification 1000 (real hardware, a capture setup, a recording).
Badges by thresholds alone (1k/5k/10k/25k), never act counts. **No currency
buys anything** (anti-farming: a currency without privileges is not worth
gaming). Imports award nothing.

The **Contribute board** is the public worklist: needs-verification and
needs-reproduction tables with rising bounties, recent contributions in the
side rail (only work that actually scored), open cases, the contributor
leaderboard. **No claiming, no assignment**: anyone may do anything anytime;
first to finish earns. Anti-rubber-stamp posture: reward-first,
punish-manually — points mint immediately, contradiction opens a case, a
pattern of forgery is a human, logged, appealable matter, never automatic.
Accepted residual risk: a determined faker can farm unchecked runs until
caught; structural prevention was deliberately traded for openness.

---

## 8. Architecture and data flow

Read `ARCHITECTURE.md` for the code map (MVC layout of generator and
archivist, module responsibilities). What matters designwise:

- **The archive is a plain git repo, no database, no LFS.** Facts in, derived
  state out. Layout: `games/<sys>/<slug>/{game.json, categories.json,
  runs/M<id>/{run.json, movie, notes.md, attachments/, reproductions/,
  console/}}` plus `authors/`, `groups.json`, `roles.json`, `claims.json`,
  `edits.json`, `deletions.json`, `systems.json`, `schema/`, `validate.py`.
  Rosters are the facts; the stored `status` is a checked cache that CI
  refuses to let lie. Invalidated acts stay on the record with by/date/reason.
- **Three derivations must always agree** (change all or none):
  `archivist/records.py::sync_status`, the archive's `validate.py`, and
  `generator/model.py::eff_state`. Same for rename resolution
  (`identity.py::current_name` / `canon()` in the other two).
- **The archivist** (Flask, VPS, `/opt/archivist/`, systemd `archivist`) is
  the archive's single writer: validates mechanically (parse + schema, no
  emulation), commits and pushes over SSH with a deploy key
  (`GIT_SSH_COMMAND` in `/etc/archivist.env`, so any fresh clone can push).
  Identity: session first, shared key + explicit username as operator
  fallback; cookie-authenticated writes are CSRF-guarded by Origin. Reads
  refresh the checkout at most every 20 s; role projections reconcile to the
  forum every 600 s. Reached publicly through the forum's nginx at
  `https://forum.toolassisted.run/archivist/` (port 8100 is docker-subnet
  only).
- **The generator** builds the whole site from the archive checkout
  (`generator/build.py <archive> <out>`; `ARCHIVE_REF` names the branch in
  links, `SITE_BETA` gates the beta bar). Frontend is real files
  (`assets/app.js`, `assets/style.css`) shipped verbatim; pages embed JSON
  blobs the script reads (always `.replace('<', '\\u003c')`-armoured). Run
  arrival dates come from git history (`fetch-depth: 0` in CI), falling back
  to `importedAt`/`submitted`.
- **The pipeline**: the archivist fires the website's `deploy.yml` dispatch
  itself, in a background thread, the moment its push lands
  (`WEBSITE_DISPATCH_TOKEN` in the VPS env; `reason=archive-content` skips
  the website's code-test gate) → build + deploy to **GitHub Pages**, about
  **30 s act-to-published**. Two fallbacks stand behind it: the archive
  repo's `rebuild-site` job fires the same dispatch before it validates
  (`ARCHIVE_ACTION_WRITE_SECRET`; the deploy concurrency group coalesces the
  pair, and the job stays red on an invalid push), and a six-hourly schedule
  backstops both. Both tokens are the same non-expiring fine-grained token
  (website repo only, Actions read-write). A six-hourly scheduled rebuild is the safety net against the
  token expiring silently. A **completeness guard** stops any deploy missing
  a run page or a core asset; deploys never cancel each other
  (`cancel-in-progress: false`, bursts coalesce). Website pushes run the full
  suite before deploying; a red suite keeps the last good build. The site's
  copy says changes appear "in a few minutes", which is the honest chain.
- **The site serves its own images**: thumbnails at `/thumbs/`, proof
  screenshots at `/shots/` (raw.githubusercontent 429s a page with hundreds
  of hotlinks). Movie downloads stay on raw, one click at a time.
- **Encodes come from six platforms** (YouTube, Niconico, Bilibili, Vimeo,
  Dailymotion, Internet Archive), registered once in
  `archivist/providers.py`: hosts + id pattern (both must match) + embed URL
  + thumbnail source. Everything (validation, players, submit copy, client
  host list, import resolution) derives from the registry; adding a platform
  is one entry. Twitch absent (authenticated thumbnails); Dailymotion cannot
  prove existence (community catches dead links).
- **The forum** (Discourse, self-hosted): identity provider, discussion
  home, notifications. Every run gets a topic (created in the same commit as
  the archival, so the pointer is never lost); every game gets an **anchor
  topic** under its tag — the tag page IS the game's forum home
  (`max_tag_length` raised to 60). Run pages proxy their thread through the
  archivist (`/api/discussion`, 60 s cache) and accept replies session-only,
  posting under the member's own Discourse name. Role groups on the forum
  are **printed projections** of `roles.json`, one-way, reconciled
  periodically; joining a forum group grants nothing. Private messages are
  never relayed anywhere.
- **Discord notifications** (`DISCORD_WEBHOOK_URL`): one line per event,
  links inside representative words (`<>` suppresses the preview); a movie
  is named the way people say it, the name carrying the link:
  `[\[SNES\] Prince of Persia](<url>) by eien86, Challenger`. Mentions
  disabled, sent only after the archive write landed, and
  **held until the page they link answers 200** (up to
  `NOTIFY_LINK_WAIT_SECONDS`). New-movie lines carry the run thumbnail as an
  embed. Imports notify once per batch. Forum posts relay through a
  HMAC-verified Discourse webhook, skipping PMs and the bot's own posts.
- **Static-first is a commitment**: no server search endpoints, no
  server-rendered pages. Pages carry small indexes; per-game payloads are
  fetched from the archive's raw URL on demand.

---

## 9. UI rules (apply to every future surface)

- **Pick, never type**: anything registered (game, member, group) is chosen
  through a type-to-find selector (datalist or chips picker), never a bare
  text box. **A picker never offers what would be refused**: grant lists
  offer who lacks, remove lists who holds, group pickers only ungrouped
  games. Free text only for things that do not exist yet, and author lists.
- **Buttons: one green, one verb.** Every action button is black-over-green
  (`.btn`; the "quiet" variant is the same green); only danger (red) and
  warn (amber) differ, because they mean something. Labels are a single verb
  ("Save", "Create", "Record"); exceptions need a real reason (navigation
  buttons name destinations, consent keeps its sentence).
- **Busy state**: every archivist-triggering button disables, goes flat and
  greyscale, and spins (steps(8)) until the request answers.
- **The Expert menu**: every expert-only form on run, game and group pages
  gathers into one box at the very bottom — faint orange, dashed border,
  headed "Expert menu" — revealed only to covering experts, answering into
  its own message line.
- **Collapsed folds read as buttons** (bordered, monospace), never as bare
  text; open-by-default section folds stay headings.
- **Fail-proof inputs beat validated inputs**: the stated-time picker is four
  clamped number boxes, not a format to get wrong. Prefer this shape.
- **Voice**: no em dashes in user-facing copy (they read as AI-written); use
  periods, semicolons, colons, or " · ". The "—" empty-value placeholder in
  data cells is typography and stays. Notifications are one-liners.
- **Brand**: Signal Green `#22C55E` accent ("the color of a passing check");
  ink `#0F172A`/`#F1F5F9`; JetBrains Mono (technical/wordmark) + Inter (UI);
  surfaces on a soft ground with bold 1.5–2 px delineation; **frame-quantized
  motion**: CSS `steps()`, never easing. Assets in the `.github` repo's
  `branding/`.
- **Responsive traps**: `.wide`/`.narrow` are text-swap classes
  (display:none below 560 px) — never use them as layout modifiers; the
  layout modifier is `fullw`. Run pages use the full window (`wide=True` on
  `page()`); everything else keeps the 1160 px wrap. Nothing may scroll
  sideways at 360 px.
- **Honest failure**: an unreachable archivist shows an amber "archivist
  unreachable" marker with a retry; reading never needs the archivist.
  The submit form arms the standard leave-page dialog once anything changes.
- Every page ends in the shared footer (constitution links, site log, social
  icons as CSS masks); the beta bar rides `SITE_BETA` (flip to `0` when the
  beta ends).
- **Freshness against the Pages cache**: GitHub Pages serves every page with
  `max-age=600`, so a browser can show a 10-minute-old leaderboard while the
  site rebuilt in ~40 s. Every build ships `/assets/buildstamp.json`; each
  page knows its own build (`window.TAR.v`), the client compares the two
  through an uncached fetch, and a green fixed pill ("This page has been
  updated · Refresh") offers the reload. Never automatic: the reload is the
  reader's.

---

## 10. Licensing, legal, privacy

- **The licensing chain**: native submissions are granted **CC BY 4.0** by an
  explicit consent checkbox (§3.2); works imported from TASVideos remain
  under their original **CC BY 2.0**. The archive's `LICENSE.md` states both,
  the attribution rules, and the contact. Imported notes contain **only the
  authors' own text** (judge/staff text stripped at the `----`/`[user:]`
  boundary; publication descriptions never used); every imported run links
  its exact source and names the license. CC BY 4.0's "indicate changes" is
  satisfied by `edits.json` + the change-history link.
- **Imports are voluntary, author by author** — never bulk. A claimed member
  imports their own catalogue from their profile: scan lists the whole
  catalogue (tickable pending rows, dimmed already-archived rows), the member
  picks by hand, batches of six, one commit per batch, idempotent by
  publication id. Picking a co-authored work is that member's stated
  responsibility that co-authors agree (§3.7); any author may withdraw it,
  all together may erase it. The importer never crawls the source site: it
  reads a local backup corpus, refreshed by a daily cron.
- **ROMs never touch the site.** Hashes and names are facts.
- **Privacy commitments** (§5, and they bind the implementation): no
  analytics, no tracker, one session cookie after login; the archive holds
  no personal data beyond usernames; emails never shown (masked, transient,
  Committee-only during claims — §5.4.1) and never stored in the archive;
  account deletion removes account, email and personal data while public
  contributions persist like git history (§5.8); third parties a page talks
  to are listed honestly (§5.9 — the reason Gravatar was switched off).
- **The public legal contact is contact@toolassisted.run** (§5.11: operator
  identity; §3.10: infringement claims from anyone, member or not; §5.12:
  data rights). The address appears in the constitution and the archive's
  LICENSE.md.
- **Accepted risks, on record**: thumbnails/screenshots are stills of
  copyrighted game footage (industry-standard exposure; the report route and
  responsiveness are the mitigation) · the 18+ gate is a click-through (no
  adult content archived; revisit if that changes) · no registered US DMCA
  agent (GitHub's own process fronts Pages content).

---

## 11. Operations

- **The legacy Infomaniak web-hosting plan is kept deliberately**: nothing
  serves from it (DNS-audited), but the domain's mail service rides its
  bundle, so terminating it risks the toolassisted.run mailboxes. Do not
  propose cancelling it.
- **Hosting**: the site on **GitHub Pages** (apex A records to GitHub, `www`
  CNAME, TLS by GitHub, `CNAME` file shipped in every build; Pages does no
  rewrites — the self-contained `404.html` forwards legacy `/stage/` links —
  and no response headers). Forum + archivist on an Infomaniak VPS
  (Ubuntu LTS, Docker Discourse owning 80/443, archivist beside it). Mail
  through Infomaniak (`mta-gw.infomaniak.ch`).
- **Everything works on the archive's `main`**. The beta's `staging` branch
  was merged in (a two-parent commit whose tree is staging's; nothing left
  behind) and stays **frozen** so old forum links into it keep resolving.
- **Deploying the archivist** = copy **all** of `archivist/*.py` to
  `/opt/archivist/` + `systemctl restart archivist`. Deploying the site =
  push to main; never deploy by hand unless CI is broken.
- **Secrets** (never committed): `/etc/archivist.env` on the VPS
  (`SUBMIT_KEY`, `DISCOURSE_*`, `SESSION_SECRET`, `SSO`, `DISCORD_WEBHOOK_URL`,
  `GIT_SSH_COMMAND`, `ARCHIVIST_BRANCH=main`); deploy keys under
  `/opt/archivist/`; `ARCHIVE_ACTION_WRITE_SECRET` on the archive repo — a
  non-expiring fine-grained token (resource owner ToolAssisted-run, website
  repo only, Actions read-write; the org's 366-day maximum-lifetime policy
  was lifted for this). GitHub still auto-revokes any token unused for a
  full year; ours fires on every act, so that only matters if the site goes
  dormant. If dispatches ever stop, deploys degrade silently to the
  six-hourly rebuild: check the token first. The retired FTP_* secrets are
  deleted from both repos (values survive in the operator's local netrc).
- **Backups**: the archive is backed up by being git, everywhere; the
  TASVideos corpus lives in a private backup repo, refreshed by a daily VPS
  cron (05:17 UTC, paced, single-threaded; the only thing that ever touches
  tasvideos.org). Discourse dumps daily (5 kept locally) and a second cron
  (04:30, `/usr/local/bin/ship-discourse-backups`) ships them to Infomaniak
  Swiss Backup over rclone/Swift (`swissbackup:discourse-backups`, 30-day
  remote retention; config in root's rclone.conf on the VPS). The ship log
  is silent on success; verify with `rclone ls`, not the log.
- **History is rewritten only deliberately, for records about people**, and
  a force push is always a human hand, never tooling (the auto-mode
  classifier blocks it by design). After any rewrite: GitHub keeps old
  commits fetchable by SHA until Support gc's the repo, and clones elsewhere
  persist — §5.8 says so rather than promising what git cannot deliver.
- **Code quality**: `bash tools/sonar.sh` runs a local SonarQube (docker)
  over the repo per `sonar-project.properties`.

---

## 12. Testing

- **Hermetic, absolutely**: suites never touch live archive data (temp
  copies; fixtures build their own governance records from scratch — live
  roles/claims drift under long-running suites), never push to GitHub
  (scratch local bare remotes), never call external services (local mock
  servers; notification waits set to 0). The CI test job holds no
  credentials by construction.
- `tests/` holds ~14 suites (generator rendering, output invariants,
  validator negatives, movie parsers, derivations, archivist end-to-end,
  security, robustness, preview parity, client runtime against real page
  DOM, layout in real Chrome, providers, news feed). `tests/mkarchive.py`
  builds synthetic archives for exact-value assertions;
  `mkarchive.prune_superseded` brings live-archive copies to the state the
  claim flow enforces. `TESTPLAN.md` is the map.
- **Lessons encoded as practice**: a feature is not shipped until the
  generator has built an archive containing its output · CI-watching is not
  optional · byte-golden diffs make refactors provable · fixtures must carry
  every shape that ever crashed a build (empty games, decided claims).

---

## 13. Open items

- The **contact@toolassisted.run mailbox** must be created at Infomaniak
  (the constitution already names the address).
- Contributor point weights are provisional; the community settles them.
- miniHawk's own movie format joins `movieparse.py` when it lands.
- Full-launch checklist: re-review policy drafts and point weights; flip
  `SITE_BETA=0`.
- **v2, deferred by explicit decision**: automated client-side reproduction
  (browser/desktop miniHawk replays locally, ROMs never leave the machine,
  signed receipts with state-hash samples corroborate independently). Revive
  when miniHawk's WASM determinism is proven.

## 14. Retired, so nobody trips on it

The hand-written landing page, `/stage/` indirection, the 21-view design
mock, the `/experts/` page (roles live on member pages), the `experts.json`
snapshot (now the `roles.json` log), the TASVideos-token claim flow (now
Committee judgement), the two-verification "full" tier (now the expert
stamp), the reproduction ranking gate (now verification), the "tools used"
field (notes carry tooling), the per-page giant game `<select>` (now the
combobox), FTP hosting (now Pages), and the beta `staging` branch (merged,
frozen). Git history holds them all.
