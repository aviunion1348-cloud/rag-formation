# The S3 Experience

`GET /experience` (also served from an installed wheel) is a 16-scene walkthrough
of what this engine actually does. It is a film about the pipeline, not a
marketing page: every number on screen is either from a recorded run of this
repository or from the server you are currently talking to, and the badge in the
top-right always says which.

## Modes

| Mode | Data source | What it is for |
| --- | --- | --- |
| **Director** | `demo_trace.json`, recorded from a real ingest → query → delete → rebuild | a deterministic walkthrough; works with no server, offline |
| **Live** | the running server's event stream (`/api/v1/events`) | watch a real query move through the pipeline |
| **Explorer** | the running server's index | read sources, chunks and traces, ask your own question |

Keyboard: `space` play/pause, `←/→` previous/next scene, `1/2/3` modes, `r`
replay, `m` reduce motion, `f` fullscreen. The transcript below the stage contains
the full narration as text, generated from the same scene script the animation
uses, so a screen reader or a slow connection loses nothing.

## Honesty rules the page enforces

- The mode badge is not decorative: `demo · recorded run` means the numbers came
  from a fixture, `live` means they came from the stream.
- The embedding badge shows the provider **and** whether it is semantic. Hashing
  is labelled lexical; it is never described as semantic.
- The generation badge shows the provider, and extractive mode is named as such.
  No language model ran in the recording, and the page says so in the fixture
  note.
- When the server cannot be reached, the badges say "server unreachable" rather
  than displaying a cached or invented value.
- When the server requires authorisation the Explorer stops and explains that the
  page deliberately does not read or store API keys. It does not guess one.
- Scenes that have no recorded events (only possible if the fixture is trimmed)
  would show "not in this recording"; the shipped recording covers all 16.

## Scene list

| # | Scene | What it shows |
| --- | --- | --- |
| 00 | A model without your documents | the problem: nothing to ground an answer |
| 01 | The source universe | documents enter as data, with provenance |
| 02 | Extraction and normalisation | structure kept, formatting noise removed |
| 03 | Chunking | heading-aware chunks with overlap and stable ids |
| 04 | Embeddings | one vector per chunk, provider and fingerprint matter |
| 05 | The persistent index | vectors + lexical + provenance in one transaction |
| 06 | The query arrives | scoped before any search runs |
| 07 | Hybrid retrieval | dense and lexical channels in parallel |
| 08 | Fusion and ranking | RRF over ranks, then a swappable reranker |
| 09 | Context assembly | bounded window, recorded omissions |
| 10 | Generation | LLM prose or the extractive quote, named as-is |
| 11 | Citation validation | markers bound server-side or removed |
| 12 | Abstention and conflicts | decline honestly; surface both values |
| 13 | Trust boundaries | instruction-like text stays data |
| 14 | Update, delete, recover | revisions, deletions, staged rebuild, rollback |
| 15 | The finished answer | the whole path, inspectable stage by stage |

## Rendering and fallbacks

- **WebGL2** path: ~2 600 particles per scene, sixteen pre-computed formations in
  one buffer, two attribute pointers for cross-scene interpolation, additive
  blending, seeded so the film is identical on every load.
- **WebGL1** path: the same scene graph through a GLSL 100 shader pair.
- **Canvas 2D** fallback: if shader compilation or context creation fails, a
  simplified point cloud is drawn instead. The narration never changes.
- **No canvas at all** (headless DOM, denied context): the page still boots and
  the transcript is still readable — this is the path exercised by the jsdom
  smoke test.
- **Quality degradation**: sustained frames slower than ~30 fps drop the particle
  count and pixel ratio, once at medium and once at low; the badge tooltip says
  so.
- **Reduced motion**: `prefers-reduced-motion` (and the on-screen toggle) removes
  camera moves and transitions instead of merely shortening them.
- **Offline**: no web fonts, no CDN, no analytics, no remote assets. A CI step
  fails the build if a remote `src`/`href` appears.

## Recording the demo fixture

`scripts/record_experience_demo.py` performs a real ingest of `corpus/`, a real
query, and a real delete + rebuild in a temporary directory, captures the event
stream, and writes `ragformation/web/experience/demo_trace.json` with
`kind: "demo"` and the provider labels it actually used. Re-record after changing
the corpus or providers:

```bash
python scripts/record_experience_demo.py
node tests/experience_contract.mjs          # scene/fixture/timeline contracts
node scripts/experience_smoke.mjs --jsdom /path/to/node_modules
```

## What is verified, and what is not

Verified in this repository, without a browser:

- scene script completeness, formation builders, camera framing, stage→scene
  mapping against the stage names the engine really publishes;
- shader attribute/uniform names match what the renderer binds (both GLSL
  versions);
- timeline behaviour (play, seek, live advance, end-of-film, catch-up after a
  long frame);
- envelope validation, per-trace sequence tracking, event-id de-duplication, and
  the polling fallback;
- the page boots in jsdom, all three modes work against a stub server, the
  transcript renders 16 scenes, keyboard navigation advances the film, and an
  unreachable server is reported honestly.

**Not verified here:** that the WebGL shaders compile on real GPUs, frame pacing
on specific hardware, and the visual composition itself. No browser or GPU exists
in this environment. Treat the first two as "engineered and statically checked",
not "demonstrated", until someone opens it in a browser — then the instructions
above are the whole test.

## Serving it

```bash
ragformation serve                 # console at /, experience at /experience
```

When the server needs an API key, Live and Explorer will show the "requires
authorisation" notice, because a static page cannot hold a key safely. Director
mode works regardless: the recording is a file in the package.
