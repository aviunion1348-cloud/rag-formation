# Data recovery validation (Stage I, part 2) — 14/14

Four questions, each answered by doing it: `scripts/recovery_check.py`.
Raw output: [`evidence/recovery_report.json`](evidence/recovery_report.json).
Every step runs in a temporary directory; `data/` and any user database are never
touched.

```
recovery: 14/14 checks passed
```

## 1. Online backup (engine open, WAL active)

SQLite's own backup API copies the live database while the engine holds it open:

| Check | Observed |
| --- | --- |
| backup file produced | 792 KB, `PRAGMA`-consistent copy |

## 2. Restore into a different directory

| Check | Observed |
| --- | --- |
| restored index reports healthy | `{"ok": true, …, "sources": 14, "chunks": 66, "vectors": 66}` |
| restored index answers identically | 5/5 answers byte-identical |
| chunk ids preserved so citations still resolve | citation chunk ids unchanged |
| same chunk count | 66 (baseline 66) |

This is the property that matters for citations: a restored index does not
re-number chunks, so a citation captured before the backup still points at the
same evidence afterwards.

## 3. Rebuild and rollback

| Check | Observed |
| --- | --- |
| rebuild keeps the chunk set stable | 66 → 66 chunks, 4.2 ms |
| rollback restores the previous index generation | 66 → 66, fingerprint restored |
| answers survive rebuild + rollback | 5/5 identical |

## 4. Interrupted ingest (SIGKILL, not a clean shutdown)

A 400-document ingest was killed with `SIGKILL` **mid-flight** — the process was
genuinely terminated (`returncode=-9`), not allowed to finish:

| Check | Observed |
| --- | --- |
| ingest killed mid-flight | `returncode=-9` |
| interrupted database passes `PRAGMA integrity_check` | `ok` (275 of 400 sources had been written) |
| re-ingest after interruption succeeds | exit 0 |
| recovered index answers correctly | 400 chunks, "availability target of 99.17%" found and cited |
| no duplicated sources after retry | 400 sources, 400 unique ids |

That is the useful combination: a hard kill leaves a *consistent* partial index,
and the retry converges to the complete one without duplicates because the
ingest is keyed on content hash, not on "did I see this file before".

## 5. Limits of this evidence

* Single-process, single-writer SQLite. Concurrent writers, network filesystems
  and disk-full conditions are **not** tested.
* No long-running soak: the interrupted ingest was seconds long, not hours.
* Backup was a local file copy through SQLite's API; no remote/object-storage
  backup path is exercised.
* The rollback path restores the *previous generation of the same corpus*, not an
  arbitrary historical schema version; there are no migrations to roll back yet.
