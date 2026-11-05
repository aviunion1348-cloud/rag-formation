"""Lear documentation index: its own namespace, incremental updates, manifest.

The Lear docs are ingested into a dedicated engine data directory
(``data/lear/index`` for Ollama, ``data/lear/index-baseline`` for the labelled
baseline), never into the generic ``data/`` index or a test fixture index.
Each document gets a stable origin ``lear-docs:<relative path>``, so its
source id does not depend on the working directory.

``rebuild`` is incremental and idempotent: unchanged documents are skipped by
content hash, changed ones are replaced atomically (the engine's
``replace_source``), documents removed from disk are deleted from the index,
and ``full=True`` re-embeds every chunk through the engine's staged rebuild,
which keeps the previous vectors for rollback.

The engine stores chunk text, heading path, ordinal, content hash, revision and
the embedding fingerprint. The manifest written next to the index adds what the
engine does not track: the pinned Lear commit, the document path, the chunk's
line span in its document, the ``path:Lx-Ly`` source references it contains,
the namespace and the embedding model/dimension.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..engine import RAGEngine
from ..errors import EmbeddingMismatch
from .settings import LearSettings

NAMESPACE = "lear-docs"
ORIGIN_PREFIX = f"{NAMESPACE}:"
MANIFEST_NAME = "chunk_manifest.json"
MANIFEST_VERSION = "lear-chunk-manifest-v1"
SOURCE_REF = re.compile(r"([A-Za-z0-9_./-]+\.[A-Za-z0-9]+):L(\d+)(?:-L?(\d+))?")


def load_pin(docs_dir: Path) -> Dict[str, Any]:
    path = Path(docs_dir) / "SOURCE_PIN.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def doc_files(docs_dir: Path) -> List[Path]:
    root = Path(docs_dir)
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.md") if p.is_file() and not any(part.startswith(".") for part in p.parts))


def _rel(path: Path, docs_dir: Path) -> str:
    return path.resolve().relative_to(Path(docs_dir).resolve()).as_posix()


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()[:200]
    return fallback


def _norm(line: str) -> str:
    line = line.strip().lstrip("#").strip()
    line = re.sub(r"^[-*+]\s+|^\d+\.\s+", "", line)
    return " ".join(line.replace("|", " ").replace("`", "").replace("**", "").split()).lower()


def locate_lines(doc_lines: List[str], chunk_text: str, heading_path: List[str]) -> Tuple[int, int, str]:
    """1-based inclusive line span of a chunk inside its document.

    A document line is *covered* when its normalised text (12+ characters)
    occurs in the normalised chunk text. Falls back to the span of the chunk's
    deepest heading section, then to the whole document, and says which.
    """

    haystack = _norm(chunk_text.replace("\n", " "))
    covered = [i for i, line in enumerate(doc_lines) if len(_norm(line)) >= 12 and _norm(line) in haystack]
    if covered:
        return covered[0] + 1, covered[-1] + 1, "lines"
    if heading_path:
        target = _norm(heading_path[-1])
        for i, line in enumerate(doc_lines):
            if line.lstrip().startswith("#") and _norm(line) == target:
                end = len(doc_lines)
                for j in range(i + 1, len(doc_lines)):
                    if doc_lines[j].lstrip().startswith("#"):
                        end = j
                        break
                return i + 1, end, "section"
    return 1, len(doc_lines), "document"


def source_refs(text: str) -> List[str]:
    refs: List[str] = []
    for path, start, end in SOURCE_REF.findall(text or ""):
        ref = f"{path}:L{start}" + (f"-L{end}" if end else "")
        if ref not in refs:
            refs.append(ref)
    return refs


def check_index_fingerprint(engine: RAGEngine) -> Optional[str]:
    """Refuse to mix embedding spaces; return the stored fingerprint.

    The core retriever scores a query vector against stored vectors with a dot
    product that returns 0.0 when lengths differ, and a different model with
    the same dimension yields meaningless scores. Either way the semantic
    channel would degrade silently, so the Lear layer checks the configured
    embedder against the index before every query and incremental rebuild.
    """

    stored = engine.store.get_meta("embedding_fingerprint")
    if not stored or engine.store.counts()["vectors"] == 0:
        return stored
    current = engine.embedder.fingerprint
    if stored != current:
        raise EmbeddingMismatch(
            "the index was built with a different embedding model or dimension",
            hint=f"index={stored}, configured={current}. Run `python -m ragformation lear index rebuild --full` to re-embed.",
        )
    return stored


def rebuild(engine: RAGEngine, settings: LearSettings, *, full: bool = False) -> Dict[str, Any]:
    started = time.perf_counter()
    docs_dir = Path(settings.docs_dir)
    files = doc_files(docs_dir)
    pin = load_pin(docs_dir)
    commit_tag = f"commit:{str(pin.get('commit', 'unknown'))[:12]}"
    on_disk: Dict[str, Path] = {ORIGIN_PREFIX + _rel(p, docs_dir): p for p in files}
    counts = {"new": 0, "updated": 0, "unchanged": 0, "failed": 0, "deleted": 0}
    failures: List[Dict[str, Any]] = []
    rebuild_report = None
    if full:
        # Re-embed what is stored first, so the ingest below runs against an
        # index whose fingerprint already matches the configured embedder.
        rebuild_report = engine.rebuild(force=True)
    else:
        check_index_fingerprint(engine)
    for origin, path in sorted(on_disk.items()):
        data = path.read_bytes()
        report = engine.ingest_bytes(
            path.name,
            data,
            title=_title(data.decode("utf-8", "replace"), path.stem),
            tags=[f"namespace:{NAMESPACE}", commit_tag],
            origin=origin,
        )
        for result in report.get("results", []):
            status = result.get("status")
            if status == "unchanged":
                counts["unchanged"] += 1
            elif status == "indexed":
                # replace_source bumps the revision; revision 1 is a new document.
                counts["updated" if int(result.get("revision") or 1) > 1 else "new"] += 1
            elif status == "failed":
                counts["failed"] += 1
                failures.append({"origin": origin, "error": result.get("error")})
            else:
                counts.setdefault(status or "other", 0)
                counts[status or "other"] += 1
    for source in engine.list_sources():
        origin = str(source.get("origin") or "")
        if origin.startswith(ORIGIN_PREFIX) and origin not in on_disk:
            engine.delete_source(source["sourceId"])
            counts["deleted"] += 1
    manifest = build_manifest(engine, settings, pin)
    path = write_manifest(settings, manifest)
    return {
        "namespace": NAMESPACE,
        "docsDir": str(docs_dir),
        "indexDir": str(settings.index_dir),
        "files": len(files),
        "counts": counts,
        "failures": failures,
        "chunks": len(manifest["chunks"]),
        "embedding": manifest["embedding"],
        "sourceCommit": manifest["sourceCommit"],
        "manifest": str(path),
        "fullRebuild": rebuild_report,
        "elapsedMs": round((time.perf_counter() - started) * 1000.0, 2),
        "qualifying": settings.qualifying,
    }


def build_manifest(engine: RAGEngine, settings: LearSettings, pin: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    pin = pin if pin is not None else load_pin(Path(settings.docs_dir))
    docs_dir = Path(settings.docs_dir)
    sources = {s["sourceId"]: s for s in engine.list_sources() if str(s.get("origin", "")).startswith(ORIGIN_PREFIX)}
    lines_cache: Dict[str, List[str]] = {}
    embedding = {
        "provider": engine.embedder.name,
        "model": engine.embedder.model,
        "dimension": engine.embedder.dimension,
        "fingerprint": engine.embedder.fingerprint,
        "semantic": engine.embedder.semantic,
    }
    entries = []
    for chunk in engine.store.all_chunks():
        source = sources.get(chunk.source_id)
        if source is None:
            continue
        rel = str(source["origin"])[len(ORIGIN_PREFIX):]
        if rel not in lines_cache:
            doc = docs_dir / rel
            lines_cache[rel] = doc.read_text(encoding="utf-8").splitlines() if doc.exists() else []
        start, end, precision = locate_lines(lines_cache[rel], chunk.text, list(chunk.section_path))
        entries.append(
            {
                "chunk_id": chunk.chunk_id,
                "source_id": chunk.source_id,
                "source_revision": chunk.revision,
                "source_path": f"{Path(settings.docs_dir).as_posix().rstrip('/')}/{rel}",
                "source_commit": pin.get("commit"),
                "document_title": source.get("title"),
                "heading_path": list(chunk.section_path),
                "start_line": start,
                "end_line": end,
                "line_precision": precision,
                "chunk_index": chunk.ordinal,
                "content_hash": chunk.content_hash,
                "token_count": chunk.token_count,
                "source_refs": source_refs(chunk.text),
                "embedding_model": embedding["model"],
                "embedding_fingerprint": embedding["fingerprint"],
                "embedding_dimension": embedding["dimension"],
                "created_at": source.get("createdAt") or source.get("updatedAt"),
                "namespace": NAMESPACE,
            }
        )
    entries.sort(key=lambda e: (e["source_path"], e["chunk_index"]))
    return {
        "version": MANIFEST_VERSION,
        "namespace": NAMESPACE,
        "sourceRepository": pin.get("repository"),
        "sourceCommit": pin.get("commit"),
        "embedding": embedding,
        "backend": settings.backend,
        "qualifying": settings.qualifying,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "chunks": entries,
    }


def write_manifest(settings: LearSettings, manifest: Dict[str, Any]) -> Path:
    path = Path(settings.index_dir) / MANIFEST_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_manifest(settings: LearSettings) -> Dict[str, Any]:
    path = Path(settings.index_dir) / MANIFEST_NAME
    if not path.exists():
        return {"chunks": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"chunks": []}


def stats(engine: RAGEngine, settings: LearSettings) -> Dict[str, Any]:
    manifest = load_manifest(settings)
    sources = [s for s in engine.list_sources() if str(s.get("origin", "")).startswith(ORIGIN_PREFIX)]
    chunks = manifest.get("chunks", [])
    precision: Dict[str, int] = {}
    for entry in chunks:
        precision[entry.get("line_precision", "?")] = precision.get(entry.get("line_precision", "?"), 0) + 1
    tokens = [int(e.get("token_count") or 0) for e in chunks]
    return {
        "namespace": NAMESPACE,
        "indexDir": str(settings.index_dir),
        "documents": len(sources),
        "chunks": len(chunks),
        "tokens": {"total": sum(tokens), "max": max(tokens) if tokens else 0, "mean": round(sum(tokens) / len(tokens), 1) if tokens else 0},
        "linePrecision": precision,
        "chunksWithSourceRefs": sum(1 for e in chunks if e.get("source_refs")),
        "embedding": manifest.get("embedding"),
        "indexFingerprint": engine.store.get_meta("embedding_fingerprint"),
        "sourceCommit": manifest.get("sourceCommit"),
        "manifestGeneratedAt": manifest.get("generatedAt"),
        "qualifying": settings.qualifying,
    }
