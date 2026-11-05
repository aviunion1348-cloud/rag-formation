# Experience validation (Stage H) — every renderer path, no physical GPU

The experience page can draw its scene four different ways depending on what the
browser gives it. All four were driven against the **real modules** in jsdom by
`scripts/experience_matrix.mjs` (run via `bash scripts/run_experience_matrix.sh`),
and the accessibility contract was checked alongside.

```
experience matrix: 33/33 checks passed
physical GPU: NOT verified in this sandbox (no /dev/dri, no nvidia-smi) — blocked, not claimed
```

## 1. Renderer matrix

Each row boots `createRenderer()` from `ragformation/web/experience/js/renderer.js`
against a canvas whose `getContext()` returns exactly what the row describes, then
walks all sixteen scenes calling `setScene(scene, {instant: true})` and `render()`.

| Browser situation | Reported mode | Scenes rendered | Draw activity | Errors |
| --- | --- | --- | --- | --- |
| WebGL2 available | `webgl` | 16/16 | 16 × `drawArrays` | none |
| WebGL2 missing, WebGL1 available | `webgl` | 16/16 | 16 × `drawArrays` | none |
| No WebGL, canvas 2D available | `canvas2d` | 16/16 | 13 871 canvas operations | none |
| No drawing context at all | `none` | 16/16 | 0 by design — the DOM transcript carries the story | none |

The fallback is not decorative: the same sixteen scenes render in every mode, and
the page boots rather than blanking when a context is denied.

## 2. Scene and accessibility checks

| Check | Result |
| --- | --- |
| sixteen scenes defined | 16 |
| every scene has caption **and** detail text (the transcript's content) | pass |
| every scene has a title | pass |
| transcript lists every scene | 16 |
| transcript lines are text, not markup | pass |
| canvas is `aria-hidden="true"` (decorative; the transcript is the accessible equivalent) | pass |
| `aria-live="polite"` region for changing content | pass |
| transcript is an `<ol>` inside a section labelled by its heading | pass |
| `prefers-reduced-motion` honoured | pass |
| forced-colours mode honoured | pass — **added in this stage** (see §3) |
| visible `:focus-visible` ring | pass |
| no third-party script or style loaded | pass |
| page documents its own fallback | pass |

The scene vocabulary is also tied to real engine stages (`SCENES[].stages` must be
event-stage names that the server actually emits); that contract is enforced by
`tests/test_experience.py::test_scenes_use_real_event_stage_names` and by the Node
contract test `tests/experience_contract.mjs` (all checks passing).

## 3. The one change this stage made

The stylesheet had no `@media (forced-colors: active)` block, so a Windows
high-contrast user would have kept a decorative canvas and low-contrast borders.
A short block now hides the canvas in forced-colours mode and paints containers
with system colours. This is a rendering-mode statement, not a new feature: the
same content was already in the DOM, and the transcript was already the accessible
path.

## 4. What is **blocked**, not claimed

**Physical-GPU validation is blocked in this environment.** There is no GPU
(`nvidia-smi` absent, no `/dev/dri`), no WebGL implementation, and jsdom does not
rasterise. So:

* shader compilation, real link status, texture limits and driver-specific
  behaviour are unverified;
* frame rate, thermal behaviour and battery impact are unverified;
* the `webgl` path is verified *structurally* (every call the renderer makes is
  answered, every scene issues draw calls, nothing throws), not visually.

Any statement of the form "this renders correctly on a GPU" is out of scope for
this release.
