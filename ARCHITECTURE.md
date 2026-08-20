# Architecture

How this repository is put together, for a developer arriving cold.
`DESIGN.md` is the canonical record of *decisions*; this file is the map of
the *code*. The system is two programs sharing one truth:

```
ToolAssisted-run/archive  (a plain git repo of facts: runs, games, roles…)
        │                        ▲
        │ reads                  │ writes (logged, validated commits)
        ▼                        │
  the generator            the archivist
  (static site,            (Flask service on the VPS:
   GitHub Pages)            intake, acts, moderation)
        ▲                        ▲
        │ pages + JSON blobs     │ JSON API (session or key)
        └──────── the browser ───┘
              assets/app.js
```

The archive repository is the single source of truth. It stores **facts,
never derived state** (who verified what, when; never "this run is ranked").
`validate.py` in that repo is CI's guard: every commit must validate.

## MVC, concretely

**Model** — what is true.
- `generator/model.py`: the archive loaded, plus every derivation
  (effective verification state, rankings, contributor points, author
  stats/news, rename resolution). Writes nothing, builds no HTML.
- `archivist/records.py` + `archivist/gitstore.py`: the same facts on the
  write side — records (members, roles, groups, claims, logs) and the git
  checkout that persists them.

**View** — how it looks.
- `assets/app.js`, `assets/style.css`: the frontend, real files, shipped
  verbatim. The script reads embedded `application/json` blobs and talks to
  the archivist's JSON API; it never scrapes pages.
- `generator/render.py`: HTML helpers (escaping, chips, wiki rendering,
  page chrome, thumbnails).
- `generator/views/*.py`: one page family per module. A view renders its
  pages **on import** (the template strings must keep exact indentation, so
  the code stays top-level); `build.py` imports them in build order.

**Controller** — what happens when.
- `generator/build.py`: scaffolding, build order, shared assets.
- `archivist/archivist.py`: the Flask app and every route. Routes validate
  the request, drive the layers, answer JSON. The layers import only upward
  (settings → webutil → identity → gitstore → notify → records → forumapi),
  never the controller.

## Frontend/backend decoupling

The site is static; the browser is the only place frontend and backend
meet, and they meet **only** through:
1. the archivist's JSON API (`/archivist/api/...`, session-cookie or
   submitter-key authenticated, CORS-pinned to the site origin), and
2. the JSON blobs the generator embeds in pages (`<script
   type="application/json">`), which app.js reads.

Neither Python program emits JavaScript; neither JavaScript file contains
markup the generator depends on.

## The three derivations that must agree

Effective run status is derived in three places that must change together
(a standing lesson): `archivist/records.py::sync_status`, the archive's
`validate.py`, and `generator/model.py::eff_state`. The same applies to
rename resolution (`identity.py::current_name`, `validate.py::canon`,
`model.py::canon`).

## Tests

`tests/` holds the hermetic suites (fixtures never copy live governance
records; nothing pushes to GitHub; external services are faked). CI runs
them on every push; a deploy only happens when they pass. `mkarchive.py`
builds fully synthetic archives for exact-value assertions.

## Code quality scanning

`tools/sonar.sh` runs a local SonarQube (community edition, docker) over
the repo; `sonar-project.properties` defines sources (generator, archivist,
assets) and tests. Run it before larger changes land.
