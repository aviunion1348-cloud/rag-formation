# Recording verification

Generated 2026-11-06T12:45:34Z from `evidence/verification.json` (verifier run 2026-11-06T12:43:27Z, security re-scan 2026-11-06T12:45:34Z) by `scripts/session_recordings/verify_package.py`. Tools: ffmpeg version n9.0.2-17-g2a571b6068-20260930 Copyright (c) 2000-2026 the FFmpeg developers (ffprobe is not installed, so stream data comes from `ffmpeg -i`), asciinema 2.4.0, Pillow.

## 1. File existence

| file | bytes | modified (UTC) | sha256 |
|---|---|---|---|
| 01_successful_rag_session.mp4 | 4361013 | 2026-11-06T12:29:18Z | `ec1569d3dc30159f214c26d1d716386f5c2195a16463966ac0b866342947ac0e` |
| 02_multiturn_memory_session.mp4 | 9159033 | 2026-11-06T12:30:22Z | `6a0209f86e043e6e44a9dbec7ed925e3bf56697d5c5190f71265871312c389c4` |
| 03_multidocument_retrieval.mp4 | 3041804 | 2026-11-06T12:30:46Z | `8fa83ed347a16dccb0a6f034069daf13d5510e23af2691aedd538aef3c411823` |
| 04_abstention_session.mp4 | 4279274 | 2026-11-06T12:31:19Z | `9bf1df2621b7e56f478b91a474a7a68958dff5aa373f509a60dd4dc07211d09f` |
| 05_guardrail_security_session.mp4 | 8033581 | 2026-11-06T12:32:19Z | `bc1083f6049af65ec585e6c9743e82f578da67b9424b2f87ebaa181fcaabe43e` |
| 06_conflict_detection_session.mp4 | 3059875 | 2026-11-06T12:32:41Z | `c2795ba4dad5f5deee68d1ccd8c59c84739857867d31678b15fb9114927d755e` |
| 07_context_compaction_session.mp4 | 17543049 | 2026-11-06T12:35:25Z | `9b68a19a627eede809c7efc084c63e3a456d2df73729e2e82489cf3d95f580fe` |
| 08_error_recovery_session.mp4 | 8905428 | 2026-11-06T12:36:28Z | `4a485bd93db41f8a4e29a1d65bd85c0d8993be7d03ed56599c09cd10ac272880` |
| 09_evaluation_benchmark_session.mp4 | 2292467 | 2026-11-06T12:36:40Z | `7e5f2096e7dabe9f0ddd104b3b2b4067650234d0eeb63270e2c1bfc87a81f1b7` |
| complete_agent_session.mp4 | 64088758 | 2026-11-06T12:37:51Z | `f1ebe55f65c7cebce93e430a4354b1bc2a573d8c27efbadc12685e9d33c12515` |
| evidence/attempts/attempt1_08_error_recovery_session.mp4 | 1830208 | 2026-11-06T12:36:54Z | `b4035126b0b20ce99423efcf9fb9f85db150c603f4a2dcea17eee7fb3849e684` |
| terminal/01_successful_rag_session.cast | 36255 | 2026-11-06T12:20:20Z | `4b5a1b67a92ae984a3f72e92fd7c4a4925a91c5eb84823ea53728d2ae494fecf` |
| terminal/02_multiturn_memory_session.cast | 82910 | 2026-11-06T12:20:22Z | `ab7ee766cb2cb62544b73a225ada226dd85990beb33f6ee3fd693488d3e0d3f9` |
| terminal/03_multidocument_retrieval.cast | 30465 | 2026-11-06T12:20:22Z | `5bb1add82f10c03b1cee82306eb7056835498d0833daae4a48d3ada7f47ffea3` |
| terminal/04_abstention_session.cast | 39417 | 2026-11-06T12:20:23Z | `c3f50565bfe1f73e65ebb402aa75e5abfbb22a5877c863471f360f7da72f6896` |
| terminal/05_guardrail_security_session.cast | 79850 | 2026-11-06T12:20:24Z | `e38ff56244f817390380a4dfe15787af252891d4e1681782e596eb8910a60fbe` |
| terminal/06_conflict_detection_session.cast | 28565 | 2026-11-06T12:20:24Z | `bd93fc2e9659f2a3c624ced474a5bbe802fdc404908f1018ff78adb7d08964e3` |
| terminal/07_context_compaction_session.cast | 193667 | 2026-11-06T12:20:27Z | `33869a8ed519362ccdd45ecf3f5060645ca9d27ae9c8083b013a9f8d1a4f5fc8` |
| terminal/08_error_recovery_session.cast | 40266 | 2026-11-06T12:22:53Z | `24bcd681dce78feef6444d549f504210e923c499b8cbc307be1352c43d314e06` |
| terminal/09_evaluation_benchmark_session.cast | 12311 | 2026-11-06T12:20:31Z | `44a52f0240268fa59043ce9517ddcbcecef4828a4292133e573b62f0d3d7bc30` |
| evidence/attempts/attempt1_08_error_recovery_session.cast | 13746 | 2026-11-06T12:20:27Z | `c12d74bb00371a642f49c8074929e5b0768fbc0c919103a84962ae8e08664e91` |

Documents and traces are covered by `checksums.sha256` (written last; verify with `sha256sum -c checksums.sha256` inside the package folder).

## 2. Video integrity

| file | container | codec | resolution | fps | duration | decoded frames | frames = duration × fps | decode errors | black spans | audio | sample frames non-blank | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 01_successful_rag_session.mp4 | mov,mp4,m4a,3gp,3g2,mj2 | h264 High | 1920x1080 | 15.0 | 1:56.6 | 1749 | yes | 0 | 0 | 0 (none by design) | 2/2 | **OK** |
| 02_multiturn_memory_session.mp4 | mov,mp4,m4a,3gp,3g2,mj2 | h264 High | 1920x1080 | 15.0 | 4:23.5 | 3952 | yes | 0 | 0 | 0 (none by design) | 2/2 | **OK** |
| 03_multidocument_retrieval.mp4 | mov,mp4,m4a,3gp,3g2,mj2 | h264 High | 1920x1080 | 15.0 | 1:33.7 | 1406 | yes | 0 | 0 | 0 (none by design) | 2/2 | **OK** |
| 04_abstention_session.mp4 | mov,mp4,m4a,3gp,3g2,mj2 | h264 High | 1920x1080 | 15.0 | 2:16.3 | 2044 | yes | 0 | 0 | 0 (none by design) | 2/2 | **OK** |
| 05_guardrail_security_session.mp4 | mov,mp4,m4a,3gp,3g2,mj2 | h264 High | 1920x1080 | 15.0 | 4:08.5 | 3728 | yes | 0 | 0 | 0 (none by design) | 2/2 | **OK** |
| 06_conflict_detection_session.mp4 | mov,mp4,m4a,3gp,3g2,mj2 | h264 High | 1920x1080 | 15.0 | 1:26.6 | 1299 | yes | 0 | 0 | 0 (none by design) | 2/2 | **OK** |
| 07_context_compaction_session.mp4 | mov,mp4,m4a,3gp,3g2,mj2 | h264 High | 1920x1080 | 15.0 | 11:32.2 | 10383 | yes | 0 | 0 | 0 (none by design) | 2/2 | **OK** |
| 08_error_recovery_session.mp4 | mov,mp4,m4a,3gp,3g2,mj2 | h264 High | 1920x1080 | 15.0 | 3:39.7 | 3296 | yes | 0 | 0 | 0 (none by design) | 2/2 | **OK** |
| 09_evaluation_benchmark_session.mp4 | mov,mp4,m4a,3gp,3g2,mj2 | h264 High | 1920x1080 | 15.0 | 37.9 s | 569 | yes | 0 | 0 | 0 (none by design) | 2/2 | **OK** |
| complete_agent_session.mp4 | mov,mp4,m4a,3gp,3g2,mj2 | h264 High | 1920x1080 | 15.0 | 34:16.2 | 30843 | yes | 0 | 0 | 0 (none by design) | 1/1 | **OK** |
| evidence/attempts/attempt1_08_error_recovery_session.mp4 | mov,mp4,m4a,3gp,3g2,mj2 | h264 High | 1920x1080 | 15.0 | 52.1 s | 782 | yes | 0 | 0 | 0 (none by design) | 2/2 | **OK** |

Method: full decode with `ffmpeg -v error -i FILE -map 0:v -f null -` (any error output fails the file); frame count from `-progress`; black-frame scan with `blackdetect=d=0.5:pic_th=0.98:pix_th=0.05`; sample frames decoded from each MP4 (end of the real-time part and middle of the paced replay) into `evidence/frames/`, with luminance standard deviation > 8 required. No audio track exists: terminal recordings carry no sound and no voiceover was added.

## 3. Terminal recording integrity

| cast | valid v2 | size | events | monotonic | recorded duration | idle limit | asciinema cat |
|---|---|---|---|---|---|---|---|
| terminal/01_successful_rag_session.cast | True | 158x40 | 37 | True | 1.322 s | none | exit 0 |
| terminal/02_multiturn_memory_session.cast | True | 158x40 | 94 | True | 1.221 s | none | exit 0 |
| terminal/03_multidocument_retrieval.cast | True | 158x40 | 48 | True | 0.369 s | none | exit 0 |
| terminal/04_abstention_session.cast | True | 158x40 | 86 | True | 0.62 s | none | exit 0 |
| terminal/05_guardrail_security_session.cast | True | 158x40 | 80 | True | 0.764 s | none | exit 0 |
| terminal/06_conflict_detection_session.cast | True | 158x40 | 32 | True | 0.33 s | none | exit 0 |
| terminal/07_context_compaction_session.cast | True | 158x40 | 147 | True | 2.115 s | none | exit 0 |
| terminal/08_error_recovery_session.cast | True | 158x40 | 254 | True | 63.381 s | none | exit 0 |
| terminal/09_evaluation_benchmark_session.cast | True | 158x40 | 16 | True | 3.983 s | none | exit 0 |
| evidence/attempts/attempt1_08_error_recovery_session.cast | True | 158x40 | 21 | True | 0.589 s | none | exit 0 |

Consistency with the per-step logs and harness timestamps:

| scenario | agent-command output: every line in cast | contiguous byte-identical block | helper steps (summary on screen) | out sha256 match | session IDs in cast | engine trace IDs match | harness elapsed | cast duration | cast start → harness start | steps.jsonl rows | events.jsonl rows |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 01 | yes (5 steps) | 5/5 | 0 | True | True | True | 1.258 s | 1.322 s | 0.474 s | 5 | 40 |
| 02 | yes (6 steps) | 6/6 | 0 | True | True | True | 1.17 s | 1.221 s | 0.943 s | 6 | 96 |
| 03 | yes (1 steps) | 1/1 | 0 | True | True | True | 0.314 s | 0.369 s | 0.324 s | 1 | 32 |
| 04 | yes (3 steps) | 3/3 | 0 | True | True | True | 0.565 s | 0.62 s | 0.837 s | 3 | 52 |
| 05 | yes (3 steps) | 3/3 | 1 | True | True | True | 0.714 s | 0.764 s | 0.587 s | 4 | 96 |
| 06 | yes (1 steps) | 1/1 | 0 | True | True | True | 0.279 s | 0.33 s | 0.49 s | 1 | 32 |
| 07 | yes (10 steps) | 10/10 | 0 | True | True | True | 2.064 s | 2.115 s | 0.96 s | 10 | 288 |
| 08 | yes (7 steps) | 6/7 | 0 | True | True | False | 63.327 s | 63.381 s | 0.838 s | 7 | 16 |
| 09 | yes (3 steps) | 3/3 | 1 | True | n/a | n/a | 3.921 s | 3.983 s | 0.982 s | 4 | 0 |

Every line that each agent CLI command printed (saved as `traces/NN/<step>.out`, sha256 in `steps.jsonl`) appears in that scenario's asciicast, and 38/39 outputs appear as one contiguous byte-identical block (carriage returns removed: the outer pseudo-terminal adds one). The exception is S08 step r1 (`lear chat`): the harness prints a labelled ▌USER line when it writes the burst into the chat's stdin, and that line lands between chunks of the chat's output. Two steps are computations of the harness, not agent commands — S05 `sc` (read-only corpus injection scan) and S09 `x1` (comparison with committed round-3): the terminal shows their labelled summary and the full JSON is in the `.out` file. The cast header timestamp has 1-second resolution, so the start offset is accurate to ±1 s. The S08 attempt-1 cast is checked structurally only; it has no result file.

## 4. Session consistency

- Session IDs are the agent's own (`--session rec-sNN-<stamp>`), printed by the agent in each answer JSON and found in the casts; the run ID `lear-rec-20261106T1219Z` is the recording's own identifier and is never presented as an agent session ID.
- Engine trace IDs (`correlationId` in each answer) match the trace rows exported read-only from the engine's SQLite `queries.trace_json`.
- Statuses in `session_metadata.json`, `session_index.md`, the chapter cards and `evidence/results/NN.json` come from the same result files.

## 5. Security scan

Scope: 181 text files under artifacts/agent_session_recordings (casts, per-step outputs, traces, JSON, Markdown); MP4 frames are rendered from the scanned casts and add no text of their own. Patterns: aws_access_key, bearer_token, email_address, env_dump_line, github_token, ipv4, jwt, openai_style_key, other_home_dir, private_key_block, secret_assignment, slack_token.

- Findings: **0**
- IPv4 addresses seen: {'192.0.2.10': 14, '127.0.0.1': 33, '192.0.2.0': 7, '127.0.0.0': 4, '0.0.0.0': 4} (allowed: 127.0.0.0/8, 0.0.0.0, 192.0.2.0/24 (TEST-NET-1, used as the injected unreachable host)).
- System prompt: 16 lines of LEAR_SYSTEM_PROMPT and the context/memory templates checked; disclosures: **0**. Prompts are referenced only by version (lear-assistant-v1) and token counts. The S05 leak check (c3) lists four short marker phrases from that prompt as its search terms; the prompt is versioned in this public repository (ragformation/lear/prompts.py) and no prompt line appears in any answer or file of the package.
- No environment dump: the casts' headers record only SHELL and TERM; child commands ran with RAG_*/OLLAMA_* variables removed except those a step sets explicitly (the injected test URL in S08).
- The Lear documentation corpus is generated from the public pin and contains no credentials; answers quote it with citations.

## 6. Final counts

| measure | value |
|---|---|
| scenarios attempted | 9 |
| scenarios recorded | 9 (plus 1 preserved aborted attempt of S08) |
| PASS | 2 |
| FAIL | 3 |
| PARTIAL | 4 |
| BLOCKED (whole scenario) | 0 |
| NOT_IMPLEMENTED (whole scenario) | 0 |
| capabilities BLOCKED inside scenarios | LLM generation, semantic embeddings, LLM judge (Ollama unavailable) |
| capabilities NOT_IMPLEMENTED | tool calling (the agent has no tool layer) |
| valid MP4 files | 11/11 |
| valid terminal recordings | 10/10 |
| native screen recordings | 0 (no display server; genuine terminal recordings used, as the brief allows) |
| scenarios whose agent-command output is fully present in the cast | 9/9 |
| agent-command outputs present as one contiguous block | 38/39 |
| security findings | 0 |
| system-prompt disclosures | 0 |
| media bytes (MP4 + casts) | 127,151,942 |
| package bytes when this file was written | 135,322,225 (final total printed by the checksums step) |

