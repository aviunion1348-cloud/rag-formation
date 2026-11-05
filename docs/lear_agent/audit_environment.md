# Audit environment

Everything in `docs/lear_agent/` was produced and measured on the machine described here. The raw probe output is in [evidence/environment_probe_2026-11-05.txt](evidence/environment_probe_2026-11-05.txt).

## Machine

| Item | Value |
|---|---|
| OS | Debian GNU/Linux 12 (bookworm), Linux 6.1 x86_64, sandboxed |
| CPU / RAM / disk | 2 vCPU / 3.8 GiB RAM / 21 GB disk (19 GB free) |
| GPU | None (`nvidia-smi` absent) |
| Python / Node / SQLite / git | 3.11.2 / 22.22.3 / 3.40.1 / 2.39.5 |
| Ollama | **Not installed**; nothing listens on `127.0.0.1:11434` |
| Clock | UTC. Evidence files are dated in UTC (`2026-11-05`); the user's time zone is IST (UTC+5:30), where the work finished on 2026-11-06. The sandbox appears to be suspended between work sessions, so timestamps put events in order but do not measure elapsed working time. For example, the round-0 and round-3 runs are stamped 22 minutes apart. |

## Network

The sandbox allows HTTPS to `github.com`, `api.github.com`, `codeload.github.com`, `pypi.org`, `files.pythonhosted.org` and `registry.npmjs.org`.

The following are **blocked** (TLS connect failure, curl exit 35):
- `registry.ollama.ai` and `ollama.com`: no model pulls.
- `huggingface.co`.
- `objects.githubusercontent.com` and `raw.githubusercontent.com`: the Ollama release binary (v0.40.14 for linux-amd64, about 1.5 GB) is served from there, so Ollama cannot be installed.

**Consequence:** no local LLM, embedder or judge can run here. Every model-dependent requirement is BLOCKED rather than simulated. See [model_compliance.md](model_compliance.md).

## rag-formation checkout

| Item | Value |
|---|---|
| Repository | `aviunion1348-cloud/rag-formation` (shallow clone) |
| Base | `main` at `986ba5587648` (merge of PR #3) |
| Working branch | `arena/d352c257-rag-formation`. All commits are here; nothing was force-pushed, rebased or merged. |
| Other remote branches | Only `arena/5f16d127-rag-formation` at `9e0918b58b48` is ahead of `main`: 1 commit, 70 files, "Qualification: root-cause fixes, frozen holdouts, gates and final report". `arena/56047441…`, `arena/9c348a7a…` and `feat/01-rag-engine` are behind `main` with nothing unmerged. |

**The `9e0918b58b48` divergence is reported only.**
- That commit is a separate qualification of the core engine. It changes `engine.py`, `generation.py`, `retrieval.py`, `loaders.py`, `chunking.py`, `cli.py` and `web/experience/styles.css`, and adds `docs/final_qualification/`.
- It contains no Lear agent.
- It was not fetched, merged or cherry-picked, because the task rules forbid it without authorisation.
- Merging it later will need conflict resolution in `ragformation/cli.py`, which both branches touch.

## Lear source (the system being documented)

| Item | Value |
|---|---|
| Repository | `aviunion1348-cloud/Lear-backend-Avi`, a fork of `ananttheacharya/Lear-Backend` |
| Local path | `/home/user/lear-backend` (read-only; clean worktree) |
| Pin | `main` at **`60cf9abf8408`** (`60cf9abf84088c3a5ec05e8700c52185444e215f`), committed 2026-10-01T19:21:30+05:30, "Merge branch 'Drufiy:main' into main" |
| Recorded in | `docs/lear_backend/SOURCE_PIN.json` (454 tracked files). `docs verify` fails if the checkout's HEAD differs. |
| Upstream | `ananttheacharya/Lear-Backend` main at `5ea73a140e28` (2026-10-04) is 1 commit ahead of the pin and was not documented |
| Unmerged fork branches | `arena/01a0f7f6-lear-backend-avi` (15 commits ahead, 30 files: security headers middleware, gitleaks, secrets audit) and `arena/01a0ee2a-lear-backend-avi` (12 ahead, 4 behind). Neither is on `main`, so neither is documented. Whether either changes the reported auth findings was not checked beyond the file list; `prash/server.py` gains only 5 lines on the first. |
| Other candidates checked | `Lear-Avi-tasks` (task documents), `lear.com`, `lear.final` (386 MB, not cloned), `learfinalised` (empty). None is the backend source. No shared-memory repository was found. |
| Package | `prash-v2` 0.2.0 (`pyproject.toml`) |

### Lear's own test status at the pin (reported, not fixed)

| Run | Result | Evidence |
|---|---|---|
| Declared dependencies only | 3 collection errors (`python-multipart` is missing from the declared dependencies) | [evidence/lear_pytest_run1_declared_deps.txt](evidence/lear_pytest_run1_declared_deps.txt) |
| Plus `python-multipart` | 28 failed, 826 passed, 10 skipped (the README claims 457 passed, 7 skipped) | [evidence/lear_pytest_run2_with_multipart.txt](evidence/lear_pytest_run2_with_multipart.txt) |
| Plus `LEAR_MOCK_SERVICES=false` | 12 failed, 842 passed, 10 skipped | [evidence/lear_pytest_run3_mock_services_false.txt](evidence/lear_pytest_run3_mock_services_false.txt) |
| GitHub Actions run for the pinned commit | Concluded `failure` | [evidence/lear_ci_runs_2026-11-05.json](evidence/lear_ci_runs_2026-11-05.json) |

The Lear suite ran in a scratch virtual environment (`/tmp/lear-run`) and was never installed into the Lear checkout.

## Data handling

- **Kept local.** `data/` is gitignored, and nothing under it was deleted or overwritten outside the documented runs. The evaluation history is kept, and each round has its own run directory.
- **Committed.** Only the evaluation summaries (`evals/lear/results/*/run.json` and `summary.md`) and small evidence files are committed. `cases.jsonl`, the SQLite files and the indexes are not.
- **No secrets.** No secret values appear in the documentation (the `docs verify` secrets check passes). Lear credential settings are listed by name only.
- **No personal data.** Personal data found in Lear sources (for example, in `prash/conversation_router.py`) is not quoted anywhere in the documentation.
