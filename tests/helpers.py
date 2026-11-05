"""Shared test fixtures.

Every test builds its own temporary data directory: no test touches ``data/``,
the network or a paid API.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ragformation.config import Settings  # noqa: E402
from ragformation.engine import RAGEngine  # noqa: E402

CORPUS_DIR = REPO_ROOT / "corpus"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def make_settings(data_dir: Path, **overrides) -> Settings:
    """Offline, deterministic settings: hashing embeddings + extractive answers."""
    base = {
        "data_dir": Path(data_dir),
        "embed_provider": "hashing",
        "llm_provider": "extractive",
        "persist_traces": True,
    }
    base.update(overrides)
    settings = Settings(**base)
    settings.validate()
    return settings


class TempDirTestCase(unittest.TestCase):
    """Test case with a throwaway data directory."""

    settings_overrides: dict = {}

    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="ragformation-test-"))
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.settings = make_settings(self.tmpdir / "data", **self.settings_overrides)
        self._engines: list[RAGEngine] = []

    def make_engine(self, **overrides) -> RAGEngine:
        settings = make_settings(self.settings.data_dir, **{**self.settings_overrides, **overrides})
        engine = RAGEngine(settings)
        self._engines.append(engine)
        return engine

    def corpus_engine(self) -> RAGEngine:
        engine = self.make_engine()
        report = engine.ingest_path([str(CORPUS_DIR)])
        self.assertEqual(report["failed"], 0, report["results"])
        return engine

    def tearDown(self) -> None:
        for engine in self._engines:
            try:
                engine.close()
            except Exception:  # pragma: no cover - best effort cleanup
                pass


def write(path: Path, content: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if isinstance(content, bytes) else "w"
    kwargs = {} if isinstance(content, bytes) else {"encoding": "utf-8"}
    with open(path, mode, **kwargs) as handle:
        handle.write(content)
    return path

