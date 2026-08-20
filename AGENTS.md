# Agent instructions — toolAssisted.run website

**Read `DESIGN.md` first.** It is the canonical, always-current record of the site's
purpose, design decisions, and implementation plan. Do not re-litigate settled decisions
with the user unless they reopen them; do not contradict it in mocks or copy.

**Keep `DESIGN.md` updated, snapshot-style**: it is a continuously maintained
snapshot of the site's entire rationale, not a decision log. When a decision is
made or changed, rewrite the section it belongs to so the document always
describes the present; never append dated entries, and never attribute
decisions to people in it (git history and the site's public logs carry
attribution).

Working rules:
- **The implementation always abides by the constitution.** The community's
  constitutional document (Principles, Governance, Terms, Privacy) lives at
  https://github.com/ToolAssisted-run/.github/blob/main/profile/README.md and
  outranks every implementation choice: when code and constitution disagree,
  the code is wrong. Changing the constitution has its own amendment process
  (hard majority, 14-day comment period); never "fix" it from here to match
  what was built.
- **UNBREAKABLE, amended 2026-08-19 by Sergio: member content is edited only
  by a responsible person, never by us.** An **expert acting inside their
  jurisdiction** may modify member content (movie files, notes, goal
  descriptions) one item at a time through `/api/expert/edit`: every edit is
  logged in `edits.json` with who/from/to/why and is reversible through git.
  What remains forbidden, absolutely: edits by us or our tooling on our own
  initiative, bulk sweeps over member content (style passes, renames), any
  unlogged alteration, and any change to an author list (honest attribution is
  Terms 3.3; who made a thing is moderation's question, never an edit). Forum
  posts are never touched by anybody through us.
- The generated site IS toolassisted.run (beta live at the root since 2026-08-15;
  the old hand-written landing page and /stage indirection are retired to
  git history). `generator/build.py <archive> <outdir>` builds it from a
  checkout of `ToolAssisted-run/archive` (default `~/ToolAssisted-archive`);
  `stage-build/`/`site-build/` are gitignored output.
- Deploy: push to main → GitHub workflow (`deploy.yml`) builds and publishes
  to **GitHub Pages** (custom domain `toolassisted.run`, TLS from GitHub).
  Never deploy by hand unless CI is broken. Since 2026-08-20 everything works
  on the archive's `main` branch (staging merged in and frozen; its old links
  still resolve).
- Brand: follow `DESIGN.md §2` and the branding kit in the `ToolAssisted-run/.github`
  repo. Signal Green #22C55E accent; JetBrains Mono + Inter; frame-quantized motion
  (CSS steps(), no easing); components as surfaces on a soft ground, bold lines.
- Terminology matters: "verification" (goal met, judged from the encode) is THE
  ranking gate since 2026-08-19: community verification ranks a run and is
  SHOWN as "verified"; a covering expert's is permanent and SHOWN as
  "verified (expert)" (2026-08-20 wording; the archive's stored enum names
  remain provisional/confirmed and never change). "reproduction" (movie file syncs) is a
  recorded, paid assurance and gates nothing. Video-only runs (no input
  movie; encode is the run; stated duration) mark both reproduction and
  console "not-applicable". "pending" (missing a
  gate), "Imported" (seeded TASVideos import; formerly "Legacy"), "experts" (scoped moderators), "archivist"
  (the intake bot). Never describe the site as running emulation server-side.
