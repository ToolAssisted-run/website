# Real Chimera projects, from Chimera's own synthetic machine

Two files written by Chimera itself and replayed through its headless runner
before they were handed over. Nothing in either is anybody's property: the
"rom" is a test rom that repository generates.

They pin the frame count from both sides:

- `last-input-early.chimeraProject` — 70 frames logged, the last press on
  frame 24, so the run is **25** frames and the idle tail is warned about.
- `input-to-the-end.chimeraProject` — 70 frames, every one carrying input,
  so the run is **70** frames and there is nothing to warn about.

A synthetic fixture cannot catch a change in what Chimera actually writes;
these can.
