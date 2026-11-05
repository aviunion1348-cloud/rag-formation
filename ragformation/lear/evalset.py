"""The Lear golden set: schema, splits, checksums and source evidence.

``evals/lear/golden_lear_v1.json`` holds the cases. ``golden_lear_v1.checksums.json``
records a SHA-256 per case, per split and for the whole case list, plus the
hashes of the older ``evals/golden.json`` / ``evals/golden_v2.json`` files, so
any edit to an existing case (or to the legacy sets) is caught by
``lear eval validate``. Cases are only ever *added*; the checksum file is
regenerated explicitly with ``--write-checksums`` and the change is visible in
the diff.

Every answerable case cites evidence in the Lear repository as
``path`` + ``start``/``end`` lines + a short ``quote``; validation reads the file
at the pinned commit with ``git show <commit>:<path>`` and requires the quote to
appear inside that line range.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..errors import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = REPO_ROOT / "evals" / "lear" / "golden_lear_v1.json"
LEGACY_SETS = ("evals/golden.json", "evals/golden_v2.json")
SPLITS = ("calibration", "dev", "test", "holdout")
CATEGORIES = ("factual", "configuration", "workflow", "security", "multi_hop", "follow_up", "unanswerable", "conflict", "injection")
STATUSES = ("GROUNDED", "PARTIAL", "ABSTAINED", "CONFLICTED", "ERROR")
MIN_CASES, MAX_CASES = 50, 100
TARGET_MIN, TARGET_MAX = 70, 80
_ID = re.compile(r"^lear-\d{3}$")


def checksum_path(dataset: Path) -> Path:
    return dataset.with_name(dataset.stem + ".checksums.json")


def load_dataset(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"golden set not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"golden set is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
        raise ValidationError("golden set must be an object with a 'cases' list")
    return data


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_checksums(data: Dict[str, Any], repo_root: Path = REPO_ROOT) -> Dict[str, Any]:
    cases = data["cases"]
    per_case = {c["id"]: sha256(canonical(c)) for c in cases}
    splits: Dict[str, List[str]] = {s: sorted(c["id"] for c in cases if c.get("split") == s) for s in SPLITS}
    split_hashes = {s: sha256(canonical([[i, per_case[i]] for i in ids])) for s, ids in splits.items()}
    legacy = {}
    for rel in LEGACY_SETS:
        path = repo_root / rel
        legacy[rel] = sha256(path.read_bytes()) if path.exists() else None
    return {
        "format": "lear-golden-checksums-v1",
        "dataset": data.get("name"),
        "version": data.get("version"),
        "cases": len(cases),
        "datasetSha256": sha256(canonical(cases)),
        "caseSha256": per_case,
        "splits": splits,
        "splitSha256": split_hashes,
        "legacySha256": legacy,
    }


def write_checksums(dataset: Path) -> Path:
    data = load_dataset(dataset)
    target = checksum_path(dataset)
    target.write_text(json.dumps(compute_checksums(data), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def _norm(text: str) -> str:
    return " ".join(text.split())


class _Source:
    def __init__(self, source: Optional[Path], commit: Optional[str]) -> None:
        self.source = source
        self.commit = commit
        self._cache: Dict[str, Optional[List[str]]] = {}

    def lines(self, rel: str) -> Optional[List[str]]:
        if rel not in self._cache:
            text = None
            if self.source is not None and self.commit:
                proc = subprocess.run(
                    ["git", "-C", str(self.source), "show", f"{self.commit}:{rel}"],
                    capture_output=True,
                    timeout=30,
                )
                if proc.returncode == 0:
                    text = proc.stdout.decode("utf-8", "replace").splitlines()
            self._cache[rel] = text
        return self._cache[rel]


def _check(name: str, status: str, summary: str, examples: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"name": name, "status": status, "summary": summary, "examples": (examples or [])[:15]}


def validate(dataset: Path, *, source: Optional[Path] = None, docs_dir: Optional[Path] = None) -> Dict[str, Any]:
    dataset = Path(dataset)
    data = load_dataset(dataset)
    cases = data["cases"]
    checks: List[Dict[str, Any]] = []
    docs_root = Path(docs_dir) if docs_dir else REPO_ROOT

    # schema ------------------------------------------------------------------
    problems: List[str] = []
    ids = [c.get("id") for c in cases]
    for dup, n in Counter(ids).items():
        if n > 1:
            problems.append(f"duplicate id {dup}")
    for case in cases:
        cid = case.get("id", "?")
        if not isinstance(cid, str) or not _ID.match(cid):
            problems.append(f"{cid}: id must look like lear-001")
        if case.get("split") not in SPLITS:
            problems.append(f"{cid}: split must be one of {SPLITS}")
        if case.get("category") not in CATEGORIES:
            problems.append(f"{cid}: unknown category {case.get('category')!r}")
        if case.get("difficulty") not in ("easy", "medium", "hard"):
            problems.append(f"{cid}: difficulty must be easy/medium/hard")
        if not isinstance(case.get("question"), str) or len(case["question"].strip()) < 8:
            problems.append(f"{cid}: question missing or too short")
        if not isinstance(case.get("history", []), list):
            problems.append(f"{cid}: history must be a list of earlier questions")
        expected = case.get("expected") or {}
        statuses = expected.get("statuses")
        if not statuses or any(s not in STATUSES for s in statuses):
            problems.append(f"{cid}: expected.statuses must list values from {STATUSES}")
        groups = expected.get("must_include", [])
        if not all(isinstance(g, list) and g and all(isinstance(x, str) and x for x in g) for g in groups):
            problems.append(f"{cid}: expected.must_include must be a list of non-empty alternative lists")
        answerable = case.get("category") not in ("unanswerable",)
        if answerable and case.get("category") != "injection":
            if not expected.get("relevant_docs"):
                problems.append(f"{cid}: answerable cases need expected.relevant_docs")
            if not case.get("evidence"):
                problems.append(f"{cid}: answerable cases need source evidence")
        if case.get("category") == "follow_up" and not case.get("history"):
            problems.append(f"{cid}: follow_up cases need at least one earlier question in history")
        if case.get("category") == "unanswerable" and not case.get("abstain_rationale"):
            problems.append(f"{cid}: unanswerable cases need abstain_rationale")
        for doc in expected.get("relevant_docs", []):
            if not (docs_root / doc).exists():
                problems.append(f"{cid}: relevant doc {doc} does not exist")
    checks.append(_check("schema", "FAIL" if problems else "PASS", f"{len(cases)} cases, {len(problems)} schema problems", problems))

    # size and splits -------------------------------------------------------------
    split_counts = Counter(c.get("split") for c in cases)
    category_counts = Counter(c.get("category") for c in cases)
    size_status = "PASS" if TARGET_MIN <= len(cases) <= TARGET_MAX else ("WARN" if MIN_CASES <= len(cases) <= MAX_CASES else "FAIL")
    checks.append(_check("size", size_status, f"{len(cases)} cases (required {MIN_CASES}-{MAX_CASES}, target {TARGET_MIN}-{TARGET_MAX})"))
    missing_splits = [s for s in SPLITS if not split_counts.get(s)]
    needed = ("multi_hop", "follow_up", "unanswerable")
    missing_categories = [c for c in needed if not category_counts.get(c)]
    split_issues = [f"empty split {s}" for s in missing_splits] + [f"no {c} cases" for c in missing_categories]
    for split in SPLITS:
        cats = {c.get("category") for c in cases if c.get("split") == split}
        if split in ("dev", "test", "holdout") and "unanswerable" not in cats:
            split_issues.append(f"{split} has no unanswerable case")
    checks.append(_check(
        "splits",
        "FAIL" if missing_splits or missing_categories else ("WARN" if split_issues else "PASS"),
        "splits " + ", ".join(f"{s}={split_counts.get(s, 0)}" for s in SPLITS) + "; categories " + ", ".join(f"{k}={v}" for k, v in sorted(category_counts.items())),
        split_issues,
    ))

    # answerability: the knowledge base must contain the expected facts ------------------
    missing_terms: List[str] = []
    doc_cache: Dict[str, str] = {}
    for case in cases:
        expected = case.get("expected") or {}
        docs = expected.get("relevant_docs") or []
        if not docs:
            continue
        text = ""
        for doc in docs:
            if doc not in doc_cache:
                path = docs_root / doc
                doc_cache[doc] = path.read_text(encoding="utf-8").lower() if path.exists() else ""
            text += doc_cache[doc]
        for group in expected.get("must_include", []):
            if not any(alt.lower() in text for alt in group):
                missing_terms.append(f"{case.get('id')}: none of {group} appears in {', '.join(docs)}")
    checks.append(_check(
        "answerability",
        "WARN" if missing_terms else "PASS",
        f"{len(missing_terms)} expected-answer term groups not found in the cited docs",
        missing_terms,
    ))

    # checksums -------------------------------------------------------------------
    recorded_path = checksum_path(dataset)
    current = compute_checksums(data)
    if not recorded_path.exists():
        checks.append(_check("checksums", "FAIL", f"{recorded_path.name} missing; run `lear eval validate --write-checksums` once and commit it"))
    else:
        recorded = json.loads(recorded_path.read_text(encoding="utf-8"))
        changed = [cid for cid, digest in recorded.get("caseSha256", {}).items() if current["caseSha256"].get(cid) not in (None, digest)]
        removed = [cid for cid in recorded.get("caseSha256", {}) if cid not in current["caseSha256"]]
        added = [cid for cid in current["caseSha256"] if cid not in recorded.get("caseSha256", {})]
        moved = [
            cid for split, members in recorded.get("splits", {}).items() for cid in members
            if cid in current["caseSha256"] and cid not in current["splits"].get(split, [])
        ]
        legacy_changed = [
            rel for rel, digest in (recorded.get("legacySha256") or {}).items()
            if digest is not None and current["legacySha256"].get(rel) != digest
        ]
        issues = [f"changed {c}" for c in changed] + [f"removed {c}" for c in removed] + [f"moved split {c}" for c in moved] + [f"legacy set modified: {r}" for r in legacy_changed]
        status = "FAIL" if issues else ("WARN" if added else "PASS")
        summary = (
            f"dataset sha256 {current['datasetSha256'][:16]}…; {len(changed)} changed, {len(removed)} removed, "
            f"{len(moved)} moved, {len(added)} added since checksums; legacy golden sets "
            + ("unchanged" if not legacy_changed else "MODIFIED")
        )
        checks.append(_check("checksums", status, summary, issues + [f"added {a} (not yet recorded)" for a in added]))

    # evidence ----------------------------------------------------------------------
    commit = (data.get("source") or {}).get("commit")
    source = Path(source).expanduser() if source else None
    if source is not None and not (source / ".git").exists():
        source = None
    reader = _Source(source, commit)
    evidence_total = 0
    bad: List[str] = []
    for case in cases:
        for item in case.get("evidence", []):
            evidence_total += 1
            path, start, end, quote = item.get("path"), item.get("start"), item.get("end"), item.get("quote", "")
            if commit and item.get("commit") and not commit.startswith(item["commit"]):
                bad.append(f"{case['id']}: evidence commit {item['commit']} is not the dataset pin")
            if source is None:
                continue
            lines = reader.lines(path)
            if lines is None:
                bad.append(f"{case['id']}: {path} not found at {commit[:12]}")
                continue
            if not (isinstance(start, int) and isinstance(end, int) and 1 <= start <= end <= len(lines)):
                bad.append(f"{case['id']}: {path}:L{start}-L{end} outside 1..{len(lines)}")
                continue
            window = _norm("\n".join(lines[start - 1:end]))
            if not quote or _norm(quote) not in window:
                bad.append(f"{case['id']}: quote not found in {path}:L{start}-L{end}: {quote[:60]!r}")
    if source is None:
        checks.append(_check("evidence", "WARN", f"{evidence_total} evidence items; not verified (no Lear checkout supplied)"))
    else:
        checks.append(_check("evidence", "FAIL" if bad else "PASS", f"{evidence_total} evidence items checked with git show at {str(commit)[:12]}; {len(bad)} problems", bad))

    valid = all(c["status"] != "FAIL" for c in checks)
    return {
        "valid": valid,
        "dataset": str(dataset),
        "name": data.get("name"),
        "version": data.get("version"),
        "cases": len(cases),
        "splits": dict(split_counts),
        "categories": dict(category_counts),
        "datasetSha256": current["datasetSha256"],
        "checks": checks,
    }


def render_validation(report: Dict[str, Any]) -> str:
    lines = [f"Golden set {report['name']} v{report['version']} — {report['cases']} cases ({report['dataset']})"]
    for check in report["checks"]:
        lines.append(f"  [{check['status']:4}] {check['name']}: {check['summary']}")
        for example in check["examples"][:8]:
            lines.append(f"         - {example}")
    lines.append("RESULT: " + ("VALID" if report["valid"] else "INVALID"))
    return "\n".join(lines)


def select_cases(data: Dict[str, Any], suites: Sequence[str], *, allow_holdout: bool = False) -> List[Dict[str, Any]]:
    """Cases for the requested splits; the holdout needs an explicit opt-in."""

    wanted: List[str] = []
    for suite in suites:
        suite = suite.strip().lower()
        if suite == "all":
            wanted.extend(["calibration", "dev", "test"] + (["holdout"] if allow_holdout else []))
        elif suite in SPLITS:
            wanted.append(suite)
        else:
            raise ValidationError(f"unknown suite {suite!r}", hint="all | calibration | dev | test | holdout")
    if "holdout" in wanted and not allow_holdout:
        raise ValidationError("the holdout split is locked", hint="pass --allow-holdout for the single final holdout run")
    order = {s: i for i, s in enumerate(SPLITS)}
    chosen = [c for c in data["cases"] if c.get("split") in set(wanted)]
    return sorted(chosen, key=lambda c: (order[c["split"]], c["id"]))


def iter_questions(cases: Iterable[Dict[str, Any]]) -> Iterable[str]:
    for case in cases:
        yield case["question"]
