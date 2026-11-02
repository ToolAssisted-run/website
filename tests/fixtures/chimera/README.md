# Real Chimera projects, from Chimera's own synthetic machine

Two files written by Chimera itself and replayed through its headless runner
before they were handed over. Nothing in either is anybody's property: the
"rom" is a test rom that repository generates.

They pin the frame count from both sides:

- `last-input-early.chimeraProject` — 70 frames logged, the last press on
  frame 24, so the run is **25** frames and the idle tail is warned about.
- `input-to-the-end.chimeraProject` — 70 frames, every one carrying input,
  so the run is **70** frames and there is nothing to warn about.

Both were saved before Chimera wrote a project's input as `inputs`
(columns, and one entry a frame), so they hold the older `input` text block,
which archived runs carry and the parser still reads. The two
`*.inputs.chimeraProject` files are the same projects in the JSON form
Chimera wrote from 2026-10-28, converted the way its own writer split the log
key (`inputsOf` in `source/engine/source/project.cpp`) rather than saved by
Chimera.

Since 2026-11-02 Chimera saves a project as a zip: project.json, and the
frames, one line each, in input.input. The two `*.zip.chimeraProject` files
are the same projects in that form, saved by Chimera's own writer
(`ce_project_save`): each `.inputs` file split into those two parts, opened
by Chimera and saved again.

A synthetic fixture cannot catch a change in what Chimera actually writes;
these can.
