"""Read-only shared-memory connector.

The take-home refers to a separately supplied shared-memory repository. It was
not supplied with this checkout, so this module provides the integration
boundary and one concrete, testable connector — a local directory (for example
a clone of that repository) — and nothing that pretends to talk to an
unknown service.

Rules:

* **read-only** – files are opened for reading only; nothing is written back;
* **bounded** – only allow-listed extensions, size-capped, no hidden paths,
  symlinks that leave the root are skipped;
* **provenance** – every record keeps its relative path, SHA-256, size,
  modification time and (if the root is a git checkout) the HEAD commit read
  from ``.git`` files without executing git;
* **guarded** – records that look like credentials or prompt-injection
  instructions are rejected by the memory store and counted, not imported;
* **separate namespace** – records land in memory scope ``global`` owned by
  ``__shared__``, never in the Lear documentation index.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Protocol, Sequence

from ..errors import RagError, ValidationError
from .memory import MemoryStore

ALLOWED_SUFFIXES = (".md", ".txt", ".json")
MAX_RECORD_BYTES = 64 * 1024


@dataclass
class SharedRecord:
    key: str
    text: str
    provenance: Dict[str, Any]


class SharedMemoryConnector(Protocol):
    name: str

    def describe(self) -> Dict[str, Any]: ...

    def records(self) -> Iterator[SharedRecord]: ...


def _git_head(root: Path) -> Optional[str]:
    head = root / ".git" / "HEAD"
    try:
        value = head.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if value.startswith("ref: "):
        ref = root / ".git" / value[5:]
        try:
            return ref.read_text(encoding="utf-8").strip()
        except OSError:
            packed = root / ".git" / "packed-refs"
            try:
                for line in packed.read_text(encoding="utf-8").splitlines():
                    if line.endswith(value[5:]):
                        return line.split()[0]
            except OSError:
                return None
            return None
    return value or None


class LocalDirectoryConnector:
    name = "local-directory"

    def __init__(self, root: str | Path, *, suffixes: Sequence[str] = ALLOWED_SUFFIXES, max_bytes: int = MAX_RECORD_BYTES) -> None:
        self.root = Path(root).expanduser()
        if not self.root.is_dir():
            raise ValidationError("shared-memory source is not a directory", hint=str(self.root))
        self.root = self.root.resolve()
        self.suffixes = tuple(s.lower() for s in suffixes)
        self.max_bytes = max_bytes
        self.skipped: List[Dict[str, str]] = []

    def describe(self) -> Dict[str, Any]:
        return {"connector": self.name, "root": str(self.root), "gitHead": _git_head(self.root), "readOnly": True}

    def records(self) -> Iterator[SharedRecord]:
        head = _git_head(self.root)
        for path in sorted(self.root.rglob("*")):
            rel = path.relative_to(self.root).as_posix()
            if any(part.startswith(".") for part in Path(rel).parts):
                continue
            if not path.is_file() or path.suffix.lower() not in self.suffixes:
                continue
            try:
                resolved = path.resolve()
                resolved.relative_to(self.root)
            except (OSError, ValueError):
                self.skipped.append({"path": rel, "reason": "symlink_outside_root"})
                continue
            size = path.stat().st_size
            if size > self.max_bytes:
                self.skipped.append({"path": rel, "reason": f"larger_than_{self.max_bytes}_bytes"})
                continue
            with open(resolved, "rb") as handle:  # read-only by construction
                raw = handle.read()
            text = raw.decode("utf-8", "replace")
            if path.suffix.lower() == ".json":
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    self.skipped.append({"path": rel, "reason": "invalid_json"})
                    continue
                items = data if isinstance(data, list) else [data]
                for index, item in enumerate(items):
                    body = item.get("text") if isinstance(item, dict) else None
                    if not isinstance(body, str) or not body.strip():
                        self.skipped.append({"path": f"{rel}#{index}", "reason": "no_text_field"})
                        continue
                    yield SharedRecord(
                        key=f"{rel}#{index}",
                        text=body,
                        provenance=self._provenance(rel, raw, path, head, index=index),
                    )
                continue
            if not text.strip():
                self.skipped.append({"path": rel, "reason": "empty"})
                continue
            yield SharedRecord(key=rel, text=text, provenance=self._provenance(rel, raw, path, head))

    def _provenance(self, rel: str, raw: bytes, path: Path, head: Optional[str], *, index: Optional[int] = None) -> Dict[str, Any]:
        data = {
            "connector": self.name,
            "root": str(self.root),
            "path": rel,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "modifiedAt": int(path.stat().st_mtime),
            "gitHead": head,
        }
        if index is not None:
            data["index"] = index
        return data


def import_shared_memory(connector: SharedMemoryConnector, store: MemoryStore) -> Dict[str, Any]:
    counts = {"imported": 0, "updated": 0, "unchanged": 0, "rejected": 0}
    rejected: List[Dict[str, str]] = []
    for record in connector.records():
        try:
            outcome = store.upsert_shared(key=record.key, text=record.text, provenance=record.provenance)
        except RagError as exc:
            counts["rejected"] += 1
            rejected.append({"key": record.key, "reason": exc.message, "rules": exc.hint or ""})
            continue
        counts[outcome] += 1
    return {
        "source": connector.describe(),
        "counts": counts,
        "rejected": rejected,
        "skipped": list(getattr(connector, "skipped", [])),
        "namespace": "shared-memory (memory scope=global, owner=__shared__)",
    }
