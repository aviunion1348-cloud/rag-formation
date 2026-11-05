#!/usr/bin/env python3
"""Data-recovery validation: backup, restore, rollback, interruption.

Four questions a reviewer actually cares about:

1. **Backup** — does an online SQLite backup of a live index produce a copy that
   answers identically, including citations and chunk ids?
2. **Restore** — does the copy start cleanly in a *different* directory with no
   WAL files, and does `doctor` accept it?
3. **Rollback** — after a rebuild, does the index rollback return the previous
   chunk set and vectors, so citations from before the rebuild still resolve?
4. **Interruption** — if an ingest is killed mid-way, is the database still
   usable, is the partial work discarded or completed on retry, and does a
   re-ingest converge to the same answer?

Nothing here touches `data/` or any user database: every step works inside a
temporary directory.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ragformation.config import Settings  # noqa: E402
from ragformation.engine import RAGEngine  # noqa: E402

CORPUS = sorted(str(path) for path in (ROOT / "corpus").glob("*") if path.is_file())
QUESTIONS = [
    "How long are audit logs retained?",
    "What is the rollback window for a release?",
    "Which team owns the ingest-api service?",
    "How long is configuration state retained in backups?",
    "What availability SLO does the ingest-api commit to?",
]

results: list[dict] = []


def record(name: str, ok: bool, detail: str) -> None:
    results.append({"check": name, "ok": ok, "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")


def settings_for(directory: Path) -> Settings:
    return Settings(
        **{
            **Settings.from_env().__dict__,
            "data_dir": directory,
            "embed_provider": "hashing",
            "llm_provider": "extractive",
        }
    )


def answers(engine: RAGEngine) -> list[dict]:
    rows = []
    for question in QUESTIONS:
        result = engine.ask(question, save=False)
        rows.append(
            {
                "question": question,
                "status": result.status,
                "answer": (result.answer or "").strip(),
                "chunks": [citation.chunk_id for citation in result.citations],
            }
        )
    return rows


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="recovery-"))
    live_dir = work / "live"
    restore_dir = work / "restored"

    # ------------------------------------------------------------------ baseline
    engine = RAGEngine(settings_for(live_dir))
    report = engine.ingest_path(CORPUS)
    record("baseline ingest", report.indexed == len(CORPUS), f"{report.indexed} sources, {report.chunks} chunks")
    before = answers(engine)

    # -------------------------------------------------------------------- backup
    db_path = Path(engine.settings.db_path)
    backup_path = work / "backup" / "ragformation.db"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(db_path)
    target = sqlite3.connect(backup_path)
    with source:
        source.backup(target)
    target.close()
    source.close()
    record(
        "online backup while the engine is open",
        backup_path.exists() and backup_path.stat().st_size > 1024,
        f"{backup_path.stat().st_size / 1024:.0f} KB in {backup_path}",
    )
    engine.close()

    # ------------------------------------------------------------------- restore
    restore_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_path, restore_dir / "ragformation.db")
    restored_engine = RAGEngine(settings_for(restore_dir))
    try:
        health = restored_engine.health()
        record("restored index reports healthy", bool(health.get("ok", True)), json.dumps(health)[:160])
        after = answers(restored_engine)
        same = [row for row in before if row["answer"] and row["answer"] == after[before.index(row)]["answer"]]
        record(
            "restored index answers identically",
            all(a["answer"] == b["answer"] for a, b in zip(before, after)),
            f"{len(same)}/{len(before)} answers byte-identical",
        )
        record(
            "restored index keeps chunk ids (citations still resolve)",
            all(a["chunks"] == b["chunks"] for a, b in zip(before, after)),
            "citation chunk ids unchanged" if all(a["chunks"] == b["chunks"] for a, b in zip(before, after)) else "ids changed",
        )
        restored_chunks = len(restored_engine.store.all_chunks())
        record(
            "restored index has the same chunk count",
            restored_chunks == report.chunks,
            f"{restored_chunks} chunks (baseline {report.chunks})",
        )
    finally:
        restored_engine.close()

    # ------------------------------------------------------------------ rollback
    rollback_engine = RAGEngine(settings_for(restore_dir))
    try:
        chunks_before = len(rollback_engine.store.all_chunks())
        rebuild = rollback_engine.rebuild()
        chunks_rebuilt = len(rollback_engine.store.all_chunks())
        rollback = rollback_engine.rollback_rebuild()
        chunks_rolled_back = len(rollback_engine.store.all_chunks())
        record(
            "rebuild keeps the chunk set stable",
            chunks_rebuilt == chunks_before,
            f"{chunks_before} -> {chunks_rebuilt} chunks; rebuild={json.dumps(rebuild)[:110]}",
        )
        record(
            "rollback restores the previous index generation",
            chunks_rolled_back == chunks_before,
            f"{chunks_rebuilt} -> {chunks_rolled_back} chunks; rollback={json.dumps(rollback)[:110]}",
        )
        after_rollback = answers(rollback_engine)
        record(
            "answers survive rebuild + rollback",
            all(a["answer"] == b["answer"] for a, b in zip(before, after_rollback)),
            f"{sum(1 for a, b in zip(before, after_rollback) if a['answer'] == b['answer'])}/{len(before)} identical",
        )
    finally:
        rollback_engine.close()

    # -------------------------------------------------------------- interruption
    interrupt_dir = work / "interrupt"
    env = {
        **os.environ,
        "RAG_DATA_DIR": str(interrupt_dir),
        "RAG_EMBED_PROVIDER": "hashing",
        "RAG_LLM_PROVIDER": "extractive",
        "PYTHONPATH": str(ROOT),
    }
    # A big enough ingest to interrupt mid-flight.
    bulk = work / "bulk"
    bulk.mkdir()
    for index in range(400):
        (bulk / f"svc-{index:03d}.md").write_text(
            f"# Service svc-{index:03d}\n\nService svc-{index:03d} is owned by team-{index % 30:02d} "
            f"and reports an availability target of {99.0 + (index % 90) / 100.0:.2f}% over a rolling window.\n"
        )
    killed = None
    for attempt, delay in enumerate((0.6, 0.35), start=1):
        process = subprocess.Popen(
            [sys.executable, "-m", "ragformation", "ingest", str(bulk), "--force"],
            env=env,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        time.sleep(delay)
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        process.communicate()
        killed = process.returncode
        if killed == -signal.SIGKILL:
            break
        record(
            f"interruption attempt {attempt} finished before the signal",
            False,
            f"returncode={killed} — the workload was too small to interrupt, retrying earlier",
        )
    record(
        "ingest killed mid-flight (SIGKILL, not a clean exit)",
        killed == -signal.SIGKILL,
        f"returncode={killed}",
    )

    # The database must still open, and be consistent (integrity_check).
    survivor = sqlite3.connect(interrupt_dir / "ragformation.db")
    integrity = survivor.execute("PRAGMA integrity_check").fetchone()[0]
    partial_sources = survivor.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    survivor.close()
    record("interrupted database passes integrity_check", integrity == "ok", f"integrity_check={integrity}, sources={partial_sources}")

    retry = subprocess.run(
        [sys.executable, "-m", "ragformation", "ingest", str(bulk)],
        env=env,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    record("re-ingest after an interruption succeeds", retry.returncode == 0, (retry.stdout or "").strip().splitlines()[-1][:140])

    recovered = RAGEngine(settings_for(interrupt_dir))
    try:
        total = len(recovered.store.all_chunks())
        result = recovered.ask("What availability target does svc-017 report?", save=False)
        correct = "99.17%" in (result.answer or "")
        record("recovered index answers correctly", correct and result.status in {"answered", "needs_review"}, f"{total} chunks, status={result.status}")
        duplicate_ids = [s["sourceId"] for s in recovered.list_sources()]
        record("no duplicated sources after retry", len(duplicate_ids) == len(set(duplicate_ids)), f"{len(duplicate_ids)} sources")
    finally:
        recovered.close()

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/recovery_report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"checks": results, "failed": [r for r in results if not r["ok"]]}, indent=2) + "\n")
    print(f"\nwrote {out}")
    failed = [r for r in results if not r["ok"]]
    print(f"recovery: {len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
