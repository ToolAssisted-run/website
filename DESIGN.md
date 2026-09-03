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
Governance §2, Submission Policy §3, Terms of Use §4, Code of Conduct §5,
Privacy Policy §6) lives at
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

**A system is created like a game: by whoever needs it** (`/api/system/create`).
The submit form asks for the system first, as the substep before the game, and
it narrows the game list; a machine nobody has listed yet is added right there
from **its name alone**, the key made from the name (lowercase, spaces to
hyphens, everything else dropped: "Bandai Terebikko" becomes
`bandai-terebikko`) and the frame rate starting at 60. A run on an unlisted
machine is a run the archive wants, and a rate is a correction rather than a
reason to turn somebody away. The panels send the key, rate and flags outright.

**A whole-site expert or the Committee corrects one** (`/api/system/edit`, on
both panels): name, frame rate, and the two flags (hard to reproduce, plays
back on original hardware), each logged in `edits.json` like any expert edit.
The key is never among them: it opens every game address filed under the
system, and a run cannot move between systems. **Removing one is the Steering
Committee's alone** (`/api/system/delete`), refused while any game is filed
under it or any expert holds scope over it, and it asks for a public reason.


- **Groups** gather one family of games across every system it appeared on.
  A game sits in at most one group (validator-enforced). Games no group has
  claimed belong to **Uncategorized**, which is *derived, never stored*: the
  archive records facts, not what can be computed, and a derived group can
  never be deleted. The reserved keys `uncategorized` and `unclassified`
  cannot name a real group. Every group has a page and a card,
  however empty: a group somebody just made IS the state worth seeing.
- **Categories** are per-game dimensions; each option carries a rule
  (markdown, up to 2000 characters; a subcategory adds its own fragment),
  and a game may carry game-wide rules of its own (#64, same markdown and
  cap, a game property edited in the game editor); game rules, then the
  category's, then the subcategory's compose per combination and open in a
  "View rules" dialog on the game page rather than cluttering the board (#60). New options exist the moment they are
  created; experts refine the wording, and delete unused mistakes. A
  category's (or subcategory's) **key** — the address rankings, links and
  runs point at — is renameable from the game editor (#69: kebab-case,
  unique per game, `unclassified` reserved); every run in it follows the
  rename in the same commit, nothing is voided, and the editor runs the
  rename last so the save's other edits still find the old address.
- **Subcategories** (issue #43): a category option may define a second
  level (`subcategories`: key, label, optional rule fragment), e.g. Episode 1
  → any%, 100%. A run in such a category names one (`category.sub`,
  validator-enforced, required exactly when the category defines some);
  each subcategory is its own leaderboard, labelled "Category · Sub", its
  rule composed from both fragments, ranked by the category's metrics (a
  subcategory has none of its own). Where no subcategories exist nothing
  mentions them: no selector row, no label suffix, no form field. Created
  from the create-category page ("subcategory of") or the game editor; the
  first subcategory added to a category takes the runs already in it;
  removable while empty; experts move runs with goal `option/sub`. The
  issue's full cross-product of dimensions was judged wider than needed.
- **The Unclassified category** exists on every game: entertainment,
  experiments, playarounds. No defined goal; the run carries its own mandatory
  `goalDescription` (≤200 chars, shown in the ranking row). These runs cannot
  be verified, can be reproduced, and rank **solely by ★ likes** in their own
  always-visible shelf. They are never "pending".
- **Terminology**: always "group", never "series". Say "forum group" when
  Discourse's role groups are meant.

---

## 3. The people hierarchy

A person can relate to the site in these ways, from lightest to heaviest:

- **A credited name**: anyone may be credited on a run's author list,
  including people who have never heard of the site. A credit is text; it gets
  no profile, no record, no page. Honest attribution of every human author is
  the one hard rule (§4.3). The tool used gates reproduction only (§4.3):
  a run is archived and verified without it, but its reproduction bounty is
  waiting until the tool is named (the empty reproduction roster says so,
  and names the tool when it is known); the submit form asks but never
  requires, and anything beyond the tool is a badge, never an obligation
  (§1.10).
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
| **Steering Committee** | The Founder (directly), or the Committee's own poll | Decides name claims and identity (it ALONE assesses identity — §2.5.8, §4.8); appoints experts at any scope (§2.5.3); records role decisions from Committee polls; deletes member records (never a seated member: the Committee does not eat itself); decides appeals |
| **Expert** (scoped) | Any Committee seat at any scope, or a strictly wider expert scope (downward, never sideways: equal scope cannot clone itself); annulled by Committee poll (simple majority, §2.5.4); resignation needs nobody | See "what experts do" below |
| **Moderator** | Committee poll | Forum moderation; enforce on everyone; may admonish but never remove Founder/Committee |
| **Editor** (§2.6) | A single Committee seat (`/api/editor/appoint`, from the Committee panel's "Appoint an editor"); removed by Committee poll (`/api/role/decide`, simple majority, §2.6.3) | The library's shape, nothing else: create/edit/delete categories and groups, edit game identity (title, thumbnail), move runs between categories (`/api/expert/edit` kind=run, field `goal` only), place games into groups at creation. Unscoped. No power over people (appoints nobody), none over runs themselves (no notes/encode/movie/metric edits, no deletions, no invalidations, no Edit-run panel); their verifications stay community-weight. Badge: an "Editor" chip styled like the contributor tiers. |

A Committee seat is a forum administrator by virtue of the seat: the
archivist's role publishing (`forumapi.publish_group`) requests forum admin
on every committee grant and revokes it on removal. Discourse only grants
admin over the API after the acting admin confirms by email, so a grant
lands as a confirmation link in the Founder's inbox; revocation is
immediate. Forum tags stay enabled (every game's "Discuss" link is a tag
page and the archivist tags each run's topic) but only staff may create or
apply them, so members never meet a tag picker.

Committee thresholds (§2): **simple majority** = >50% of votes cast in a 7-day
window, no quorum (grants, and every removal except a Committee seat's);
**hard majority** = two thirds of all sitting members (unseating the
Committee, veto override, amendments). The archivist reads decisions
from Discourse polls (`/api/role/decide`, `/api/expert/annul`): the poll must
be restricted to the committee group, public, closed, and meet the threshold:
a simple majority is counted against the poll's own votes cast (`voters`),
a hard majority against `committee_size()` counted from `roles.json`, never
from a forum group's member count.

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
their group, groups at site scope · delete outright what was never a
work · withdraw any run in scope. Experts never
judge merit: reproduction and verification stay open to all; experts police
the trust layer's quality and curate taxonomy.

**Badges**: the site shows four chips beside a name: **Steering Committee**
(red), **Expert** (unqualified; which scopes is on the member's page),
**Editor** (blue), and **Contributor** with its milestone (Contributor, then
1k/5k/10k/25k: the leaderboard thresholds themselves, one green chip filling
up). Founder and Moderator are recorded and shown in each member's role log,
never as chips.

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
when coming from a game page via `/submit/?game=<key>`; a question + button
beside the selector points at `/create-game/` in a new tab — the selector
never creates) · category (fetched per game from the archive's raw URL on
pick, metric definitions included; "Category not there?" points at
`/create-category/?game=<key>`) · the category's stated metrics, required,
in a dashed box (§5) · authors (chips picker; may credit non-members as
text) · **encode link,
mandatory** (six platforms; the run's thumbnail derives from it) · emulator/
core (optional) · the files the movie was made against, 0 to n rows of
name+sha1 (`contract.files`: ROMs, disc images, executables, sources; each
picked and hashed locally, **the file never leaves the machine**, or typed;
revisable from Edit run; the legacy single `contract.rom` on older records
is shown as one row and never rewritten) · movie file, **optional**: a run
without one is video-only (see below). Any extension is archived as it is;
a supported format is parsed mechanically (`archivist/movieparse.py`:
Chimera's own `.chimeraProject`, the TASVideos emulator formats, classic
ones included (smv, zmv, fcm, fmv, vmv,
nmv, mmv, mcm, pjm, pxm, mc2, ymv, bkm, dof, rec), plus the game-specific
tools the Tools page marks with a check. A Chimera project IS the movie, so
what it reports is the run's own length: the frame count stops at the last
frame anything is pressed on, Chimera's own rule for its "Last input"
marker, and idle frames left after it are warned about rather than counted.
Its rate is read the way Chimera reads it: the cycle count over the clock
rate first (measured), then the recorded vsync, then the archive's figure
for the system, with a round 50 or 60 treated as nominal and deferred. Two
of Chimera's cores (PCSX2, Flycast) end a frame when the machine presents a
picture rather than at a vblank, so a game that drops frames makes a
frames-over-rate time a lower bound; the form says so, and the author states
the time regardless; every parser validated against
real movies from the TASVideos corpus. The Tools page carries three tables:
current emulators, classic emulator formats (the retired rerecording
emulators whose movies still parse in full), and game-specific tools, each
row marked parsed or not; GPL-3.0 with TASVideos contributors credited; the rest of the
repo is MIT), and a parse failure or an unknown format is a warning at the
form, never a refusal · optional completion
date (real date, 1980+, not future; shown beside the submission date;
author-editable later) · content warnings, required where they apply (§3.1.7; mature/violent,
sexual, photosensitivity, strong language; sexual blurs thumbnails behind a
session-scoped 18+ overlay) · text attachments (allowlisted extensions incl.
.xml, UTF-8, ≤128 KB each/≤512 KB total/≤8 files, plus up to 4 extra movie
files) · notes (the author's write-up) · consent: **CC BY 4.0** license grant
plus agreement with the Community Principles (§1), the Run submission policy
(§3.1) and the Multiple author submission policy (§3.2); the constitution's
§3 intro says submitting is that agreement, so the checkbox and the
document state the same thing.

The run is **archived instantly and appears immediately, as pending**. One
commit per run; git history is the public submission log. Duplicates are
refused at intake: same movie bytes (`movie.sha1`), the same work saved again
(same game+category+frames+author set), or for video-only the same encode URL.
Caps: 32 MB at intake (a human decides past that), 100 MB in the validator
(what a git host will hold). Movie frame counts are ≥0 by schema; a movie
whose own frame rate differs from the system default carries `movie.fps`
(the fallback rate for older runs whose time still derives from frames).

**Video-only runs**: no movie file was provided (there is no checkbox: the
absence of the file is the fact; the API's `video_only` flag survives for
callers, and a flag plus a file is refused as a contradiction); the encode
IS the run. Nothing exists to
reproduce, in emulator or on console: both gates are marked `not-applicable`
(a status only video-only runs may carry), the endpoints refuse the acts, and
the page says so plainly. Verification ranks it exactly like any other run.

**The stated time is the record** (fully decoupled from the movie): whenever
the category ranks by time, the submitter states the run's time through the
segmented h/m/s/ms picker (a format mistake is impossible; stored as
`duration` seconds). The field is never filled in for them; an **Import from…**
selector beside it lists the sources the form has actually checked, each with
its value: the movie file (when it parsed to a time) and the video encode
(when the platform states its length; the archivist asks YouTube, Niconico,
Bilibili, Vimeo and Dailymotion through `providers.duration_seconds`, cached
with the encode check). Picking one fills the segments and the selector
resets. Numeric metric fields carry their own **From movie** button that
fills the movie's frame or step count (for categories ranked by frames,
steps or ticks; formats whose frame count is readable but whose rate is not,
like `.otts` and `.gmtas`, still feed this). **Importing is a copy/paste,
never a commitment**: it only writes the value into the field, which stays
hand-editable like any typed value. Every import control sits on the same
line as the field it fills, and its sources track the form live: removing
the movie file takes its option out of the selector and disables the
From movie buttons. `run_seconds` prefers the stated
`duration` and falls back to frames/fps for older runs that never stated
one; a time-less category stores no duration at all (§5).

**"You may also like"** (every run page): a reel of 8 cards filled closest scope
first — the run's own designated picks (`related` on run.json: up to 8 run ids,
chosen in the edit form through a search-as-you-type chips picker over
`/api/search?kind=runs`, author- or expert-edited, presentation only so nothing
is voided), then the same category in a different subcategory, the same game,
the same group, the same system, then the site's most liked, most viewed and
most recent. Within every computed bucket the verified come first, then the most
liked, then the most recent; designated picks keep their designated order
(`reel_for` in views/run_pages.py). On desktop viewports, the section sits in
the space to the right of the video player in a vertical scrollable column
(YouTube style) with compact horizontal cards; on mobile/tablet viewports, it
wraps down below the player as a horizontal scrolling reel.

### States and the ranking gate

**Verification is the ranking gate.** Reproduction gates nothing (it is a
recorded, paid act of assurance); console verification gates nothing.

| Stored enum | Shown as | Meaning |
|---|---|---|
| `none` | Pending | awaiting its first verification |
| `provisional` | **Verified** | one verification, from anybody; ranked |
| `confirmed` | **Verified** | same: a verification that happened to be an expert's. No tier, no permanence; shown identically |
| `imported` | **Verified** | verified+reproduced at the trusted source site, irrevocable. No badge of its own anywhere: the run page's Status box alone says where it was verified, names the importer and the CC BY attribution |
| `not-applicable` | (explained in place) | video-only runs, repro/console gates |

**There are no verification tiers**: zero verifications is not verified,
one (from whoever) is verified, and the verifier is trusted by default; a
covering expert may later invalidate a wrong verification, which is the only
expert power over the gate. The stored enum names never change, and the
expert-ness of a verification stays **stamped on the act** (`expert: true`)
as a fact about who acted, which nothing on the site distinguishes. One
`/api/verify` endpoint serves everybody. Unclassified runs rank by likes and
are never pending.

**One run per author set per category** is derived, not stored: the fastest
ranked run per exact author set counts; slower ones render in a History
subsection with frame deltas. A faster submission supersedes; nothing is
erased. Ranked tables sort by seconds (`run_seconds()` unifies frames/fps and
stated duration).

**Every event carries its arrival second**: beside the human-readable
`date` (day), event records (acts, invalidations, withdrawals, reports,
cases, role events, edits, deletions, claims; and
`ratifiedAtTime`/`claimedAtTime` on games, groups and author records) carry
an optional `at` (ISO seconds, UTC), stamped by the archivist at write time.
Boards and logs sort by the moment (`at` falling back to `date`) and display
the day; run arrivals were always second-exact via git commit times. Events
predating the history collapse share the day-only date honestly.

### Acts (all: any member except the run's authors, one live act per member per
roster; an edit that obsoletes an act reopens the slot, while an expert
invalidation of a faulty act does not)

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

- **Withdrawal**: a voluntary act of the run's own authors alone, with a
  mandatory public reason (an expert who must remove a run deletes it, on
  the record; historical expert withdrawals keep their `role: expert`
  stamp). The run leaves every listing; the page becomes a tombstone;
  movie file, record and history remain. The form is shown only to the
  run's authors.
- **Erasure**: only when **every** credited author asks (§4.1); movie, notes,
  thumbnail and record all go, permanently. Tombstoned withdrawals with
  `contentRemoved: true` mark works whose files were taken down while the id
  and record stay (ids are never reused).
- **Deletion**: things that were never works — spam, tests, non-TAS,
  mistakes (§4.1.1). Experts delete runs/games/groups outright from the
  page (confirmation dialog + mandatory public reason); the Committee deletes
  member records from its own panel (`/committee/`, never from the member's
  page: a delete button on a profile reads as a threat; refused while they
  authored runs; a seated member is the Founder's alone; the Founder is
  nobody's). Every deletion lands in
  `deletions.json` and the site log; a deleted run's own forum announce
  topic is closed with the reason posted into it (never deleted: member
  replies stay readable) and Discord is notified, both best-effort after
  the archive write; deleting a game deletes every run in
  it, one logged entry per run beside the game's own, because the use case
  is content that should never have been archived (rule violations, spam).
  A genuine work in a wrong game record is moved by an expert through
  `/submit/?move=M1234`, never deleted with the record. The expert must cover
  both games; `/api/run/move` relocates the intact run folder, changes
  `run.json`'s game/category and invalidates verifications bound to the old
  goal, records who/from/to/why in `edits.json`, and commits the whole
  correction once. A move is refused when the run lacks values required by
  the destination category; structural correction never invents scoring.

### Edits (the record can be corrected; the history always shows)

**The general voiding rule: an edit voids exactly the acts that attested
what it changed.** A change to the run's **goal or scoring** (its category,
stated time or any metric value) invalidates every live verification (the run leaves the
ranking until verified again). A change to its **reproduction
information** (the movie file, the tool it plays in, the files it was
made against) invalidates every live reproduction and console
verification (they synced the old setup). Nothing else voids anything:
encode, notes, dates, disclosures and authors are free. The archivist
enforces this on both edit paths (`SCORING_FIELDS` / `REPRO_FIELDS` +
`metric:*` in `void_acts_for`), logs it, and a dry run announces it
(`would_void`), so the Edit run form asks "are you sure" before sending.
The prefilled record sent back unchanged is never a change: only a real
difference is recorded or voids anything. Times are compared at the
resolution the time picker can express: every value the picker is handed
(the record, an import from the movie, an import from the encode, a
restored draft) is rounded once to whole milliseconds, and the archivist
compares the value it gets against that same rounding. A round trip is
therefore silent even when the stored duration is finer than a
millisecond, and a correction of one millisecond is still a correction.
This is what keeps replacing a video link from touching the scoring.

- **One "Edit run" panel serves authors and covering experts** (`/api/edit`):
  notes, emulator, completion date, goal description, encode, stated time
  (whenever the category ranks by it), metric values, and the movie file. Authors
  alone may also revise the author list (refused if it would credit somebody
  who already acted on the run) and upload **supplementary files** (same
  validation and caps as submission's attachments, counted together; stored
  under `attachments/` with role `supplementary`; a taken name is refused).
  An expert using the panel must state a public reason (8–500 chars) and can
  never touch the author list or the uploads; their changes log exactly like
  `/api/expert/edit` ones. Replacing a movie invalidates the live reproductions
  and console verifications that synced the old file, but keeps those historical
  acts in their rosters marked **Obsoleted**; the run then needs reproduction
  again.
- **Experts** additionally correct structural facts through
  `/api/expert/edit` (API; the old per-field run form is retired from the
  page): a run's goal (existing options; unclassified refused while live
  verifications exist); a game's title
  and thumbnail (validated image ≤256 KB, shown on the page and preferred by
  the game card); a category option's label, rule or metrics (target
  `sys/slug:option`); a group's title and composition.
- **Moving a run between games** uses the submit form in a separate expert-only
  move mode (`/submit/?move=M1234`, reached from the run's Expert menu). It
  keeps only two panels: destination game/category/subcategory, then the public
  reason and Move. Newly created games or categories require refreshing this
  already-open form. Editors cannot use this path; an expert must cover both
  the source and destination game.
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

- **Creation is free and real on arrival**: anyone creates a game or a
  category through the dedicated creation pages (never from inside the
  submit selectors; see §5 metrics); it exists the moment it is made. **Ratification is retired as a mechanism** (2026-08-20):
  nothing is provisional, nothing waits for a vouch. This is about content
  taxonomy only: **a name claim still waits for the Steering Committee**
  (§6); identity is a judgement, not a creation. The counterweight is
  the fast lane: a creation that should not exist is deleted by an expert,
  logged, and reversible through git. Historical `ratifiedBy/At` (and
  `rejected`) fields survive on old records and in the site log's
  ratifications section as the record of who vouched while the mechanism
  existed; the validator keeps only their internal consistency.
- **Groups are acts, not hand edits**: `/api/group/create` (only games you
  already speak for) and `/api/group/edit`; every change logged. The edit's
  `add` refuses a game that already has a home (409); `move` is the explicit
  transfer, pulling each game out of whatever group held it. The group page
  offers "Move games into this group": a filterable checkbox multi-selector
  of every game not already in it (new games are made on the create page,
  not from group pages).
- **A game leaves through deletion** (public reason; its runs survive in the
  system's Uncategorized game). **A group is deleted outright the same way**
  (covering expert or editor, public reason): its games become ungrouped and
  the derived Uncategorized group gathers them; that group is computed from
  whatever no real group claims, so it can never be deleted. The old
  ask-then-decide removal-request flow is fully retired (2026-08-21):
  endpoints gone, UI gone, and nothing in the archive ever used it.
- **The game editor** (`/games/<key>/edit/`, linked from the game page's
  Expert menu, revealed only to covering experts, enforced server-side):
  identity (rename, thumbnail) and the **category manager**: one card per
  option with label, rule and metric definitions edited in place (public
  reason required), unused options deletable (a category with runs in it is 
  their home and cannot be deleted), new options simply added.
  Thumbnail uploads are guided toward 16:9 and prepared in
  the browser: contributors may apply a locked 16:9 crop, otherwise the
  client uploads the largest centered 16:9 crop. The archivist's existing
  image validation and size cap remain the backend guard; it does not
  letterbox or pillarbox uploads. Endpoints:
  `/api/category/add` (option_key field: 'key' is the auth field) and
  `/api/category/delete`; every act lands in edits.json. The editor carries
  no governance acts: deletion lives on the game page's Expert menu alone.
- Every game and group page ends with the **Expert menu** (§9) holding the
  governance acts for those entitled; content editing lives on the editor.

### Game properties

A game record carries, beyond title and system, four optional facts about
the game itself: **released** (first public release, at whatever precision
is known: `YYYY`, `YYYY-MM` or `YYYY-MM-DD`, shown as "1989", "March 1989"
or "3 March 1989"), **unofficial** (a ROM hack, mod, fangame or other
unofficial release; absent means official), **discord** (a permanent invite
to the game's community server; only `discord.gg/…` and
`discord.com/invite/…` are accepted) and **website** (the community hub,
wiki or leaderboard), **rta** (where the real-time records live, e.g.
speedrun.com) and **rules** (game-wide rules, markdown up to 2000
characters, composed above every category's own rule in View rules; #64). They are set at creation by whoever creates the game,
and afterwards by a covering expert or an editor through the game editor,
one logged edit per field; an empty value clears the field.

**Who speaks for a game shows closest scope first** (#65): the game page's
expert line names the game's own experts (marked with a muted `Game` pill badge),
then its group's (marked `Group`), then its system's (marked `System`), and rolls
site-wide experts into a quiet "+N site-wide" count whose tooltip carries the
names; nothing renders when nobody holds a game, group, or system scope. Group
and system pages likewise name their own experts (marked `Group` or `System`)
with a "+N site-wide" rollup; the permission data the action zones read keeps
the full covering union.

The game page
shows the release date and the unofficial mark as chips beside the system,
and the links as buttons in the header, opened apart and referrer-free.
Group and system pages sort their cards by stars, views, title or release
date (oldest first, undated last) and can hide unofficial games. The slug
stays derived from the title and is not customisable: it is the archive
path and every link into it.

### The game editor

`/games/<key>/edit/` (covering experts and editors) edits one local draft
of the whole record (title, thumbnail, properties, categories with their
rules, metrics, subcategories, and the order of both levels) and writes
nothing until the single **Save all changes** at the bottom. Save turns the
draft's differences into the archivist's logged edits in a safe order (new
categories, then new subcategories, renames and rules, metrics, deletions,
orders last), all under one public reason; the first failure stops the
sequence, what went through is the new baseline, the rest stays pending.
The page shows "N changes pending: …" as you go and guards the tab against
leaving with unsaved changes. Category cards span the page.

### Per-category metrics (shipped 2026-08-20)

A category defines what it ranks by.

- **Model**: a category option may carry `metrics: [{key, label, type:
  time|number, better: lower|higher, unit?}]` (schema-checked, at most 4).
  Array order IS the tie-break hierarchy; the first entry is the primary
  metric, shown wherever time shows classically (browse, thumbnails, home
  shelf, member lists, group records). The reserved key `time` (which a
  metric labeled Time slugifies to) is the run's main time: stated by the
  author like any other metric, stored as `duration`, importable from the
  movie file or the encode on demand; metrics and the movie are fully
  decoupled. **Absent `metrics`
  means the implicit classic metric** (real time, lower better): zero
  migration. Runs store stated values in `run.json` `metrics: {key: number}`
  (times as seconds); `0` means "not yet stated", renders as the dash and
  sorts LAST at its level regardless of direction, falling through to the
  next metric. The final tie-break is the submission date (imports: original
  publication), earlier wins, so ranks are always plain 1, 2, 3.
- **Verification attests the goal and the scoring**: metrics order the
  achievers, and editing a stated value afterwards voids the live
  verifications (the general voiding rule above); dishonest values that
  slip past are a moderation matter.
- **Derivation lives in the generator only**: `model.py` `run_metric_defs`
  / `metric_value` / `rank_key` (comparator), `render.py` `fmt_metric` /
  `primary_metric_html` / `primary_metric_text`. The archive stores facts;
  nothing ranking-shaped is written.
- **Submission**: the category's stated metrics are required fields; they
  appear on category pick in a **dashed-edge box** in the Scoring panel
  (segmented h/m/s/ms for time-types, number+unit otherwise; posted
  as `metric_<key>`, times as seconds). The stated-time input appears
  whenever `time` is among the category's metrics, whatever the movie holds;
  a time-less category stores no duration at all (schema + validator enforce).
- **Creation is everybody's; curation is the experts'**: any logged-in
  member creates a game (`/create-game/`: title, system, plus the first
  category) or a category (`/create-category/?game=<key>`, game locked).
  Both forms share the **metrics editor** (up to 4 rows: label, type,
  direction, unit, reorder; time is a row like any other, and a row labeled
  Time becomes the run's main time; keys derive from labels; `unclassified`
  refused; skipping metrics yields the classic category). Entry points: "Create a game" on `/games/`,
  "Create a category" on every game page, and a question + button BESIDE
  the submit form's selectors ("Game not there? Create it") opening in a
  new tab so a half-filled submission survives. The selectors themselves
  never create. Both creations notify Discord and log to edits.json;
  `/api/game/create` and `/api/category/add` take any member (placing a
  game into a group still needs scope over the group). Only experts edit
  what exists.
- **Editing values**: authors state metric values at submission and may
  correct them via the author edit path (`metric_<key>` fields); experts
  via `/api/expert/edit` field `metric:<key>` on a run, both logged.
  Experts manage a category's metric definitions in the game editor
  (field `metrics`, JSON): adding a metric writes an explicit `0` onto
  every run of that category in the same commit (nothing is unranked;
  experts fill values and the board re-sorts); removing one keeps stored
  run values, they just stop being read.
- **Leaderboards**: one column per metric of the category (classic shows
  the single Time column, unchanged); a "Ranked by:" line names the
  hierarchy; History deltas are direction-aware on the primary metric
  (frames against frames when time rules and both sides are movies).
  Browse coalesces Frames/Time into one untitled column showing each run's
  own primary metric; a flat cross-category list has no honest metric sort,
  so browse orders by date/stars/title only. The run page's fact box keeps
  everything: frames, derived time when it exists, every stated metric with
  its label ("not yet stated" for 0).

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
  credited to an author from ANY other TAS site is held for them. Discourse
  refuses a held name in the same words it uses for a name somebody already
  registered ("Not available. Try Nymx1?"), which turns away the one person
  it is held for, so `/api/name/status` says which of the two it is (free,
  taken, held, unknown; the forum unreachable is never read as free, and the
  reserved list is read from the forum's own setting through a ten-minute
  cache). The forum's signup form asks it as the name is typed and explains
  the held case, through the theme component in
  `infra/discourse-theme/held-name/`; the claim page carries the same
  sentence for anyone who arrives there first.
- **Claims**: `/api/claim/request` files one (one open claim per member);
  the claimant TYPES the name, and no page anywhere offers the held ones
  as a list: that list is the roll of people who have not come here, and
  handing it out is not ours to do. Every other registered thing is still
  picked rather than typed;
  the **Steering Committee alone** decides (`/committee/` panel), seeing a
  **masked** form of the requester's forum email (`jo***oe@e****.com`),
  computed live, shown only to those entitled, **never stored**: the
  validator refuses any `@` in `claims.json`. Approval renames the forum
  account to the claimed name, unlocks self-import, and PMs the person;
  denial requires a reason and PMs it. Committee members may also **attest**
  an identity directly, publicly naming how they verified it (the one place
  the archive accepts judgement instead of proof — token-based proof was
  abandoned because it depended on another site's permissions, §4.8). A ban
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
sat waiting, the whole payout topping out at 2,000 · later reproductions 25 ·
hard-to-reproduce systems (flagged in systems.json) +50 on any reproduction ·
first verification 20 + 1/day waiting, topping out at 1,000; later
verifications 20 · console verification a fixed 1,000 (real hardware, a
capture setup, a recording). Both contribute worklists sort by bounty.
One plain **Contributor** badge, earned by the first act; the milestone
tiers (1k/5k/10k/25k) are retired, since the medals carry the honors. The
badge shows beside a name everywhere except on the contributor board, where
the points column already says it (#59). There, and on the member page, **medals**
take its place: little gold/silver/bronze discs, one letter each, the
achievement in the tooltip. All recomputed from the recorded acts at build
time, nothing stored: top contributor of the last 7 / 30 days (W, M; ties
share), ten / a hundred / five hundred reproductions (R) or verifications (V),
twenty-five / a hundred firsts (1), one / ten console verifications (H);
one medal per family, the highest earned. **No currency
buys anything** (anti-farming: a currency without privileges is not worth
gaming). Imports award nothing.

The **Contribute board** is the public worklist: needs-verification and
needs-reproduction tables with rising bounties, recent contributions in the
side rail (only work that actually scored), open cases, the contributor
leaderboard. A run page carries the board's ask ("This run needs help")
only while the run is still waiting for the verification that would rank
it; an Unclassified run ranks by likes, so it never asks. **No claiming,
no assignment**: anyone may do anything anytime;
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
- **Run visit counters are operational state, not archive facts**: the run
  page's script POSTs `/api/visit` (no auth; a visit is anonymous, and
  counting from the script keeps plain crawlers out), the archivist tallies
  into `visits.json` **beside** the checkout (`VISITS_FILE`; never inside
  it, where refresh's `git clean` would erase it), and the page shows the
  number live in a small grey eye badge in the header chips. Best-effort by
  design: no dedup, lost on VPS loss, never committed.
- **The generator** builds the whole site from the archive checkout
  (`generator/build.py <archive> <out>`; `ARCHIVE_REF` names the branch in
  links). Frontend is real files, shipped verbatim (except the
  `ENCODE_HOSTS`/`ENCODE_NAMES` provider-name substitution, applied to
  whichever script actually names them): `assets/app.js` is the **shared,
  page-agnostic** ES module every page loads (nav, account menu, view-as,
  the type-to-find picker, the busy/note/mark helpers, file rows, the
  metrics editor) — every page-specific behavior it used to carry has
  moved out into a real per-page module (`assets/page-home.js`,
  `page-library.js`, `page-run.js`, `page-game-edit.js`, `page-submit.js`,
  `page-create.js`, `page-panels.js`, `page-member.js`, `page-import.js`),
  each declared through the page renderer's `scripts=` and each importing
  the shared bindings it needs with an explicit `import … from './app.js'`
  (all in `assets/`, so the import resolves the same regardless of how
  deep the HTML page itself sits). Pages embed JSON blobs the client reads
  (always `.replace('<', '\\u003c')`-armoured). Run arrival dates come from
  git history (`fetch-depth: 0` in CI), falling back to
  `importedAt`/`submitted`.
- **The pipeline**: the archivist is the publisher. The moment its push
  lands, `archivist/sitebuild.py` rebuilds the whole site from the local
  checkout (~1 s), refuses any incomplete build (the same guard CI applies:
  every run has a page, core assets exist), and swaps an atomic `current`
  symlink that host nginx serves. **Act-to-published is about a second**;
  the read lock holds the tree still during the build, a burst of commits
  coalesces into one rebuild, and a failed build keeps the previous site
  serving. Content arriving from elsewhere is caught by the refresh loop
  (HEAD moved → rebuild). **GitHub Pages remains the hot standby**: the
  same push still fires the website's `deploy.yml` dispatch
  (`WEBSITE_DISPATCH_TOKEN`; `reason=archive-content` skips the code-test
  gate), the archive repo's `rebuild-site` job and a six-hourly schedule
  back that up, and Pages keeps a complete, current copy of the site that
  one DNS change puts back in front. Website pushes run the full suite
  before touching either origin; a red suite keeps the last good build
  everywhere.
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
  home, notifications. Every run gets a topic in the **Movies** category
  (id 13, `MOVIES_CATEGORY_ID`; created in the same commit as the archival,
  so the pointer is never lost); every game gets an **anchor topic** in
  **Games** (id 12) under its tag, and the tag page across categories
  (`/tag/<system>-<slug>`) IS the game's forum home, run topics included
  (`max_tag_length` raised to 60). Run pages proxy their thread through the
  archivist (`/api/discussion`, 60 s cache) and accept replies session-only,
  posting under the member's own Discourse name. Theme components on the
  default theme: our own "toolAssisted tweaks" and the official **DiscoTOC**
  (#70: automatic table of contents; auto-applied in General and Emulators
  for posts with 3+ headings, insertable by any author from the composer
  gear elsewhere). Role groups on the forum
  are **printed projections** of `roles.json`, one-way, reconciled
  periodically; joining a forum group grants nothing. Private messages are
  never relayed anywhere.
- **The site answers on the apex and on www**, and the archivist treats both
  as its own origin (`SITE_ORIGINS`, the apex plus its www form, plus
  anything in `SITE_ORIGINS_EXTRA`): a credentialed response may name exactly
  one origin, so the caller's is echoed when it is ours and the apex is named
  otherwise. The CSRF guard on cookie writes accepts the same set. The apex
  stays canonical (pages link to it, and a redirect in front of the service
  is the tidier fix); this only stops a reader who typed www from being told
  the archivist is unreachable.
- **Discord notifications** (`DISCORD_WEBHOOK_URL`): one line per event,
  links inside representative words (`<>` suppresses the preview); a movie
  is named the way people say it, the name carrying the link:
  `[\[SNES\] Prince of Persia](<url>) by eien86, Challenger`. Mentions
  disabled, sent only after the archive write landed, and
  **held until the page they link answers 200** (up to
  `NOTIFY_LINK_WAIT_SECONDS`). New-movie lines carry the run thumbnail as an
  embed. Imports notify once per batch. **Every act names the category it
  was about** (verification, reproduction, hardware playback), since that is
  what was judged or synced. **Every edit is announced too**: a run revision,
  an expert edit of a run, game, category or group, a cross-game move, and a
  system correction, each naming the fields that changed, what the change
  invalidated, and the public reason where one was owed (an author revising
  their own work owes none). Forum posts relay through a
  HMAC-verified Discourse webhook, skipping PMs and the bot's own posts.
- **Static-first is a commitment**: no server search endpoints, no
  server-rendered pages. Pages carry small indexes; per-game payloads are
  fetched from the archive's raw URL on demand.

---

## 9. UI rules (apply to every future surface)

- **The work is a "run", never a "movie"**, in every piece of copy: page
  titles, nav ("Runs"), buttons, Discord lines, log headings ("Run
  reports"), import flow ("Import my runs"). "Movie file" survives only as
  the name of the input artifact (the recorded inputs a video-only run
  lacks): "movie file", "movie format", "input movie". Member content and
  stored field names (`movie` in run.json) are untouched, as always.
- **Pick, never type**: anything registered (game, member, group) is chosen
  through a type-to-find selector, never a bare text box or a static
  `<select>` of everything. On the panels (expert, Committee, Founder) the
  member and game pickers are one shared widget (`armPicker` in the shared
  client runtime): it
  asks the archivist as you type, debounced (`GET /api/search?kind=members|
  games&q=`, a page of matches from a 20 s in-memory index), so no page
  carries the whole member or game list (#56); the group chips pickers fill
  their list the same way. Small registered sets (groups, systems, the
  roster) stay embedded. **A picker never offers what would be refused**:
  the page keeps the eligibility rule (who already speaks for the scope, who
  is seated, which games are ungrouped) and filters the matches by it; grant
  lists offer who lacks, remove lists who holds. Free text only for things
  that do not exist yet, and author lists.
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
- **View as** (Steering Committee only): the account menu offers Yourself /
  Site-wide expert / Editor / Plain member / Signed out. Presentation only
  and per tab (sessionStorage, an amber fixed pill while active, one click
  back): the page reveals itself as the chosen role by filtering the baked
  role lists and page blobs client-side, while the archivist keeps treating
  every request as the real you — a pretend role must never reach the write
  path. A view-as key on a non-Committee account is ignored and cleared.
- **Honest failure**: an unreachable archivist shows an amber "archivist
  unreachable" marker with a retry; reading never needs the archivist.
  **Editing a run is the submit form in edit mode** (`/submit/?edit=M1234`,
  reached from the run page's "Edit run ↗"): one form, one set of rules, no
  second copy to drift. The record comes from the archivist's checkout
  (`/api/run/record`, with who may do what); every panel is open and
  prefilled; the game is fixed; category and subcategory are changeable by
  a covering expert or an editor only (their logged move edit); authors
  alone change the author list and add supplementary files; the movie file
  stays, a covering expert may replace it; experts state their public
  reason; an editor touches nothing but category and subcategory. "Save
  changes" asks the archivist first (dry run) and warns before an edit that
  voids the run's acts. No draft is kept in edit mode; "Discard changes"
  reloads.
  **Moving a run reuses the same submit shell** at
  `/submit/?move=M1234`: its subtitle identifies the run, game, authors and
  expert mode; the submission-policy prompt is hidden; Back to the run remains;
  and only the destination and agreement panels remain.
  The submit form is one form in six panels that unfold in sequence as
  the previous one is complete: 1 game, category, subcategory; 2 the run:
  encode (checked live), authors, completion date; 3 reproduction
  information: the optional movie file, read at once by
  `/api/movie/inspect` with a status mark beside the picker: a spinner
  while it reads, a green check when it parsed, an outlined blue "!" when
  the format is unknown or unreadable, with a note saying the submission
  or edit continues but nothing is importable from the movie (the file is
  archived exactly as it is, `frames` 0), the tool and its version
  (free text), the files
  the movie was made against, supplementary uploads; 4 scoring: the
  category's metric values, and the time, stated by the author, never
  filled in for them (Import from movie fills it on demand when the movie
  parsed); 5 submission notes: disclosures, notes, Preview;
  6 agreement and Submit, which appears once Preview was pressed. An
  unfolded panel stays open, a folded one says what it waits for.
  The submit form arms the standard leave-page dialog once anything changes,
  and keeps a **draft** in the browser's localStorage (every field but the
  movie, which browsers refuse to restore; saved 300 ms after each change,
  "Draft saved <time>" shown; restored on the next visit, the URL's game
  winning over the draft's; dropped on a successful submit or by "Clear the
  form", which asks first). Drafts older than 30 days are ignored.
- Every page ends in the shared footer (constitution links, site log, social
  icons as CSS masks).
- **Freshness**: the live origin serves every response `Cache-Control:
  no-cache` with an ETag, so each load revalidates (a 304 nearly always)
  and a build swap is visible on the very next request. For a page left
  open across a change, every build still ships `/assets/buildstamp.json`;
  each page knows its own build (`window.TAR.v`), the client compares the
  two through an uncached fetch, and a green fixed pill ("This page has
  been updated · Refresh") offers the reload. Never automatic: the reload
  is the reader's. (The pill earns its keep again whenever the Pages
  standby, with its `max-age=600`, is in front.)
- **Search engines**: every public page carries a canonical URL, a meta
  description, Open Graph and Twitter cards (the run's thumbnail as image),
  and JSON-LD: BreadcrumbList on content pages, VideoObject on run pages
  (encode embed, thumbnail, duration), Person on profiles, WebSite with a
  SearchAction plus Organization on the home page. Titles name the search
  vocabulary: "Prince of Persia (DOS) TAS in 18:50 by GMP · Any%"; games,
  groups, systems and authors follow the same pattern. Thumbnails carry
  "<game> (<SYS>) TAS by <authors>" alt text. The build writes `sitemap.xml`
  (every indexable page) and `robots.txt`; tooling pages (submit, claim,
  import, panels, create pages, game editors, mocks) are noindexed and
  fenced. Search Console and Bing Webmaster verification are operator steps.
- **Confirmations mean "and you can see it"**: every successful write's
  response carries the archive revision it produced (`serial`, injected by
  the archivist for every non-dry POST), and the buildstamp carries the
  revision the served site was built from. The client holds the green
  message at "Publishing to the site…" until the served stamp reaches the
  response's serial, then says the change is live; follow-on links (submit
  a run to a just-created game or category) only appear at that moment, so
  they always land on pages that know about the change. If the stamp never
  catches up in 30 s (the Pages standby serving), the message degrades to
  "shortly" instead of lying.

---

## 10. Licensing, legal, privacy

- **The licensing chain**: native submissions are granted **CC BY 4.0** by an
  explicit consent checkbox (§4.2); works imported from TASVideos remain
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
  responsibility that co-authors agree (§4.7, spelled out by the
  constitution's §3.2 multiple author submission policy, which the submit
  form and the import page link to; the site carries no policy page of its
  own); any author may withdraw it, all together may erase it. The importer never crawls the source site: it
  reads a local backup corpus, refreshed by a daily cron.
- **Hardware verification exists only where it can happen** (issue #53):
  `systems.json` marks the systems a movie is played back on real hardware
  (`hardwareVerifiable`: a2600, nes, snes, genesis, gb, gbc, gba, n64). On
  every other system the console signal is absent rather than "none": no
  Console column on that game's boards (mixed tables show the column only
  when some row can carry it, a dot otherwise), no roster, act form or
  status line on the run page, no row on the hardware worklist, and the
  archivist refuses the act. An import's source verification still shows.
- **ROMs never touch the site.** Hashes and names are facts.
- **Privacy commitments** (§6, and they bind the implementation): no
  analytics, no tracker, one session cookie after login; the archive holds
  no personal data beyond usernames; emails never shown (masked, transient,
  Committee-only during claims — §6.4.1) and never stored in the archive;
  account deletion removes account, email and personal data while public
  contributions persist like git history (§6.8); third parties a page talks
  to are listed honestly (§6.9 — the reason Gravatar was switched off).
- **The public legal contact is contact@toolassisted.run** (§6.11: operator
  identity; §4.10: infringement claims from anyone, member or not; §6.12:
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
- **Hosting**: everything on the Infomaniak VPS (Ubuntu LTS). Host nginx
  terminates TLS (certbot) and serves three things: `toolassisted.run`
  statically from `/opt/archivist/site/current` (the archivist's freshest
  build; configs in `infra/nginx/`), `forum.toolassisted.run` proxied to the
  Docker Discourse in socketed mode (`web.socketed.template.yml`, unix
  socket, no ports), and `/archivist/` on both hosts proxied to the
  archivist on 8100. **GitHub Pages is the hot standby**: every build still
  deploys there (`CNAME` shipped, TLS by GitHub), and repointing the apex A
  records back at GitHub restores service if the VPS dies. Mail through
  Infomaniak (`mta-gw.infomaniak.ch`).
- **Cloudflare in front (Free plan: unmetered DDoS protection, any site)**.
  The zone is proxied (orange cloud) for the apex, `www` and `forum`; SSL
  mode *Full (strict)* against the origin's Let's Encrypt certificates;
  mail records stay DNS-only. The origin trusts Cloudflare's published
  ranges for the real visitor address (`/etc/nginx/conf.d/cloudflare-realip.conf`,
  regenerated monthly by `infra/vps/cloudflare-realip` from cron) so
  Discourse's rate limits and IP logs still see people, not the edge. No
  cache rules are needed: pages answer `Cache-Control: no-cache` (never
  cached at the edge), versioned assets carry `?v=`, and JSON is not in the
  edge's default cacheable set, so `buildstamp.json` stays live. certbot
  renews through the proxy (HTTP-01 passes). The Pages standby is
  unaffected: swapping the proxied A records to GitHub's still works.
- **Hardening headers** on both surfaces: nginx serves the site with
  nosniff, `X-Frame-Options: DENY`, a referrer policy, a frame-ancestors /
  object-src / base-uri CSP (a full CSP is impractical with inline scripts
  and third-party embeds) and HSTS (`infra/nginx/hardening.conf`, a snippet
  both server blocks include); the archivist adds the same on every answer
  (`default-src 'none'` on the JSON API). Found and fixed by an OWASP ZAP
  pass against a throwaway archivist (`tools/zap/`); never scan the live
  service, an active scan submits junk for good.
- **Everything works on the archive's `main`**. The old `staging` branch
  was merged in (a two-parent commit whose tree is staging's; nothing left
  behind) and stays **frozen** so old forum links into it keep resolving.
- **Deploying anything** = push to main. The `sync-vps` job in `deploy.yml`
  runs after the full suite passes and reaches the VPS through a
  forced-command SSH key (`VPS_SYNC_KEY` secret) that can only ever run
  `/usr/local/bin/tar-site-sync`: pull the website checkout at
  `/opt/archivist/website` (over **SSH with a read-only deploy key**,
  `/opt/archivist/website_deploy_key`, set as that checkout's own
  `core.sshCommand`: GitHub began answering the host's anonymous HTTPS
  git-upload-pack with 401, so every deploy failed at the pull while the
  same URL still advertised its refs), copy **all** of `archivist/*.py` to
  `/opt/archivist/`, restart the archivist (whose startup build republishes
  the site). **A second, independent door**: GitHub's own webhook reaches
  `POST /api/hooks/github` on the archivist, HMAC-verified
  (`X-Hub-Signature-256`, `GITHUB_HOOK_SECRET`). It opens on exactly the
  condition CI's own job does — a **successful `Build and deploy` run,
  triggered by a push to main** (`workflow_run` completed) — and it
  deploys **that run's commit**, passed to the script as its argument, so
  main racing ahead to a red commit cannot ride along. A bare push only
  logs that work is coming; red runs, other branches, and the schedule and
  archive-content runs (both skip the suite) deploy nothing. The script
  runs in a transient systemd unit, since it ends by restarting the
  archivist and would otherwise kill its own parent; repeat calls inside
  20 s fold together. It exists because Actions is not always there: a
  backed-up queue, or a push that produces no run at all, used to leave the
  VPS serving old code with nothing to notice it. When Actions cannot
  report at all, the override is an operator's own signed call to the same
  endpoint (a `repository_dispatch`-shaped body, action `deploy-now`,
  optional `client_payload.sha`; GitHub itself never delivers that event to
  a webhook), or plainly `ssh ubuntu@… sync`; the manual path (scp the same
  files, restart) remains the last fallback.
- **Secrets** (never committed): `/etc/archivist.env` on the VPS
  (`SUBMIT_KEY`, `DISCOURSE_*`, `SESSION_SECRET`, `SSO`, `DISCORD_WEBHOOK_URL`,
  `GITHUB_HOOK_SECRET`, `GIT_SSH_COMMAND`, `ARCHIVIST_BRANCH=main`); deploy keys under
  `/opt/archivist/`; `ARCHIVE_ACTION_WRITE_SECRET` on the archive repo — a
  non-expiring fine-grained token (resource owner ToolAssisted-run, website
  repo only, Actions read-write; the org's 366-day maximum-lifetime policy
  was lifted for this). GitHub still auto-revokes any token unused for a
  full year; ours fires on every act, so that only matters if the site goes
  dormant. If dispatches ever stop, deploys degrade silently to the
  six-hourly rebuild: check the token first. The retired FTP_* secrets are
  deleted from both repos (values survive in the operator's local netrc).
- **Backups: one snapshot restores 100% of the site** (scripts and
  `RESTORE.md` in `infra/vps/`). Two crons ship to Infomaniak Swiss Backup
  over rclone/Swift (config in root's rclone.conf on the VPS): Discourse
  dumps daily (04:30, `ship-discourse-backups` →
  `swissbackup:discourse-backups`, 30-day retention; 5 kept locally), and
  the whole site's state daily (04:45, `ship-site-backups` →
  `swissbackup:site-backups`, 30-day retention): a full-history git bundle
  of the archive, one of the website repo, and a state tarball carrying the
  operational files (visits, spool, claims), the archivist env and keys,
  nginx vhosts, certificates, cron files and the Discourse `app.yml` — it
  contains secrets, which is the point of a restorable snapshot on private
  storage. The TASVideos corpus (2.3 GB; also mirrored to a private GitHub
  repo, refreshed daily at 05:17 by the only cron that ever touches
  tasvideos.org) ships as a weekly bundle (Mondays →
  `swissbackup:corpus-backups`, 21-day retention). Ship logs are silent on
  success; verify with `rclone ls`, not the log.
- **History is rewritten only deliberately, for records about people**, and
  a force push is always a human hand, never tooling (the auto-mode
  classifier blocks it by design). After any rewrite: GitHub keeps old
  commits fetchable by SHA until Support gc's the repo, and clones elsewhere
  persist — §6.8 says so rather than promising what git cannot deliver.

**The site log page is a window, the archive is the log**: every section of
`/policy/site-log/` renders the last 7 days (always at least the latest 25
entries so a quiet section still reads, at most 50, and anything still open
whatever its age); the headings carry the full-history totals, and the page
says where the complete record lives (the archive repository and its git
log), so the page never grows with the archive.

**Write pacing (log-flooding defence)**: every member-triggered write is a
commit, a rebuild and often a log entry, so the archivist paces writes per
member in memory (`pace_gate`: likes 12/10min, edits 40/h counting dry
runs, acts 30/h, submissions 12/h, reports 6/h, creations 20/h; 429 with a
plain sentence). The operator key is never paced (imports, tests). nginx
holds a per-IP backstop in front (`limit_req` 5 r/s burst 20 on
`/archivist/`; 30 r/s burst 80 on the whole site), keyed on the Cloudflare
real IP; Cloudflare's own DDoS layer sits before all of it. The submit
helpers (`/api/movie/inspect`, `/api/preview`, `/api/encode/check`) do real
work (parsing uploads, fetching third-party pages), so they require a
session: the form that uses them only shows to a logged-in member. The
anonymous `/api/visit` counter counts each address once per run per hour,
in memory only (no address is stored), so reload loops cannot inflate view
counts. Account registration is the forum's: Discourse defaults apply
(email verification, at most 3 accounts per IP per day, its own signup
rate limits); registration stays open by principle (§1.5).
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
  builds synthetic archives for exact-value assertions. Frontend syntax and
  runtime checks cover the shared entrypoint and every page module emitted by
  the generator.
  `mkarchive.prune_superseded` brings live-archive copies to the state the
  claim flow enforces. `TESTPLAN.md` is the map.
- **Lessons encoded as practice**: a feature is not shipped until the
  generator has built an archive containing its output · CI-watching is not
  optional · byte-golden diffs make refactors provable · fixtures must carry
  every shape that ever crashed a build (empty games, decided claims).

---

## 13. Open items

- Contributor point weights are provisional; the community settles them.
- Full-launch checklist: re-review policy drafts and point weights.
- **v2, deferred by explicit decision**: automated client-side reproduction
  (browser/desktop Chimera replays locally, ROMs never leave the machine,
  signed receipts with state-hash samples corroborate independently). Revive
  when Chimera's WASM determinism is proven.

## 14. Retired, so nobody trips on it

The hand-written landing page, `/stage/` indirection, the 21-view design
mock, the `/experts/` page (roles live on member pages), the `experts.json`
snapshot (now the `roles.json` log), the TASVideos-token claim flow (now
Committee judgement), the two-verification "full" tier (now the expert
stamp), the reproduction ranking gate (now verification), the "tools used"
field (notes carry tooling), the per-page giant game `<select>` (now the
combobox), FTP hosting (now Pages), the `SITE_BETA` beta bar (removed
2026-08-23, the site is out of beta), and the `staging` branch (merged,
frozen). Git history holds them all.
