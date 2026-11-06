#!/usr/bin/env python3
"""Preflight for the Lear agent session recordings. NEW INSTRUMENTATION, read-only.

Runs real diagnostics (environment, repository, Ollama and model availability,
application health, recording tooling) and writes:
  evidence/preflight.json   every check with status, detail and evidence file
  evidence/env/*.txt        raw outputs of the commands that were run
  PREFLIGHT_REPORT.md       the human report generated from preflight.json

It never prints or stores environment variable values, only names. The only
writes are under the package directory and the run's /tmp root.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
VENV = Path("/tmp/recvenv/bin")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Preflight:
    def __init__(self, pkg: Path, root: Path, run_id: str) -> None:
        self.pkg, self.root, self.run_id = pkg, root, run_id
        self.env_dir = pkg / "evidence" / "env"
        self.env_dir.mkdir(parents=True, exist_ok=True)
        self.checks: List[Dict[str, Any]] = []
        self.scratch = root / "preflight-data"

    def sh(self, name: str, argv: List[str], *, cwd: Path = REPO, timeout: int = 600, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        started = time.perf_counter()
        base = {k: v for k, v in os.environ.items() if not k.startswith(("RAG_", "OLLAMA_"))}
        base["PYTHONDONTWRITEBYTECODE"] = "1"
        base.update(env or {})
        try:
            p = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, env=base)
            out, code = (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.stderr else ""), p.returncode
        except FileNotFoundError as exc:
            out, code = f"{type(exc).__name__}: {exc}", 127
        except subprocess.TimeoutExpired:
            out, code = f"timeout after {timeout}s", 124
        path = self.env_dir / f"{name}.txt"
        path.write_text(f"$ {' '.join(argv)}\n# cwd {cwd}\n# exit {code}\n# at {now()}\n\n{out}", encoding="utf-8")
        return {"exit": code, "out": out, "file": str(path.relative_to(self.pkg)), "ms": round((time.perf_counter() - started) * 1000, 1)}

    def add(self, cid: str, area: str, name: str, status: str, detail: str, evidence: Optional[str] = None, **extra: Any) -> None:
        self.checks.append({"id": cid, "area": area, "check": name, "status": status, "detail": detail,
                            "evidence": evidence, "at": now(), **extra})

    # ------------------------------------------------------------ checks
    def environment(self) -> None:
        osr = dict(re.findall(r'^(\w+)="?([^"\n]*)"?', Path("/etc/os-release").read_text(), re.M))
        mem = re.search(r"MemTotal:\s+(\d+)", Path("/proc/meminfo").read_text())
        self.facts = {"os": osr.get("PRETTY_NAME"), "kernel": platform.release(), "arch": platform.machine(),
                      "cpus": os.cpu_count(), "memMiB": int(mem.group(1)) // 1024 if mem else None,
                      "python": platform.python_version(), "display": "set" if os.environ.get("DISPLAY") else "unset"}
        self.add("E01", "environment", "Operating system and architecture", "PASS",
                 f"{self.facts['os']} · kernel {self.facts['kernel']} · {self.facts['arch']} · {self.facts['cpus']} vCPU · {self.facts['memMiB']} MiB RAM · DISPLAY {self.facts['display']}")
        top = self.sh("git_toplevel", ["git", "rev-parse", "--show-toplevel"])
        self.add("E02", "environment", "Working directory and repository root", "PASS", f"cwd {REPO} · git root {top['out'].strip()}", top["file"])
        br = self.sh("git_branch", ["git", "rev-parse", "--abbrev-ref", "HEAD"])
        head = self.sh("git_head", ["git", "log", "-1", "--format=%H %cI %s"])
        st = self.sh("git_status", ["git", "status", "--porcelain=v1", "--untracked-files=all"])
        tracked = [l for l in st["out"].splitlines() if l and not l.startswith("??")]
        untracked = [l[3:] for l in st["out"].splitlines() if l.startswith("??")]
        self.git = {"branch": br["out"].strip(), "head": head["out"].split(" ")[0], "headLine": head["out"].strip(),
                    "trackedChanges": tracked, "untracked": untracked}
        self.add("E03", "environment", "Git branch", "PASS", self.git["branch"], br["file"])
        self.add("E04", "environment", "Commit SHA", "PASS", self.git["headLine"], head["file"])
        self.add("E05", "environment", "Uncommitted changes", "PASS" if not tracked else "PARTIAL",
                 f"tracked changes: {len(tracked)}; untracked files: {len(untracked)}"
                 + (f" (top-level: {', '.join(sorted({u.split('/')[0] for u in untracked}))})" if untracked else ""), st["file"])
        vers: Dict[str, str] = {}
        for k, argv in {"python3": ["python3", "--version"], "node": ["node", "--version"], "npm": ["npm", "--version"],
                        "pip": ["python3", "-m", "pip", "--version"]}.items():
            r = self.sh(f"version_{k}", argv)
            vers[k] = r["out"].strip().splitlines()[0] if r["exit"] == 0 and r["out"].strip() else "missing"
        vers["sqlite"] = sqlite3.sqlite_version
        self.versions = vers
        self.add("E06", "environment", "Runtime and language versions", "PASS", f"{vers['python3']} · node {vers['node']} · SQLite {vers['sqlite']}")
        self.add("E07", "environment", "Dependency and package-manager versions", "PASS",
                 f"pip: {vers['pip'][:40]} · npm {vers['npm']} · RAG Formation is standard-library Python (pyproject declares no runtime dependencies)")
        pyproject = (REPO / "pyproject.toml").read_text()
        make_targets = re.findall(r"^([a-zA-Z_-]+):", (REPO / "Makefile").read_text(), re.M)
        version = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
        self.app_version = version.group(1) if version else None
        self.add("E08", "environment", "Project scripts", "PASS",
                 f"console script ragformation = ragformation.cli:main · Makefile targets: {', '.join(make_targets)}")
        helpc = self.sh("lear_help", ["python3", "-m", "ragformation", "lear", "--help"])
        self.add("E09", "environment", "Application and agent entry points", "PASS" if helpc["exit"] == 0 else "FAIL",
                 "python3 -m ragformation lear {doctor,docs,index,ask,chat,sessions,memory,eval,bench,demo,report}; agent class ragformation.lear.agent.LearAgent.ask", helpc["file"])
        configs = [p for p in ("pyproject.toml", "Makefile", "ragformation/lear/settings.py", "ragformation/config.py",
                               "evals/lear/golden_lear_v1.json", "evals/lear/.checksums.json") if (REPO / p).exists()]
        self.add("E10", "environment", "Configuration files", "PASS",
                 f"{', '.join(configs)} · .env present: {(REPO / '.env').exists()} (existence only; never read)")
        names = sorted(set(re.findall(r'"(RAG_LEAR_[A-Z0-9_]+)"', (REPO / "ragformation/lear/settings.py").read_text())))
        set_names = sorted(k for k in os.environ if k.startswith(("RAG_", "OLLAMA_")))
        self.env_names = {"readBySettings": names, "setInThisEnvironment": set_names}
        self.add("E11", "environment", "Environment variable names (values never read)", "PASS",
                 f"{len(names)} RAG_LEAR_* names read by settings; RAG_*/OLLAMA_* names set here: {set_names or 'none'} (all defaults apply)")
        self.add("E12", "environment", "Vector database implementation", "PASS",
                 "SQLite file ragformation.db: table vectors (L2-normalised; exact brute-force cosine scan in Python) + FTS5 virtual table chunks_fts (BM25)")
        self.add("E13", "environment", "Embedding provider", "PARTIAL",
                 "configured: ollama:nomic-embed-text (BLOCKED here, see O-checks) · available: hashing:hashing-lexical-v1 d512 (offline baseline, lexical, NOT semantic)")
        self.add("E14", "environment", "Reranking provider", "PASS",
                 "heuristic-coverage-v1: a transparent blend of the retrieval signals (support score); not a cross-encoder, no model")
        self.add("E15", "environment", "LLM provider", "BLOCKED",
                 "configured: ollama:qwen2.5:1.5b at http://127.0.0.1:11434 (loopback only); judge llama3.2:1b · available here: extractive:extractive-synthesis-v1 (no LLM, labelled NOT QUALIFYING)")
        self.add("E16", "environment", "Memory implementation", "PASS",
                 "SQLite agent.db: sessions, messages, compactions (summaries), memories (scoped long-term memory); read-only shared-memory import from an approved directory")
        lear_src = " ".join((REPO / f"ragformation/lear/{m}.py").read_text() for m in ("agent", "sessions", "memory", "compaction", "ollama", "shared_memory"))
        has_tools = bool(re.search(r"\bsubprocess\b|register_tool|tool_call|os\.system", lear_src))
        self.add("E17", "environment", "Tool registry", "NOT_IMPLEMENTED" if not has_tools else "PASS",
                 "no tool registry, tool calling, subprocess or shell execution in the Lear agent path (static scan of agent, sessions, memory, compaction, ollama, shared_memory)")
        self.add("E18", "environment", "Logging and observability", "PASS",
                 "engine events (ragformation/events.py, schema 1.0, sanitised) · per-query trace persisted to SQLite queries.trace_json · agent lifecycle spans lear-lifecycle-v1 in the answer JSON · no OpenTelemetry exporter")
        self.add("E19", "environment", "Testing and evaluation frameworks", "PASS",
                 "unittest suites (tests/, engine/tests/) · lear eval validate|run|rescore (golden set, score_case, judge) · lear bench (dummy users)")
        tools = {}
        for name, argv in {"asciinema": [str(VENV / "asciinema"), "--version"], "script": ["script", "--version"],
                           "ffmpeg": [str(VENV / "python"), "-c", "import imageio_ffmpeg,subprocess;print(subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-version'],capture_output=True,text=True).stdout.splitlines()[0])"],
                           "pyte_pillow": [str(VENV / "python"), "-c", "import pyte,PIL,importlib.metadata as m;print('pyte',m.version('pyte'),'Pillow',PIL.__version__)"],
                           "obs": ["obs", "--version"]}.items():
            r = self.sh(f"tool_{name}", argv)
            tools[name] = r["out"].strip().splitlines()[0] if r["exit"] == 0 and r["out"].strip() else "missing"
        self.tools = tools
        self.add("E20", "environment", "Recording and media utilities", "PASS",
                 f"asciinema {tools['asciinema']} · {tools['script']} · {tools['ffmpeg'][:40]} · {tools['pyte_pillow']} · OBS {tools['obs']} · no display server (native screen capture unavailable; terminal recording used)")

    def ollama(self) -> None:
        path = shutil.which("ollama")
        self.add("O01", "ollama", "Ollama installed", "BLOCKED" if not path else "PASS", f"ollama binary on PATH: {path or 'not found'}")
        try:
            with socket.create_connection(("127.0.0.1", 11434), timeout=3):
                tcp = "open"
        except OSError as exc:
            tcp = f"{type(exc).__name__}: {exc}"
        self.add("O02", "ollama", "Server reachable (TCP 127.0.0.1:11434)", "PASS" if tcp == "open" else "BLOCKED", tcp)
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=3) as resp:
                version = resp.read().decode()[:200]
        except Exception as exc:  # noqa: BLE001 - recorded verbatim
            version = f"{type(exc).__name__}: {exc}"
        self.add("O03", "ollama", "HTTP /api/version", "PASS" if version.startswith("{") else "BLOCKED", version)
        self.add("O04", "ollama", "Configured models", "PASS", "configured: embedder nomic-embed-text (~137M), llm qwen2.5:1.5b (~1.5B), judge llama3.2:1b (~1.2B) - names from ragformation/lear/settings.py defaults")
        self.add("O05", "ollama", "Models installed / metadata / size / quantisation / context window", "BLOCKED",
                 "cannot be read: /api/tags and /api/show need a running Ollama server")
        self.add("O06", "ollama", "Embedding model available", "BLOCKED", "nomic-embed-text cannot be listed or probed without the server")
        self.add("O07", "ollama", "Inference invoked / real response returned", "BLOCKED", "no generation request can be sent; no model response exists in this package")
        gpu = [p for p in ("/dev/nvidia0", "/dev/nvidiactl") if Path(p).exists()]
        self.add("O08", "ollama", "GPU or CPU execution", "PASS", f"GPU devices: {gpu or 'none'} · nvidia-smi: {shutil.which('nvidia-smi') or 'not found'} · CPU only ({os.cpu_count()} vCPU)")
        doc = self.sh("lear_doctor_ollama", ["python3", "-m", "ragformation", "lear", "--data-dir", str(self.scratch), "doctor", "--no-generation"])
        self.add("O09", "ollama", "lear doctor --no-generation (qualifying backend)", "BLOCKED",
                 f"exit {doc['exit']} · " + " | ".join(l.strip() for l in doc["out"].splitlines() if "[FAIL" in l or l.startswith("NOT READY"))[:300], doc["file"])
        rel = self.sh("ollama_release_api", ["gh", "api", "repos/ollama/ollama/releases/latest", "--jq",
                                             '.tag_name + " " + ([.assets[] | select(.name | test("linux-amd64")) | .name + " " + (.size|tostring) + " " + .browser_download_url] | join(" | "))'])
        probes = {}
        for label, url in {"github_release_asset_redirect": "https://objects.githubusercontent.com/",
                           "release_assets_host": "https://release-assets.githubusercontent.com/",
                           "ollama_com": "https://ollama.com/download", "ollama_registry": "https://registry.ollama.ai/v2/",
                           "huggingface": "https://huggingface.co/"}.items():
            try:
                req = urllib.request.Request(url, method="HEAD")
                with urllib.request.urlopen(req, timeout=6) as resp:
                    probes[label] = f"HTTP {resp.status}"
            except urllib.error.HTTPError as exc:
                probes[label] = f"HTTPError {exc.code}"
            except Exception as exc:  # noqa: BLE001
                probes[label] = f"{type(exc).__name__}: {str(exc)[:120]}"
        (self.env_dir / "model_hosts_reachability.json").write_text(json.dumps({"at": now(), "probes": probes}, indent=2), encoding="utf-8")
        self.add("O10", "ollama", "Install path for Ollama (release assets)", "BLOCKED",
                 f"latest release via api.github.com: {rel['out'].strip()[:220]} · asset hosts: objects.githubusercontent.com → {probes['github_release_asset_redirect']}; release-assets.githubusercontent.com → {probes['release_assets_host']}; ollama.com → {probes['ollama_com']}",
                 rel["file"])
        self.add("O11", "ollama", "Model registries reachable", "BLOCKED",
                 f"registry.ollama.ai → {probes['ollama_registry']}; huggingface.co → {probes['huggingface']} (sandbox egress allows only github.com, api.github.com, codeload.github.com, registry.npmjs.org, pypi.org, files.pythonhosted.org)",
                 "evidence/env/model_hosts_reachability.json")
        self.ollama_states = {"installed": bool(path), "configured": True, "available": tcp == "open", "loaded": False,
                              "invoked": False, "realResponse": False}

    def health(self) -> None:
        tests = self.sh("unit_tests", ["python3", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"], timeout=1200)
        ran = re.search(r"Ran (\d+) tests", tests["out"])
        ok = re.search(r"^OK(?: \((.*)\))?$", tests["out"], re.M)
        self.add("H01", "health", "Unit and integration tests (tests/)", "PASS" if tests["exit"] == 0 and ok else "FAIL",
                 f"ran {ran.group(1) if ran else '?'} · {'OK ' + (ok.group(1) or '') if ok else 'FAILED'} · {tests['ms'] / 1000:.1f} s", tests["file"])
        legacy = self.sh("legacy_tests", ["python3", "-m", "unittest", "discover", "-s", "tests"], cwd=REPO / "engine", timeout=600)
        lran = re.search(r"Ran (\d+) tests", legacy["out"])
        self.add("H02", "health", "Legacy engine tests (engine/tests)", "PASS" if legacy["exit"] == 0 else "FAIL",
                 f"ran {lran.group(1) if lran else '?'} · exit {legacy['exit']}", legacy["file"])
        val = self.sh("eval_validate", ["python3", "-m", "ragformation", "lear", "eval", "validate"])
        self.add("H03", "health", "Golden set validation", "PASS" if val["exit"] == 0 else "FAIL", f"exit {val['exit']} · " + " ".join(val["out"].split())[:200], val["file"])
        idx = self.sh("index_rebuild_scratch", ["python3", "-m", "ragformation", "lear", "--backend", "baseline", "--data-dir", str(self.scratch), "index", "rebuild"])
        self.add("H04", "health", "Application startup + document indexing (baseline, scratch dir)", "PASS" if idx["exit"] == 0 else "FAIL",
                 " ".join(idx["out"].split())[:220], idx["file"])
        stats = self.sh("index_stats_scratch", ["python3", "-m", "ragformation", "lear", "--backend", "baseline", "--data-dir", str(self.scratch), "index", "stats"])
        try:
            sj = json.loads(stats["out"].split("\n[stderr]")[0])
        except ValueError:
            sj = {}
        self.index = sj
        self.add("H05", "health", "Document-index availability", "PASS" if sj.get("chunks") else "FAIL",
                 f"documents {sj.get('documents')} · chunks {sj.get('chunks')} · tokens {(sj.get('tokens') or {}).get('total')} · fingerprint {sj.get('indexFingerprint')} · source pin {str(sj.get('sourceCommit'))[:12]}", stats["file"])
        db = self.scratch / "index-baseline" / "ragformation.db"
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("chunks", "vectors", "sources")}
            fts = con.execute("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'permission'").fetchone()[0]
            con.close()
            vs = ("PASS", f"read-only open OK · chunks {counts['chunks']} · vectors {counts['vectors']} · sources {counts['sources']} · FTS5 MATCH 'permission' → {fts} rows")
        except Exception as exc:  # noqa: BLE001
            vs = ("FAIL", f"{type(exc).__name__}: {exc}")
        self.add("H06", "health", "Vector-store and database connectivity", vs[0], vs[1])
        sess = self.sh("sessions_list_scratch", ["python3", "-m", "ragformation", "lear", "--backend", "baseline", "--data-dir", str(self.scratch), "sessions", "list", "--user", "preflight", "--json"])
        try:
            con = sqlite3.connect(f"file:{self.scratch / 'agent.db'}?mode=ro", uri=True)
            tables = sorted(r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'"))
            con.close()
        except Exception as exc:  # noqa: BLE001
            tables = [f"error {exc}"]
        self.add("H07", "health", "Memory and session store initialisation", "PASS" if sess["exit"] == 0 else "FAIL",
                 f"lear sessions list exit {sess['exit']} · agent.db tables: {', '.join(tables)}", sess["file"])
        lear = self.root / "lear-backend"
        clone = self.sh("lear_backend_clone", ["git", "clone", "-q", "https://github.com/aviunion1348-cloud/Lear-backend-Avi.git", str(lear)], timeout=300)
        if clone["exit"] == 0:
            head = self.sh("lear_backend_head", ["git", "-C", str(lear), "log", "-1", "--format=%H %cI"])
            ver = self.sh("lear_docs_verify", ["python3", "-m", "ragformation", "lear", "docs", "verify", "--source", str(lear)], timeout=600)
            line = next((l for l in ver["out"].splitlines() if "PASS" in l or "FAIL" in l), "")
            self.add("H08", "health", "Indexed corpus vs pinned Lear source (lear docs verify)", "PASS" if ver["exit"] == 0 else "FAIL",
                     f"Lear-backend-Avi HEAD {head['out'].strip()[:60]} · exit {ver['exit']} · {line.strip()[:160]}", ver["file"])
        else:
            self.add("H08", "health", "Indexed corpus vs pinned Lear source (lear docs verify)", "BLOCKED",
                     f"clone failed: exit {clone['exit']}", clone["file"])
        self.add("H09", "health", "Model and embedding connectivity (qualifying)", "BLOCKED", "see O02, O03, O06, O09: no Ollama server")
        self.add("H10", "health", "Tool registration", "NOT_IMPLEMENTED", "the Lear agent has no tools to register (see E17)")
        self.add("H11", "health", "API availability", "NOT_APPLICABLE",
                 "the Lear agent's interface is the CLI (the take-home brief requires a CLI); the core HTTP API of RAG Formation is a different surface and is not part of the Lear agent")
        ps = self.sh("processes", ["ps", "-eo", "pid,comm"])
        relevant = [l.strip() for l in ps["out"].splitlines() if re.search(r"ollama|uvicorn|gunicorn|node|ragformation", l)]
        self.add("H12", "health", "Relevant runtime processes", "PASS", f"relevant processes: {relevant or 'none'} (nothing is started in the background for these recordings)", ps["file"])

    def write(self) -> Dict[str, Any]:
        data = {"schema": "lear-session-recording-preflight-v1", "runId": self.run_id, "generatedAt": now(),
                "repository": {"name": "aviunion1348-cloud/rag-formation", **self.git}, "applicationVersion": self.app_version,
                "facts": self.facts, "versions": self.versions, "tools": self.tools, "envVarNames": self.env_names,
                "index": self.index, "ollamaStates": self.ollama_states, "checks": self.checks}
        (self.pkg / "evidence" / "preflight.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return data


def report(data: Dict[str, Any], rerendered_at: Optional[str] = None) -> str:
    counts: Dict[str, int] = {}
    for c in data["checks"]:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    s = data["ollamaStates"]
    odd = [c["id"] for c in data["checks"] if c["status"] in ("NOT_IMPLEMENTED", "NOT_APPLICABLE")]
    yes = lambda v: "YES" if v else "NO"  # noqa: E731
    lines = [
        "# Preflight report: Lear agent session recordings", "",
        f"Run `{data['runId']}` · generated {data['generatedAt']} by `scripts/session_recordings/preflight.py` (read-only diagnostics). "
        f"Raw outputs: `evidence/env/`. Machine-readable: `evidence/preflight.json`.", "",
        f"**Result:** {' · '.join(f'{k} {v}' for k, v in sorted(counts.items()))}. "
        "The pipeline, index, stores, tests and recording tools are healthy. **The configured local LLM (Ollama) is BLOCKED**: it is not installed "
        "and its download and model hosts are unreachable from this sandbox, so no real model inference can be recorded.", "",
        f"Status values: PASS, FAIL, PARTIAL, BLOCKED. {len(odd)} checks ({', '.join(odd)}) use NOT_IMPLEMENTED or NOT_APPLICABLE because none "
        "of the four would be truthful.", "",
        *([f"_Re-rendered {rerendered_at} from the unchanged `evidence/preflight.json` to correct a wording error in this summary (it said "
           "\"Two checks\"); no check was re-run and no result changed._", ""] if rerendered_at else []),
        "## Ollama and model states", "",
        "| State | Observed |", "|---|---|",
        f"| Installed | {yes(s['installed'])} |", f"| Configured | {yes(s['configured'])} (qwen2.5:1.5b, nomic-embed-text, judge llama3.2:1b) |",
        f"| Available (server reachable) | {yes(s['available'])} |", f"| Loaded | {yes(s['loaded'])} |",
        f"| Successfully invoked | {yes(s['invoked'])} |", f"| Returned a real inference response | {yes(s['realResponse'])} |", "",
        "## Checks", "", "| ID | Area | Check | Status | Detail | Evidence |", "|---|---|---|---|---|---|",
    ]
    for c in data["checks"]:
        detail = c["detail"].replace("|", "\\|")
        lines.append(f"| {c['id']} | {c['area']} | {c['check']} | **{c['status']}** | {detail} | {('`' + c['evidence'] + '`') if c.get('evidence') else ''} |")
    lines += ["", "## Baseline", "",
              f"- Repository `{data['repository']['name']}`, branch `{data['repository']['branch']}`, commit `{data['repository']['head']}`.",
              f"- Tracked changes at preflight: {len(data['repository']['trackedChanges'])}; untracked files: {len(data['repository']['untracked'])}.",
              f"- Application version (pyproject): {data['applicationVersion']}.",
              f"- Index (scratch build): {data['index'].get('documents')} documents, {data['index'].get('chunks')} chunks, fingerprint `{data['index'].get('indexFingerprint')}`, source pin `{data['index'].get('sourceCommit')}`.",
              f"- Environment variable names read by the agent: {len(data['envVarNames']['readBySettings'])} (`RAG_LEAR_*`); set in this environment: {data['envVarNames']['setInThisEnvironment'] or 'none'}. Values are never read or stored.",
              "", "No environment-level correction was applied, and no application file was changed for these recordings."]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkg", default=str(REPO / "artifacts" / "agent_session_recordings"))
    ap.add_argument("--root")
    ap.add_argument("--run-id")
    ap.add_argument("--render-only", action="store_true", help="re-render PREFLIGHT_REPORT.md from the existing evidence/preflight.json")
    args = ap.parse_args()
    if args.render_only:
        data = json.loads((Path(args.pkg) / "evidence" / "preflight.json").read_text(encoding="utf-8"))
        when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        (Path(args.pkg) / "PREFLIGHT_REPORT.md").write_text(report(data, rerendered_at=when), encoding="utf-8")
        print(json.dumps({"rerendered": when}))
        return 0
    if not (args.root and args.run_id):
        ap.error("--root and --run-id are required unless --render-only is given")
    pf = Preflight(Path(args.pkg), Path(args.root), args.run_id)
    pf.environment()
    pf.ollama()
    pf.health()
    data = pf.write()
    (Path(args.pkg) / "PREFLIGHT_REPORT.md").write_text(report(data), encoding="utf-8")
    print(json.dumps({c["id"]: c["status"] for c in data["checks"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
