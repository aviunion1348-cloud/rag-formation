#!/usr/bin/env python3
"""Write the documents of the Lear agent session recording package from its evidence.

Inputs (all inside artifacts/agent_session_recordings/ unless noted):
  scripts/session_recordings/scenario_plan.json   pre-registered plan
  evidence/results/NN.json                        harness result per scenario
  traces/NN/*                                     per-step outputs, answers, engine traces
  evidence/preflight.json                         environment, Ollama and health checks
  evidence/render/*.json                          renderer reports
  evidence/verification.json                      verify_package.py output
  evidence/complete_manifest.json                 make_complete.py output
Outputs: session_metadata.json, session_index.md, recording_verification.md,
MANAGER_HANDOFF.md, terminal/README.md, traces/README.md.

  python3 scripts/session_recordings/build_docs.py --run-id <run-id>
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "artifacts" / "agent_session_recordings"
PLAN = REPO / "scripts" / "session_recordings" / "scenario_plan.json"
REPO_NAME = "aviunion1348-cloud/rag-formation"
BRANCH = "arena/c2d86cd7-rag-formation"

# Step whose agent-reported timings represent each scenario in session_metadata.json.
PRIMARY = {"01": "a1", "02": "t2", "03": "q1", "04": "u2", "05": "p1", "06": "k1", "07": "f1", "08": None, "09": None}
CAPABILITY = {
    "01": "retrieval, reranking, context assembly, extractive answer, citation validation, engine trace",
    "02": "session history, follow-up rewriting, long-term memory note",
    "03": "citations from two distinct documents",
    "04": "abstention: scope guard and insufficient-support threshold",
    "05": "injection resistance, output guard, no tool execution",
    "06": "surfacing contradictory evidence; conflict detector",
    "07": "session compaction (lear-compaction-v1) and recall after it",
    "08": "dependency failure, config refusal, rate limit and recovery",
    "09": "golden-set evaluation reproduction and dummy-user benchmark",
}
# What happened, restating facts visible in the recording and traces (numbers are filled from evidence below).
NARRATIVE = {
    "01": "The first ask uses the configured Ollama backend: the agent returns ERROR (exit 6, dependency unavailable) with no answer and no silent fallback. "
          "The second ask uses the labelled baseline: GROUNDED with validated citations, and the engine's own persisted trace shows every stage from "
          "query_received to response_ready. The extractive answer is on topic but does not state the expected decision (PROMPT), so the frozen golden "
          "check for lear-003 fails.",
    "02": "A user-asserted memory note is stored and is retrieved in turn 1 (memoryUsed). Turns 2 and 3 are rewritten as follow-ups from the session "
          "history (the retrieval query shows 'follow-up to: ...'). Turn 3 (a controlled question) does not name audit.log, so its key-fact check fails.",
    "03": "The multi-hop golden question is answered with citations from two distinct indexed documents, but only half of the expected key facts "
          "appear in the extractive answer, so the golden check fails.",
    "04": "All three unanswerable questions abstain with no answer text: two through the scope guard (unknown_term, before retrieval, no engine call) "
          "and one after retrieval (insufficient_support, with a persisted engine trace).",
    "05": "The indexed corpus is scanned for injection text first (none found). Three attack prompts (one golden, two controlled) produce no secret "
          "value, no system-prompt text and no tool execution; answers stay grounded in cited corpus text, and the output guard raises "
          "possible_meta_disclosure on the system-prompt request. The agent has no tool layer, so tool misuse is structurally impossible rather than defended.",
    "06": "The answer surfaces both sides of the documented contradiction (the README's promised test counts versus the pinned commit's recorded run) "
          "with citations, which meets the golden expectations, but the engine's numeric conflict detector does not flag it (status GROUNDED).",
    "07": "Eight golden questions in one session trigger real compaction (lear-compaction-v1): summaries are persisted with their sha256 and later turns "
          "carry the summary version. The controlled follow-up about the second question is rewritten against the immediately preceding question "
          "instead, so the answer misses the earlier fact.",
    "08": "The Ollama path fails twice in the same classified way (exit 6, dependencyUnavailable, no answer). An injected non-loopback Ollama URL is "
          "refused (exit 2, config_error). In one chat session 20 requests are answered, the 21st is rate limited with a retry-after, and after a real "
          "wait the next request is answered. The index is healthy afterwards. Attempt 1 of this scenario was aborted by a recording-harness bug and is kept.",
    "09": "The golden set validates; the calibration+dev evaluation (42 cases) reproduces the committed round-3 results exactly for status, correctness and "
          "answer text; a 5-user benchmark completes with no errors. All timings are PIPELINE-ONLY and the judge scores are deterministic proxies.",
}


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def js_from_out(path: Path) -> Optional[Dict[str, Any]]:
    text = re.sub(r"\r+\n", "\n", path.read_bytes().decode("utf-8", errors="replace"))
    i = text.find("{")
    if i < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[i:])[0]
    except ValueError:
        return None


def fmt_s(seconds: Optional[float]) -> str:
    if seconds is None:
        return "n/a"
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:04.1f}" if m else f"{s:.1f} s"


def table(header: List[str], rows: List[List[Any]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in r) + " |")
    return "\n".join(out)


def nz(v: Any, reason: str = "") -> Any:
    return v if v is not None else (f"N/A ({reason})" if reason else "N/A")


class Package:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.plan = load(PLAN)
        self.sc = {s["id"]: s for s in self.plan["scenarios"]}
        self.res = {n: load(PKG / "evidence" / "results" / f"{n}.json") for n in self.sc}
        self.pre = load(PKG / "evidence" / "preflight.json")
        self.ver = load(PKG / "evidence" / "verification.json")
        self.man = load(PKG / "evidence" / "complete_manifest.json")
        self.render = {p.stem: load(p) for p in (PKG / "evidence" / "render").glob("*.json")}
        self.videos = {v["path"]: v for v in self.ver["videos"]}
        self.casts = {c["path"]: c for c in self.ver["casts"]}
        self.cons = {s["scenario"]: s.get("consistency") or {} for s in self.ver["scenarios"]}
        self.checks = {c["id"]: c for c in self.pre["checks"]}
        self.answers_full = {}
        for n in self.sc:
            for sid in self.res[n]["answers"]:
                p = PKG / "traces" / n / f"{sid}.answer.json"
                if p.exists():
                    self.answers_full[(n, sid)] = load(p)
        self.bench = load(PKG / "traces" / "09" / "bench.json")["levels"][0]
        self.eval = load(PKG / "traces" / "09" / "eval_run.json")["summary"]
        s7 = js_from_out(PKG / "traces" / "07" / "s7.out") or {}
        self.compactions = s7.get("compactions") or []
        self.now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ------------------------------------------------------------ helpers
    def passed(self, n: str) -> str:
        r = self.res[n]
        return f"{sum(c['outcome'] == 'PASS' for c in r['checks'])}/{len(r['checks'])}"

    def counts(self) -> Dict[str, int]:
        out = {k: 0 for k in ("PASS", "FAIL", "PARTIAL", "BLOCKED", "NOT_IMPLEMENTED", "NOT_RUN")}
        for r in self.res.values():
            out[r["status"]] = out.get(r["status"], 0) + 1
        return out

    def video(self, n: str) -> Dict[str, Any]:
        return self.videos.get(f"{self.sc[n]['file']}.mp4", {})

    def cast(self, n: str) -> Dict[str, Any]:
        return self.casts.get(f"terminal/{self.sc[n]['file']}.cast", {})

    def metrics(self, n: str) -> Dict[str, Any]:
        step = PRIMARY[n]
        if n == "09":
            b = self.bench
            return {"total_latency_ms": b["totalMs"]["p50"], "ttft_ms": b["ttftMs"]["p50"], "itl_median_ms": b["itlMs"]["p50"],
                    "itl_p95_ms": None, "output_tokens": None, "tokens_per_second": None,
                    "_source": "traces/09/bench.json levels[0] (5 users x 10 requests): p50 values; p85/p99 are in the file",
                    "_notes": ["PIPELINE-ONLY: extractive baseline, no model decode; not model latency.",
                               "itl_p95_ms: the bench reports p50/p85/p99, not p95.",
                               "output_tokens / tokens_per_second: no model tokens exist (the bench itself prints tok/s n/a)."]}
        if step is None:
            return {"total_latency_ms": None, "ttft_ms": None, "itl_median_ms": None, "itl_p95_ms": None, "output_tokens": None,
                    "tokens_per_second": None, "_source": None,
                    "_notes": ["Behavioural scenario: `lear ask` errors carry no stream; `lear chat` prints answers, not per-request timings. "
                               "Step wall times are in traces/08/steps.jsonl."]}
        a = self.answers_full[(n, step)]
        t = a.get("timings") or {}
        itl = t.get("itlMs") or {}
        return {"total_latency_ms": t.get("totalMs"), "ttft_ms": t.get("ttftMs"), "itl_median_ms": itl.get("p50"), "itl_p95_ms": None,
                "output_tokens": None, "tokens_per_second": None,
                "agent_reported_estimates": {"completion_tokens_estimated": t.get("completionTokens"), "tokens_estimated": t.get("tokensEstimated"),
                                             "tokens_per_sec_from_estimate": t.get("tokensPerSec"), "itl_p99_ms": itl.get("p99"),
                                             "itl_samples": itl.get("count"), "stream_events": t.get("streamEvents")},
                "_source": f"timings of the answer JSON printed by the CLI for step {step} (traces/{n}/{step}.answer.json), copied verbatim",
                "_notes": ["PIPELINE-ONLY: generation is the extractive baseline (not an LLM). ttft_ms is the time to the first streamed chunk of the "
                           "extractive answer, observed by the agent; itl_median_ms is the median gap between streamed chunks.",
                           "itl_p95_ms: the agent's ITL summary reports p50/p85/p99, not p95; raw samples are not printed by the CLI.",
                           "output_tokens / tokens_per_second: the agent's token counts are estimates (characters/4) of extractive text, so they are "
                           "listed under agent_reported_estimates and not presented as model throughput."]}

    def models(self, n: str) -> (str, str):
        gens = set()
        for (m, sid), a in self.answers_full.items():
            if m == n:
                g = ((a.get("models") or {}).get("generation") or {})
                if g.get("provider") or g.get("model"):
                    gens.add(f"{g.get('provider')}:{g.get('model')}")
        if n == "01" or n == "08":
            return ("qwen2.5:1.5b requested via Ollama (unavailable: exit 6), then extractive-synthesis-v1" if n == "01"
                    else "qwen2.5:1.5b requested via Ollama (unavailable: exit 6); extractive-synthesis-v1 in the chat steps",
                    "ollama (BLOCKED) / extractive baseline")
        if n == "09":
            return ("extractive-synthesis-v1 (baseline); judge: deterministic proxy scorer, no LLM judge", "extractive baseline")
        return (", ".join(sorted(gens)) or "none (no generation: abstained before or after retrieval)" if n == "04" else ", ".join(sorted(gens)),
                "extractive baseline (NOT an LLM)")

    def evidence(self, n: str) -> List[str]:
        files = sorted(p.relative_to(PKG).as_posix() for p in (PKG / "traces" / n).iterdir())
        ev = [f"{self.sc[n]['file']}.mp4", f"terminal/{self.sc[n]['file']}.cast", f"evidence/results/{n}.json", *files]
        if n == "08":
            ev += sorted(p.relative_to(PKG).as_posix() for p in (PKG / "evidence" / "attempts").rglob("*") if p.is_file())
        return ev

    def limitations(self, n: str) -> List[str]:
        r = self.res[n]
        out = []
        cap = r.get("cap") or {}
        if cap.get("reason"):
            out.append(f"status cap {cap['status']}: {cap['reason']}")
        for c in r["checks"]:
            if c["outcome"] != "PASS":
                out.append(f"check {c['id']} failed: {c['description']}")
        out.append("No answer in this scenario comes from an LLM: Ollama is BLOCKED in this environment (preflight O01-O11).")
        if n == "05":
            out.append("The agent has no tool layer (preflight E17 NOT_IMPLEMENTED), so tool-misuse resistance is structural, not a guardrail decision.")
        if n == "08":
            out.append("Attempt 1 aborted on a recording-harness display bug (fixed in 3cade47); kept under evidence/attempts/.")
        if n == "09":
            out.append("Judge scores are deterministic proxies; bench timings are pipeline-only; no model latency exists in this package.")
        return out

    def result_text(self, n: str) -> str:
        r = self.res[n]
        fails = [c["id"] for c in r["checks"] if c["outcome"] != "PASS"]
        return f"{r['status']}: {self.passed(n)} pre-registered checks passed" + (f" (failed: {', '.join(fails)})" if fails else "") + ". " + NARRATIVE[n]

    # ------------------------------------------------------------ session_metadata.json
    def metadata(self) -> Dict[str, Any]:
        pre = self.pre
        sessions = []
        for n, s in self.sc.items():
            r = self.res[n]
            model, provider = self.models(n)
            m = self.metrics(n)
            sessions.append({
                "scenario_id": n, "scenario_name": s["title"], "status": r["status"],
                "session_id": (r.get("sessionIds") or [None])[0], "session_ids": r.get("sessionIds") or [],
                "run_id": self.run_id, "started_at_utc": r["startedAt"], "ended_at_utc": r["endedAt"],
                "model": model, "provider": provider,
                "recording_file": f"{s['file']}.mp4", "terminal_recording_file": f"terminal/{s['file']}.cast",
                "trace_file": f"traces/{n}/events.jsonl", "engine_trace_ids": r.get("correlationIds") or [],
                "execution_surface": "CLI `python3 -m ragformation lear ...`, each command in its own pseudo-terminal, run by the recording harness inside asciinema",
                "recording_commit": r["git"]["commit"], "tracked_files_clean": r["git"]["trackedFilesClean"],
                "metrics": {k: m.get(k) for k in ("total_latency_ms", "ttft_ms", "itl_median_ms", "itl_p95_ms", "output_tokens", "tokens_per_second")},
                "metrics_source": m.get("_source"), "metrics_notes": m.get("_notes"),
                **({"agent_reported_estimates": m["agent_reported_estimates"]} if m.get("agent_reported_estimates") else {}),
                "result": self.result_text(n), "limitations": self.limitations(n), "evidence": self.evidence(n),
            })
        att = self.render.get("attempt1_08_error_recovery_session", {})
        return {
            "project": "Lear RAG Agent", "recording_package_version": "1.0", "generation_timestamp_utc": self.now,
            "repository": {"name": REPO_NAME, "branch": BRANCH, "commit_sha": self.res["01"]["git"]["commit"],
                           "working_tree_status": "tracked files clean during every recording (checked by the harness before each scenario); "
                                                  "untracked: artifacts/agent_session_recordings/ (the outputs being written)",
                           "recording_commits": {n: r["git"]["commit"] for n, r in self.res.items()},
                           "plan_registered_at_utc": self.plan["registeredAt"], "plan_sha256": self.res["01"]["planSha256"]},
            "environment": {"operating_system": "Debian GNU/Linux 12 (bookworm), kernel 6.1.158+",
                            "architecture": "x86_64 · 2 vCPU · 3939 MiB RAM · no GPU · no display server",
                            "runtime": f"{pre['versions']['python3']} (CPython) · SQLite {pre['versions']['sqlite']}",
                            "application_version": f"ragformation {pre['applicationVersion']} · Lear agent CLI `python3 -m ragformation lear`",
                            "evidence": "evidence/preflight.json checks E01, E06, E07; evidence/env/"},
            "providers": {
                "llm": {"configured": "ollama:qwen2.5:1.5b at http://127.0.0.1:11434 (loopback only)", "status": "BLOCKED",
                        "reason": "Ollama binary absent, server unreachable, release assets and model registries unreachable from this sandbox (preflight O01-O11)",
                        "used_in_recordings": "extractive:extractive-synthesis-v1, the labelled baseline that selects and cites corpus sentences; NOT an LLM"},
                "embedding": {"configured": "ollama:nomic-embed-text", "status": "BLOCKED",
                              "used_in_recordings": "hashing:hashing-lexical-v1 (512-d, L2-normalised; lexical baseline, not semantic)"},
                "vector_store": "SQLite ragformation.db: vectors table (exact brute-force cosine in Python) + FTS5 chunks_fts (BM25)",
                "reranker": "heuristic-coverage-v1 (rule-based support score; not a model), then MMR (lambda 0.72)",
                "memory": "SQLite agent.db: sessions, messages, compactions (lear-compaction-v1 summaries), memories (scoped long-term memory)"},
            "recording_tool": "asciinema 2.4.0 (asciicast v2, real timing, no idle-time limit) inside util-linux script 2.38.1; MP4 rendered by "
                              "scripts/session_recordings/render_cast.py (pyte 0.8.2, Pillow 13.0.0, ffmpeg n9.0.2, libx264)",
            "video_format": "1920x1080 H.264 High yuv420p 15 fps, no audio; part 1 real-time playback, part 2 paced replay of the same recording "
                            "(pauses labelled HELD FOR READING; the banner always shows the true recorded clock)",
            "sessions": sessions,
            "aborted_attempts": [{"scenario_id": "08", "attempt": 1, "status": "ABORTED (recording harness)", "counted_as_outcome": False,
                                  "terminal_recording_file": "evidence/attempts/attempt1_08_error_recovery_session.cast",
                                  "recording_file": "evidence/attempts/attempt1_08_error_recovery_session.mp4",
                                  "recorded_seconds": att.get("castLastEventS"), "recording_commit": "81c28c828be166fa51b66bd7cc8dadb0cd163c70",
                                  "cause": "after the CLI correctly refused the injected non-loopback URL (exit 2, config_error), the harness's compact "
                                           "view assumed the error field was an object and raised AttributeError; fixed in 3cade47 and re-recorded",
                                  "evidence": ["evidence/attempts/attempt1_outer_08.log", "evidence/attempts/attempt1_traces_08/"]}],
            "complete_recording": {"file": "complete_agent_session.mp4", "manifest": "evidence/complete_manifest.json",
                                   "segments": len(self.man["segments"]), "total_seconds": self.man["totalSeconds"],
                                   "editorial_seconds": self.man["editorialSeconds"], "method": self.man["method"]},
        }

    # ------------------------------------------------------------ session_index.md
    def session_index(self, meta: Dict[str, Any]) -> str:
        c = self.counts()
        rows = []
        for n, s in self.sc.items():
            r, v, ca = self.res[n], self.video(n), self.cast(n)
            rows.append([f"{n} {s['title']}", f"[{s['file']}.mp4]({s['file']}.mp4)", f"**{r['status']}**",
                         ", ".join(f"`{x}`" for x in r.get("sessionIds") or []) or "none (eval/bench)",
                         f"{ca.get('durationS')} s recorded · {fmt_s(v.get('durationS'))} video", CAPABILITY[n],
                         f"[cast](terminal/{s['file']}.cast) · [traces](traces/{n}/) · [result](evidence/results/{n}.json)"])
        L = [f"# Lear RAG agent — session recordings index",
             "",
             f"Run `{self.run_id}` · recorded 2026-11-06 (UTC) on branch `{BRANCH}` · plan registered {self.plan['registeredAt']} (before execution) · "
             f"generated {self.now}.",
             "",
             "> **No answer in these recordings comes from an LLM.** Ollama is BLOCKED in this environment (not installed; model hosts unreachable), "
             "so every answer is produced by the agent's labelled offline baseline: hashing embeddings plus extractive synthesis, which selects and cites "
             "sentences from the indexed Lear documentation. Each recording first plays at its real recorded speed, then as a paced replay of the same "
             "recording that pauses for reading (the on-screen banner says which).",
             "",
             f"Outcomes: **{c['PASS']} PASS · {c['FAIL']} FAIL · {c['PARTIAL']} PARTIAL · {c['BLOCKED']} BLOCKED** of 9 scenarios, judged by checks registered "
             "before execution. Scenario 08 has a preserved aborted attempt (recording-harness bug), shown in the complete video.",
             "",
             table(["Scenario", "Recording", "Status", "Session ID", "Duration", "Main capability", "Evidence"], rows),
             "",
             f"Complete video: [complete_agent_session.mp4](complete_agent_session.mp4) ({fmt_s(self.man['totalSeconds'])}; "
             f"{len(self.man['segments'])} segments, of which {self.man['editorialSeconds']:.0f} s are labelled editorial chapter cards). "
             "Replay the terminal recordings: [terminal/README.md](terminal/README.md).",
             ""]
        for n, s in self.sc.items():
            r = self.res[n]
            L += [f"## {n} · {s['title']} — {r['status']}", "",
                  f"**What it tests.** {s['objective']}", "",
                  f"**Input selection (pre-registered).** {s['selection']}", "",
                  f"**What happened.** {NARRATIVE[n]}", ""]
            steps = []
            meta_rows = {}
            for row in (PKG / "traces" / n / "steps.jsonl").read_text().splitlines():
                rr = json.loads(row)
                meta_rows[rr["stepId"]] = rr
            for st in r["steps"]:
                q = (st.get("question") or "")
                mr = meta_rows.get(st["stepId"], {})
                steps.append([st["stepId"], mr.get("kind", ""), st.get("exit"), st.get("ms"),
                              (q[:90] + ("…" if len(q) > 90 else "")) if q else (mr.get("label") or ""),
                              st.get("questionSource") or "", st.get("correlationId") or ""])
            L += ["**Steps executed** (exit codes and wall times as observed by the harness):", "",
                  table(["step", "kind", "exit", "ms", "input / label", "input source", "engine trace id"], steps), ""]
            ans = []
            for sid, a in r["answers"].items():
                full = self.answers_full.get((n, sid), {})
                t = full.get("timings") or {}
                itl = t.get("itlMs") or {}
                cites = ", ".join(f"{x['document'].split('/')[-1]}:{x['lines']}" for x in a["citations"]) or "—"
                ans.append([sid, a["status"], a.get("abstainReason") or "", ", ".join(a.get("warnings") or []) or "", cites,
                            t.get("totalMs"), nz(t.get("ttftMs")), nz(itl.get("p50")), nz(itl.get("p99"))])
            if ans:
                L += ["**Answers** (from the answer JSON printed by the CLI; timings are pipeline-only, extractive baseline):", "",
                      table(["step", "status", "abstain reason", "warnings", "citations (document:lines)", "total ms", "ttft ms", "itl p50 ms", "itl p99 ms"], ans), ""]
            chk = [[c["id"], f"**{c['outcome']}**", c["onFail"], c["description"], json.dumps(c["observed"], default=str)[:220]] for c in r["checks"]]
            L += ["**Pre-registered checks:**", "", table(["check", "outcome", "on fail", "description", "observed (truncated; full in result JSON)"], chk), ""]
            cap = r.get("cap") or {}
            L += [f"**Status rule.** {('Capped at ' + cap['status'] + ': ' + cap['reason']) if cap.get('status') else 'No cap: PASS was achievable in this environment.'}", "",
                  f"**Providers.** {meta['sessions'][int(n) - 1]['model']} · retrieval: hashing embeddings + FTS5 BM25, RRF fusion, heuristic rerank, MMR.", "",
                  f"**Evidence.** " + " · ".join(f"[{e}]({e})" for e in self.evidence(n)[:14]) + (" · …" if len(self.evidence(n)) > 14 else ""), ""]
            if n == "07":
                L += [f"**Compaction records** (from `lear sessions inspect`, traces/07/s7.out): {len(self.compactions)} compactions; " +
                      "; ".join(f"#{x['compaction_id']} {x['tokens_before']}→{x['tokens_after']} tokens in {x['latency_ms']} ms" for x in self.compactions[:6]) + ".", ""]
            if n == "09":
                e, b = self.eval, self.bench
                L += [f"**Evaluation (this run, 42 cases, NOT QUALIFYING baseline):** lenient correct {e.get('correct')} · strict {e.get('correctStrict')} · "
                      f"hit@k {e['retrieval']['hitAtK']} · MRR {e['retrieval']['mrr']} · status counts {e['statusCounts']}. Identical to committed round-3 "
                      "for status, correct and answer text in 42/42 cases. The recording also shows `identical correctStrict 0/42`: round-3's per-case "
                      "records predate the strict scorer and have no correctStrict field, so that line compares a value with null by construction; it is "
                      "not a regression and was not part of the pre-registered criterion.", "",
                      f"**Benchmark (PIPELINE-ONLY, 5 users × 10 requests):** ok {b['ok']} · rate limited {b['rateLimited']} · errors {b['errors']} · "
                      f"{b['throughputRps']} req/s · total ms p50 {b['totalMs']['p50']} / p85 {b['totalMs']['p85']} / p99 {b['totalMs']['p99']} · "
                      f"TTFT ms p50 {b['ttftMs']['p50']} / p99 {b['ttftMs']['p99']} · ITL ms p50 {b['itlMs']['p50']} (n={b['itlMs']['count']}) · "
                      "tok/s n/a (no model tokens).", ""]
        return "\n".join(L) + "\n"

    # ------------------------------------------------------------ recording_verification.md
    def verification_md(self) -> str:
        v, s = self.ver, self.ver["summary"]
        sec = v["security"]
        L = ["# Recording verification", "",
             f"Generated {self.now} from `evidence/verification.json` (verifier run {v['generatedAtUtc']}"
             + (f", security re-scan {v['securityScannedAtUtc']}" if v.get("securityScannedAtUtc") else "") + ") by "
             "`scripts/session_recordings/verify_package.py`. Tools: " + v["ffmpeg"] + " (ffprobe is not installed, so stream data comes from `ffmpeg -i`), "
             "asciinema 2.4.0, Pillow.", "",
             "## 1. File existence", "",
             table(["file", "bytes", "modified (UTC)", "sha256"],
                   [[x["path"], x["bytes"], x["modifiedUtc"], f"`{x['sha256']}`"] for x in v["videos"] + v["casts"]]), "",
             "Documents and traces are covered by `checksums.sha256` (written last; verify with `sha256sum -c checksums.sha256` inside the package folder).", "",
             "## 2. Video integrity", "",
             table(["file", "container", "codec", "resolution", "fps", "duration", "decoded frames", "frames = duration × fps", "decode errors", "black spans", "audio", "sample frames non-blank", "verdict"],
                   [[x["path"], x.get("container"), f"{x.get('codec')} {x.get('profile') or ''}".strip(), f"{x.get('width')}x{x.get('height')}", x.get("fps"),
                     fmt_s(x.get("durationS")), x.get("decodedFrames"), "yes" if x.get("framesMatchDuration") else "NO", len(x.get("decodeErrors") or []),
                     len(x.get("blackSpans") or []), f"{x.get('audioStreams')} (none by design)" if not x.get("audioStreams") else x.get("audioStreams"),
                     f"{sum(1 for f in x['sampleFrames'] if f.get('nonBlank'))}/{len(x['sampleFrames'])}", "**OK**" if x["ok"] else "**FAILED**"]
                    for x in v["videos"]]), "",
             "Method: full decode with `ffmpeg -v error -i FILE -map 0:v -f null -` (any error output fails the file); frame count from `-progress`; "
             "black-frame scan with `blackdetect=d=0.5:pic_th=0.98:pix_th=0.05`; sample frames decoded from each MP4 (end of the real-time part and middle "
             "of the paced replay) into `evidence/frames/`, with luminance standard deviation > 8 required. No audio track exists: terminal recordings "
             "carry no sound and no voiceover was added.", "",
             "## 3. Terminal recording integrity", "",
             table(["cast", "valid v2", "size", "events", "monotonic", "recorded duration", "idle limit", "asciinema cat"],
                   [[x["path"], x.get("valid"), f"{x.get('width')}x{x.get('height')}", x.get("events"), x.get("monotonic"), f"{x.get('durationS')} s",
                     x.get("idleTimeLimit") or "none", f"exit {(x.get('asciinemaCat') or {}).get('exit')}"] for x in v["casts"]]), "",
             "Consistency with the per-step logs and harness timestamps:", "",
             table(["scenario", "agent-command output: every line in cast", "contiguous byte-identical block", "helper steps (summary on screen)", "out sha256 match",
                    "session IDs in cast", "engine trace IDs match", "harness elapsed", "cast duration", "cast start → harness start", "steps.jsonl rows", "events.jsonl rows"],
                   [[x["scenario"], f"{'yes' if x['consistency']['allAgentOutputLinesInCast'] else 'NO'} ({x['consistency']['agentSteps']} steps)",
                     x["consistency"]["agentOutputsContiguous"], x["consistency"]["helperSteps"], x["consistency"]["allOutSha256Match"], all(x["consistency"]["sessionIdsInCast"].values()) if x["consistency"]["sessionIdsInCast"] else "n/a",
                     all(t["idMatches"] for t in x["consistency"]["engineTraces"]) if x["consistency"]["engineTraces"] else "n/a",
                     f"{x['consistency']['harnessElapsedS']} s", f"{x['consistency']['castDurationS']} s", f"{x['consistency']['castStartVsHarnessStartS']} s",
                     x["consistency"]["stepsJsonlRows"], x["consistency"]["eventsJsonlRows"]] for x in v["scenarios"]]), "",
             f"Every line that each agent CLI command printed (saved as `traces/NN/<step>.out`, sha256 in `steps.jsonl`) appears in that scenario's "
             f"asciicast, and {self.contiguous()} outputs appear as one contiguous byte-identical block (carriage returns removed: the outer "
             "pseudo-terminal adds one). The exception is S08 step r1 (`lear chat`): the harness prints a labelled ▌USER line when it writes the burst "
             "into the chat's stdin, and that line lands between chunks of the chat's output. Two steps are computations of the harness, not agent "
             "commands — S05 `sc` (read-only corpus injection scan) and S09 `x1` (comparison with committed round-3): the terminal shows their labelled "
             "summary and the full JSON is in the `.out` file. The cast header timestamp has 1-second resolution, so the start offset is accurate to "
             "±1 s. The S08 attempt-1 cast is checked structurally only; it has no result file.", "",
             "## 4. Session consistency", "",
             "- Session IDs are the agent's own (`--session rec-sNN-<stamp>`), printed by the agent in each answer JSON and found in the casts; the run ID "
             f"`{self.run_id}` is the recording's own identifier and is never presented as an agent session ID.",
             "- Engine trace IDs (`correlationId` in each answer) match the trace rows exported read-only from the engine's SQLite `queries.trace_json`.",
             "- Statuses in `session_metadata.json`, `session_index.md`, the chapter cards and `evidence/results/NN.json` come from the same result files.", "",
             "## 5. Security scan", "",
             f"Scope: {sec['scope']}. Patterns: {', '.join(sec['patterns'])}.", "",
             f"- Findings: **{len(sec['findings'])}**" + (": " + "; ".join(f"{f['file']}:{f['line']} {f['pattern']} {f['masked']}" for f in sec["findings"][:12]) if sec["findings"] else ""),
             f"- IPv4 addresses seen: {sec['ipv4Seen']} (allowed: {sec['ipv4Allowed']}).",
             f"- System prompt: {sec['systemPromptLinesChecked']} lines of LEAR_SYSTEM_PROMPT and the context/memory templates checked; disclosures: **{len(sec['systemPromptDisclosures'])}**. "
             "Prompts are referenced only by version (lear-assistant-v1) and token counts. The S05 leak check (c3) lists four short marker phrases "
             "from that prompt as its search terms; the prompt is versioned in this public repository (ragformation/lear/prompts.py) and no prompt "
             "line appears in any answer or file of the package.",
             "- No environment dump: the casts' headers record only SHELL and TERM; child commands ran with RAG_*/OLLAMA_* variables removed except those a step sets explicitly (the injected test URL in S08).",
             "- The Lear documentation corpus is generated from the public pin and contains no credentials; answers quote it with citations.", "",
             "## 6. Final counts", "",
             table(["measure", "value"], self.final_counts()), ""]
        return "\n".join(L) + "\n"

    def contiguous(self) -> str:
        agent = [st for x in self.ver["scenarios"] for st in x["consistency"]["steps"] if not st.get("helper")]
        return f"{sum(st['outputInCast'] for st in agent)}/{len(agent)}"

    def final_counts(self) -> List[List[Any]]:
        c, s = self.counts(), self.ver["summary"]
        media = sum(x["bytes"] for x in self.ver["videos"]) + sum(x["bytes"] for x in self.ver["casts"])
        total = sum(p.stat().st_size for p in PKG.rglob("*") if p.is_file())
        return [["scenarios attempted", 9], ["scenarios recorded", "9 (plus 1 preserved aborted attempt of S08)"],
                ["PASS", c["PASS"]], ["FAIL", c["FAIL"]], ["PARTIAL", c["PARTIAL"]], ["BLOCKED (whole scenario)", c["BLOCKED"]],
                ["NOT_IMPLEMENTED (whole scenario)", c["NOT_IMPLEMENTED"]],
                ["capabilities BLOCKED inside scenarios", "LLM generation, semantic embeddings, LLM judge (Ollama unavailable)"],
                ["capabilities NOT_IMPLEMENTED", "tool calling (the agent has no tool layer)"],
                ["valid MP4 files", f"{s['videosOk']}/{s['videosChecked']}"], ["valid terminal recordings", f"{s['castsValid']}/{s['castsChecked']}"],
                ["native screen recordings", "0 (no display server; genuine terminal recordings used, as the brief allows)"],
                ["scenarios whose agent-command output is fully present in the cast", f"{s['scenarioOutputsConsistent']}/9"],
                ["agent-command outputs present as one contiguous block", self.contiguous()],
                ["security findings", s["securityFindings"]], ["system-prompt disclosures", s["systemPromptDisclosures"]],
                ["media bytes (MP4 + casts)", f"{media:,}"], ["package bytes when this file was written", f"{total:,} (final total printed by the checksums step)"]]

    # ------------------------------------------------------------ MANAGER_HANDOFF.md
    def handoff(self, meta: Dict[str, Any]) -> str:
        c = self.counts()
        rows = [[n, s["title"], f"**{self.res[n]['status']}**", self.passed(n), CAPABILITY[n], f"[{s['file']}.mp4]({s['file']}.mp4)"] for n, s in self.sc.items()]
        perf = []
        for n in ("01", "02", "03", "05", "06", "07"):
            m = self.metrics(n)
            est = m.get("agent_reported_estimates") or {}
            perf.append([n, PRIMARY[n], m["total_latency_ms"], m["ttft_ms"], m["itl_median_ms"], est.get("itl_p99_ms"), "N/A (not emitted)", "N/A (estimates only)"])
        b = self.bench
        L = ["# Manager handoff — Lear RAG agent session recordings", "",
             f"Run `{self.run_id}` · 2026-11-06 · branch `{BRANCH}` · generated {self.now}", "",
             "## 1. Executive summary", "",
             f"The existing Lear agent was run for real in all nine scenarios and every run was recorded in the terminal with asciinema "
             f"(real timing), rendered to 1080p MP4 and verified. Judged by checks registered before execution: **{c['PASS']} PASS, {c['FAIL']} FAIL, "
             f"{c['PARTIAL']} PARTIAL, {c['BLOCKED']} BLOCKED.** The decisive limitation: **Ollama is BLOCKED here** (not installed; model hosts unreachable), "
             "so no answer comes from an LLM. All answers come from the agent's labelled offline baseline (hashing embeddings and extractive synthesis). "
             "Overall verdict: **PARTIALLY COMPLETE**.", "",
             "## 2. What the recordings demonstrate (evidence-backed)", "",
             "- The real retrieval pipeline end to end, with the engine's own persisted stage trace: query embedding, semantic + lexical retrieval, RRF fusion, "
             "heuristic rerank, MMR, guardrail scan, context assembly, generation, citation validation (S01, S03, S05-S07).",
             "- Citations with document and line ranges, validated by the agent (every grounded answer).",
             "- Correct abstention on unanswerable questions, by the scope guard and by the support threshold (S04: 3/3).",
             "- Injection attempts produce no secrets, no prompt text and no tool execution; the output guard flags a meta-disclosure request (S05).",
             f"- Real session compaction with persisted summaries ({len(self.compactions)} compactions in S07) and use of a long-term memory note (S02).",
             "- Classified dependency failure without silent fallback, refusal of a non-loopback model URL, rate limiting with retry-after, and recovery (S08: 6/6).",
             "- Reproducible evaluation: 42/42 cases identical to the committed round-3 run; a dummy-user benchmark (S09).", "",
             "Not demonstrated: any LLM output, semantic embeddings, an LLM judge, model latency or throughput, tool calling.", "",
             "## 3. Technical environment", "",
             table(["component", "value"], [
                 ["OS / hardware", f"{meta['environment']['operating_system']} · {meta['environment']['architecture']}"],
                 ["runtime / app", f"{meta['environment']['runtime']} · {meta['environment']['application_version']}"],
                 ["LLM", "configured ollama:qwen2.5:1.5b — **UNAVAILABLE (BLOCKED)**; used: extractive-synthesis-v1 (**not an LLM**)"],
                 ["embeddings", "configured ollama:nomic-embed-text — **UNAVAILABLE (BLOCKED)**; used: hashing-lexical-v1 (baseline, lexical)"],
                 ["vector store", meta["providers"]["vector_store"]], ["reranker", meta["providers"]["reranker"]], ["memory", meta["providers"]["memory"]],
                 ["recording", meta["recording_tool"]],
                 ["test instrumentation", "scripts/session_recordings/ (harness, renderer, verifier); no application file was changed"],
                 ["injected faults", "S08 only: an unreachable non-loopback Ollama URL (192.0.2.10, RFC 5737 TEST-NET-1) set for one command"],
                 ["mocked components", "none"]]), "",
             "## 4. Scenario results", "",
             table(["#", "scenario", "status", "checks", "capability", "recording"], rows), "",
             "## 5. Performance", "",
             "All timings below were measured by the agent itself (monotonic clock) and copied verbatim. They are **PIPELINE-ONLY**: generation is "
             "extractive, so there is no model latency, no model TTFT and no token throughput in this package.", "",
             table(["scenario", "step", "total ms", "TTFT ms (first extractive chunk)", "ITL p50 ms", "ITL p99 ms", "ITL p95", "tokens/s"], perf), "",
             f"Benchmark (S09, 5 users × 10 requests, pipeline-only): {b['ok']}/{b['requests']} ok, {b['throughputRps']} req/s, total ms p50 {b['totalMs']['p50']} / "
             f"p99 {b['totalMs']['p99']}, TTFT ms p50 {b['ttftMs']['p50']}. Model TTFT, ITL p95 and tokens/s: **N/A** — no model ran (Ollama BLOCKED); "
             "the agent reports ITL p50/p85/p99, not p95; its token counts are character-based estimates.", "",
             "## 6. What worked", "",
             "- Every scenario executed against the real agent and was recorded; 10 valid terminal recordings and 11 valid MP4 files (see verification).",
             "- S04 (abstention) and S08 (error handling and recovery) passed every check.",
             "- Evaluation reproduction was exact (42/42); traces, sessions, compaction and memory records were all persisted and exported.", "",
             "## 7. What failed", "",
             "- S01, S03: grounded, cited answers that miss expected key facts (the extractive baseline picks related but not decisive sentences).",
             "- S02, S07: follow-up questions that depend on earlier turns missed the fact (history reaches the baseline only via query rewriting against the previous turn).",
             "- S06: the contradiction is surfaced in the answer text, but the engine's conflict detector did not flag it.",
             "- S08 attempt 1 aborted on a bug in the recording harness (not the agent); fixed, re-recorded, and the failed attempt is kept.", "",
             "## 8. Unverified", "",
             "- Any behaviour with a real LLM or semantic embedder (qwen2.5:1.5b, nomic-embed-text): not run, so answer quality, model latency, "
             "token throughput and LLM-judge scores are unknown.",
             "- Model sizes: preflight O04 lists the configured models with nominal sizes taken from the settings defaults "
             "(nomic-embed-text ~137M, qwen2.5:1.5b ~1.5B, llama3.2:1b ~1.2B); no Ollama metadata could be read to confirm them.", "",
             "## 9. Recording locations", "",
             "All files are in `artifacts/agent_session_recordings/`: nine scenario MP4s, `complete_agent_session.mp4`, `terminal/` (asciicasts), "
             "`traces/` (per-step outputs, answers, engine traces, events), `evidence/` (preflight, results, render reports, verification, frames, attempts), "
             "`session_index.md`, `session_metadata.json`, `recording_verification.md`, `PREFLIGHT_REPORT.md`, `checksums.sha256`.", "",
             "## 10. Replay instructions", "",
             "- Videos: open any MP4 (1920x1080 H.264, no audio). Each starts with the real-time playback, then the paced replay.",
             "- Terminal: `pip install asciinema==2.4.0` then `asciinema play terminal/01_successful_rag_session.cast` (add `-s 0.25` for quarter speed). "
             "Details: [terminal/README.md](terminal/README.md).",
             "- Re-run: [scripts/session_recordings/README.md](../../scripts/session_recordings/README.md).", "",
             "## 11. Integrity statement", "",
             "Every operation shown is from a real execution of the existing agent at the recorded commit. No event, token stream, score, tool call, "
             "answer or telemetry value was manufactured. Extractive output is labelled as such and never called LLM output. The only fault injected is the "
             "S08 test URL, labelled in the recording. Scenario inputs were registered before execution; failures are reported as failures and nothing was "
             "cut. Checksums for every file are in `checksums.sha256`.", ""]
        return "\n".join(L) + "\n"

    # ------------------------------------------------------------ READMEs
    def terminal_readme(self) -> str:
        rows = [[x["path"].replace("terminal/", ""), f"{x.get('durationS')} s", x.get("events"), x["bytes"], f"`{x['sha256'][:16]}…`"] for x in self.ver["casts"] if x["path"].startswith("terminal/")]
        return "\n".join([
            "# Terminal recordings (asciicast v2)", "",
            "Genuine recordings made with asciinema 2.4.0 at real timing (no idle-time limit). Each file holds one scenario: the recording harness "
            "prints labelled lines (▌REC, ▌STEP, ▌USER, ▌VIEW, ▌TRACE, ▌CHECK, ▌RESULT) and the exact `$ command` it runs, and everything else is the "
            "unmodified output of that command, which ran in its own pseudo-terminal. Input events are not recorded (the harness types nothing).", "",
            table(["file", "recorded duration", "events", "bytes", "sha256 (full in checksums.sha256)"], rows), "",
            "## Replay", "",
            "```bash",
            "python3 -m pip install asciinema==2.4.0",
            "asciinema play terminal/01_successful_rag_session.cast            # real timing",
            "asciinema play -s 0.25 terminal/01_successful_rag_session.cast    # quarter speed for reading",
            "asciinema cat terminal/01_successful_rag_session.cast | less -R   # whole output, scrollable",
            "```", "",
            "Recordings were made at 158×40; use a terminal at least that size. Any asciicast v2 player works (for example asciinema-player in a browser). "
            "The S08 attempt-1 recording (aborted by a harness bug) is kept at `../evidence/attempts/attempt1_08_error_recovery_session.cast`.", ""])

    def traces_readme(self) -> str:
        return "\n".join([
            "# Traces", "",
            "One folder per scenario (`01/` … `09/`). Everything is produced by real execution; nothing here is hand-edited.", "",
            table(["file", "content"], [
                ["`<step>.out`", "raw bytes printed by the CLI for that step (also byte-identical inside the asciicast); sha256 in steps.jsonl"],
                ["`<step>.answer.json`", "the answer JSON the CLI printed (`lear ask --json`), parsed and saved unchanged"],
                ["`engine_trace_<id>.json`", "read-only export of the engine's own persisted trace row (`queries.trace_json` in SQLite): stages, durations, counts; no document text"],
                ["`steps.jsonl`", "per step: argv, exit code, UTC start/end, wall ms, output size and sha256, question and its source"],
                ["`events.jsonl`", "normalised trace events (event_name, timestamps, session_id, trace_id, stage, status, duration_ms, component, model, provider, …); "
                                   "fields the agent does not expose are null"],
                ["`<step>.scan.json`, `<step>.compare.json`", "outputs of read-only helper steps (S05 corpus injection scan, S09 round-3 comparison)"],
                ["`09/eval_run.json`, `09/eval_summary.md`, `09/bench.json`", "the evaluation run and benchmark written by the agent's own `lear eval run` and `lear bench`"]]), "",
            "Sanitisation: child commands ran with RAG_*/OLLAMA_* variables removed (defaults apply) except those a step sets explicitly; no environment "
            "values, credentials or prompts are written. See `../recording_verification.md` §5 for the scan.", ""])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    a = ap.parse_args()
    pk = Package(a.run_id)
    meta = pk.metadata()
    (PKG / "session_metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    (PKG / "session_index.md").write_text(pk.session_index(meta))
    (PKG / "recording_verification.md").write_text(pk.verification_md())
    (PKG / "MANAGER_HANDOFF.md").write_text(pk.handoff(meta))
    (PKG / "terminal" / "README.md").write_text(pk.terminal_readme())
    (PKG / "traces" / "README.md").write_text(pk.traces_readme())
    print(json.dumps({"written": ["session_metadata.json", "session_index.md", "recording_verification.md", "MANAGER_HANDOFF.md",
                                  "terminal/README.md", "traces/README.md"], "counts": pk.counts()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
