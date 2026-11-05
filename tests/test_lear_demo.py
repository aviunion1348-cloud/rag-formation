"""Tests for ``lear demo``: terminal capabilities, provenance, replay integrity,
exit-code preservation, and the live baseline scenes. No Ollama is needed."""

from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import io
import json
import os
import re
import select
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ragformation.lear import demo
from ragformation.lear import demo_effects as fx
from ragformation.lear.demo_render import RESET, Renderer, TermCaps, detect_caps
from ragformation.lear.settings import LearSettings

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs" / "lear_backend"
ANSI = re.compile(r"\x1b\[")


class _Stream(io.StringIO):
    def __init__(self, tty: bool, encoding: str = "utf-8") -> None:
        super().__init__()
        self._tty = tty
        self._encoding = encoding

    def isatty(self) -> bool:
        return self._tty

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return self._encoding


def _args(**kw):
    base = dict(mode="examiner", json=True, plain=False, no_animate=False, scene=None, source=None, bundle=None,
                out=None, speed=0.0, auto=True, keep_scratch=False, full=False, preview=None)
    base.update(kw)
    return argparse.Namespace(**base)


def _settings(tmp: str, backend: str = "baseline", **kw) -> LearSettings:
    return LearSettings.from_env(backend=backend, data_dir=Path(tmp) / "user-data", docs_dir=DOCS, **kw)


class CapabilityTests(unittest.TestCase):
    def test_non_tty_disables_colour_and_motion(self):
        caps = detect_caps(_Stream(False), env={}, stdin=_Stream(False))
        self.assertFalse(caps.color)
        self.assertFalse(caps.animate)
        self.assertFalse(caps.interactive)
        self.assertIn("output is not a TTY", caps.reasons)

    def test_tty_with_no_color_ci_plain_and_json(self):
        tty = _Stream(True)
        self.assertTrue(detect_caps(tty, env={"TERM": "xterm"}, stdin=_Stream(True)).color)
        self.assertFalse(detect_caps(tty, env={"TERM": "xterm", "NO_COLOR": "1"}).color)
        self.assertFalse(detect_caps(tty, env={"TERM": "xterm", "CI": "true"}).animate)
        plain = detect_caps(tty, env={"TERM": "xterm"}, plain=True)
        self.assertFalse(plain.color or plain.unicode or plain.animate)
        js = detect_caps(tty, env={"TERM": "xterm"}, json_mode=True)
        self.assertFalse(js.color or js.animate)
        self.assertFalse(detect_caps(tty, env={"TERM": "dumb"}).color)

    def test_plain_output_is_ascii_and_wraps_without_truncation(self):
        out = _Stream(False, encoding="ascii")
        r = Renderer(TermCaps(tty=False, color=False, unicode=False, width=40, animate=False, interactive=False), out)
        long = "citation [1] → docs/lear_backend/authentication_authorization.md lines 13-18 — caps ≤4B …"
        r.write(long)
        r.panel("BLOCKED", ["exit 6 → start Ollama"])
        text = out.getvalue()
        self.assertTrue(all(ord(ch) < 128 for ch in text), text)
        squashed = "".join(text.split())
        for word in ("citation", "docs/lear_backend/authentication_authorization.md", "13-18", "<=4B", "->", "start", "Ollama"):
            self.assertIn(word, squashed)  # wrapped, never truncated
        self.assertTrue(all(len(line) <= 40 for line in text.splitlines()))

    def test_resize_is_picked_up_at_the_next_scene(self):
        r = Renderer(TermCaps(tty=True, color=False, unicode=True, width=100, animate=False, interactive=False), _Stream(True))
        with mock.patch("shutil.get_terminal_size", return_value=__import__("os").terminal_size((60, 20))):
            r.refresh_width()
        self.assertEqual(r.caps.width, 60)

    def test_reset_is_written_when_colour_was_used(self):
        out = _Stream(True)
        r = Renderer(TermCaps(tty=True, color=True, unicode=True, width=80, animate=False, interactive=False), out)
        r.write(r.style("x", "red"))
        r.reset()
        self.assertTrue(out.getvalue().endswith(RESET))


class SceneSelectionAndEffectsTests(unittest.TestCase):
    def test_select_scenes(self):
        self.assertEqual(demo.select_scenes("0,5,S08,s11"), ["S00", "S05", "S08", "S11"])
        self.assertEqual(demo.select_scenes(None), demo.SCENE_IDS)
        with self.assertRaises(ValueError):
            demo.select_scenes("42")

    def test_effects_json(self):
        out = io.StringIO()
        code = demo.run_demo(_args(mode="effects"), _settings(tempfile.mkdtemp()), out=out)
        self.assertEqual(code, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["count"], len(fx.RECIPES))
        self.assertEqual(data["registry"], fx.REGISTRY_VERSION)
        for row in data["effects"]:
            for key in ("id", "name", "family", "trigger", "transformation", "timing", "purpose", "scene", "interrupt",
                        "terminal", "reduced", "noColour", "cost", "cleanup", "seed", "test", "accessibility"):
                self.assertTrue(row.get(key), (row.get("id"), key))


def _caps(**kw) -> TermCaps:
    base = dict(tty=False, color=False, unicode=False, width=100, animate=False, interactive=False)
    base.update(kw)
    return TermCaps(**base)


class EffectRegistryTests(unittest.TestCase):
    def test_registry_has_at_least_100_genuinely_distinct_recipes(self):
        self.assertEqual(fx.validate_registry(), [])
        self.assertGreaterEqual(len(fx.RECIPES), 100)
        ids = [r.id for r in fx.RECIPES]
        purposes = [r.purpose for r in fx.RECIPES]
        combos = [(r.layout, r.motion, r.purpose) for r in fx.RECIPES]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(purposes), len(set(purposes)))  # no renamed copies
        self.assertEqual(len(combos), len(set(combos)))
        self.assertGreaterEqual(len({r.family for r in fx.RECIPES}), 40)
        self.assertGreaterEqual(len(fx.LAYOUTS), 30)

    def test_every_recipe_renders_plain_ascii_without_ansi(self):
        for recipe in fx.RECIPES:
            out = _Stream(False, encoding="ascii")
            r = Renderer(_caps(), out)
            lines = fx.play(r, recipe.id, fx.example_for(recipe))
            text = out.getvalue()
            self.assertTrue(lines and text.strip(), recipe.id)
            self.assertTrue(all(ord(ch) < 128 for ch in text), recipe.id)
            self.assertIsNone(ANSI.search(text), recipe.id)

    def test_static_form_is_the_final_frame_of_the_animated_form(self):
        animated_any = False
        for recipe in fx.RECIPES:
            data = fx.example_for(recipe)
            static = fx.play(Renderer(_caps(tty=True, color=True, unicode=True), _Stream(True)), recipe.id, data)
            out = _Stream(True)
            moving = fx.play(Renderer(_caps(tty=True, color=True, unicode=True, animate=True), out, speed=1e-6), recipe.id, data)
            self.assertEqual(static, moving, recipe.id)
            frames = out.getvalue().count("\r")
            self.assertLessEqual(frames, 50 * max(1, len(moving)), recipe.id)  # bounded frame count
            animated_any = animated_any or frames > 0
        self.assertTrue(animated_any)

    def test_typing_is_reserved_for_a_real_model_stream(self):
        data = {"rows": [["", "an extractive answer that must not be typed out"]]}
        for real, expect_frames in ((False, False), (True, True)):
            out = _Stream(True)
            r = Renderer(_caps(tty=True, color=True, unicode=True, animate=True), out, speed=1e-6)
            fx.play(r, "answer_stream", {**data, "realStream": real})
            self.assertEqual("\r" in out.getvalue(), expect_frames)

    def test_registry_page_is_generated_from_the_current_registry(self):
        page = (REPO_ROOT / "docs" / "lear_agent" / "demo_effects.md").read_text(encoding="utf-8")
        self.assertIn(f"**{len(fx.RECIPES)} recipes**", page)
        self.assertIn(fx.REGISTRY_VERSION, page)
        missing = [r.id for r in fx.RECIPES if f"| `{r.id}` |" not in page]
        self.assertEqual(missing, [], "rerun: python3 scripts/gen_demo_effects_doc.py")

    def test_preview_renders_labelled_example_data(self):
        out = _Stream(False)
        code = demo.run_demo(_args(mode="effects", json=False, preview="chunk_token_strip,percentile_ruler"), _settings(tempfile.mkdtemp()), out=out)
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().count("[ILLUSTRATIVE]"), 2)
        self.assertEqual(demo.run_demo(_args(mode="effects", json=False, preview="no_such_effect"), _settings(tempfile.mkdtemp()), out=_Stream(False)), 2)


class ExitCodeTests(unittest.TestCase):
    def test_scene_errors_fail_the_demo_but_missing_repo_artifacts_do_not(self):
        s = _settings(tempfile.mkdtemp())
        self.assertEqual(demo.demo_exit_code([{"blocked": {"exitCode": 2}, "steps": []}], s), 5)
        self.assertEqual(demo.demo_exit_code([{"blocked": {"exitCode": None}, "steps": []}], s), 0)

    def test_golden_fallback_matches_the_frozen_set(self):
        golden = {c["id"]: c for c in json.loads((REPO_ROOT / "evals" / "lear" / "golden_lear_v1.json").read_text(encoding="utf-8"))["cases"]}
        for case_id, copy_ in demo.GOLDEN_FALLBACK.items():
            self.assertEqual(copy_["question"], golden[case_id]["question"])
            self.assertEqual(copy_["history"], golden[case_id].get("history") or [])

    def test_mismatch_is_gate_failure_and_block_is_dependency(self):
        s = _settings(tempfile.mkdtemp())
        ok = [{"steps": [{"verdict": "MATCH"}, {"verdict": "LIMITATION"}]}]
        self.assertEqual(demo.demo_exit_code(ok, s), 0)
        self.assertEqual(demo.demo_exit_code([{"steps": [{"verdict": "MISMATCH"}]}], s), 5)
        blocked = [{"blocked": {"exitCode": 6}, "steps": []}]
        self.assertEqual(demo.demo_exit_code(blocked, s), 0)  # baseline: not a qualifying run
        self.assertEqual(demo.demo_exit_code(blocked, _settings(tempfile.mkdtemp(), backend="ollama")), 6)

    def test_ctrl_c_returns_130_and_resets_the_terminal(self):
        out = _Stream(True)
        with mock.patch.object(demo, "_live", side_effect=KeyboardInterrupt), \
                mock.patch.dict("os.environ", {"TERM": "xterm", "FORCE_COLOR": "1"}, clear=False):
            code = demo.run_demo(_args(json=False), _settings(tempfile.mkdtemp()), out=out)
        self.assertEqual(code, 130)
        self.assertTrue(out.getvalue().endswith(RESET))


def _record(sid="S06", p="LIVE"):
    return demo._scene(sid, "t", p, facts=[demo.fact("status", "GROUNDED", p)],
                       steps=[demo.step("x", "a", "a", "MATCH")], evidence=["docs/lear_agent/final_report.md"])


class ReplayTests(unittest.TestCase):
    def test_bundle_round_trip_and_tamper_detection(self):
        s = _settings(tempfile.mkdtemp())
        bundle = demo.build_bundle([_record()], s, None)
        self.assertTrue(demo.validate_bundle(bundle)["valid"])
        tampered = copy.deepcopy(bundle)
        tampered["scenes"][0]["facts"][0]["v"] = "PASS"
        check = demo.validate_bundle(tampered)
        self.assertFalse(check["valid"])
        self.assertTrue(any("checksum" in p for p in check["problems"]))
        no_meta = copy.deepcopy(bundle)
        no_meta["meta"]["runId"] = ""
        self.assertTrue(any("provenance" in p for p in demo.validate_bundle(no_meta)["problems"]))

    def test_missing_evidence_is_a_warning_not_a_crash(self):
        bundle = demo.build_bundle([_record()], _settings(tempfile.mkdtemp()), None)
        bundle["meta"]["evidence"]["docs/does_not_exist.json"] = "0" * 64
        body = {"meta": bundle["meta"], "scenes": bundle["scenes"]}
        bundle["sha256"] = __import__("hashlib").sha256(demo.canonical(body)).hexdigest()
        check = demo.validate_bundle(bundle)
        self.assertTrue(check["valid"])
        self.assertIn("evidence missing here: docs/does_not_exist.json", check["warnings"])

    def test_replay_marks_every_scene_recorded(self):
        tmp = Path(tempfile.mkdtemp())
        bundle = demo.build_bundle([_record("S04"), _record("S06")], _settings(str(tmp)), None)
        path = tmp / "b.json"
        path.write_text(json.dumps(bundle), encoding="utf-8")
        out = _Stream(False)
        code = demo.run_demo(_args(mode="replay", json=False, bundle=str(path)), _settings(str(tmp)), out=out)
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertEqual(text.count("RECORDED REPLAY — run"), 2)
        self.assertNotIn("[LIVE]", text)

    def test_corrupt_bundle_exits_5_with_explanation_only(self):
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "b.json"
        path.write_text(json.dumps({"schema": "nope"}), encoding="utf-8")
        out = _Stream(False)
        code = demo.run_demo(_args(mode="replay", json=False, bundle=str(path)), _settings(str(tmp)), out=out)
        self.assertEqual(code, 5)
        self.assertIn("EVIDENCE INTEGRITY FAILURE", out.getvalue())
        self.assertIn("[ILLUSTRATIVE]", out.getvalue())

    def test_packaged_bundle_is_valid_redacted_and_not_qualifying(self):
        bundle, check = demo.load_bundle(demo.DEFAULT_BUNDLE)
        self.assertTrue(check["valid"], check)
        self.assertEqual(bundle["schema"], demo.REPLAY_SCHEMA)
        self.assertEqual(bundle["meta"]["provenance"], "LIVE_CAPTURE")
        self.assertFalse(bundle["meta"]["qualifying"])
        self.assertTrue(bundle["meta"]["contentRedacted"])
        raw = demo.DEFAULT_BUNDLE.read_text(encoding="utf-8")
        golden = json.loads((REPO_ROOT / "evals" / "lear" / "golden_lear_v1.json").read_text(encoding="utf-8"))["cases"]
        leaked = [c["id"] for c in golden if c["question"] in raw]
        self.assertEqual(leaked, [])  # no evaluation question ships in the wheel
        self.assertNotIn(str(Path.home()), raw)
        s06 = next(s for s in bundle["scenes"] if s["id"] == "S06")
        ttft = next(f for f in s06["facts"] if f["k"].startswith("model TTFT"))
        self.assertEqual(ttft["p"], "UNAVAILABLE")

    def test_full_and_v1_bundles_in_the_repository_still_replay(self):
        for path in (demo.FULL_BUNDLE, REPO_ROOT / "docs" / "lear_agent" / "evidence" / "demo" / "lear_demo_replay_v1.json"):
            bundle, check = demo.load_bundle(path)
            self.assertTrue(check["valid"], (path, check))
            out = _Stream(False)
            self.assertEqual(demo.run_demo(_args(mode="replay", json=False, bundle=str(path)), _settings(tempfile.mkdtemp()), out=out), 0)
            self.assertNotIn("visual unavailable", out.getvalue())

    def test_sanitize_redacts_secrets_and_scratch_paths(self):
        scratch = Path("/tmp/lear-demo-xyz")
        clean = demo._sanitize({"a": f"{scratch}/data key sk-abcdefghijklmnopqrstuvwx", "_raw": "dropped"}, scratch)
        self.assertEqual(clean, {"a": "<scratch>/data key [REDACTED]"})


class LiveBaselineTests(unittest.TestCase):
    def test_examiner_json_runs_the_real_pipeline_in_scratch_only(self):
        tmp = tempfile.mkdtemp()
        settings = _settings(tmp)
        out = io.StringIO()
        code = demo.run_demo(_args(scene="0,4,6,8"), settings, out=out)
        raw = out.getvalue()
        self.assertIsNone(ANSI.search(raw))
        data = json.loads(raw)
        self.assertEqual(code, 0, json.dumps([s.get("steps") for s in data["scenes"]], indent=1)[:3000])
        scenes = {s["id"]: s for s in data["scenes"]}
        self.assertEqual(list(scenes), ["S00", "S04", "S06", "S08"])
        # the user's data dir is never created or touched
        self.assertFalse((Path(tmp) / "user-data").exists())
        # citations map to real manifest entries and retrieved chunks
        cites = scenes["S06"]["data"]["citations"]
        self.assertTrue(cites)
        self.assertTrue(all(c["inManifest"] and c["retrieved"] for c in cites))
        # no fake model timings on the baseline
        ttft = next(f for f in scenes["S06"]["facts"] if f["k"].startswith("model TTFT"))
        self.assertEqual(ttft["p"], "UNAVAILABLE")
        self.assertEqual(next(f for f in scenes["S06"]["facts"] if f["k"] == "generator")["p"], "NOT QUALIFYING")
        # guardrail steps preserve the real exit codes
        observed = {s["name"]: s for s in scenes["S08"]["steps"]}
        self.assertEqual(observed["second request in a 1-per-minute session window"]["observed"], "exit 7 (rate limited)")
        self.assertEqual(observed["ollama backend without a reachable model"]["observed"], "exit 6 (dependency unavailable)")
        self.assertTrue(any(s["observed"].startswith("exit 3") for s in scenes["S08"]["steps"]))
        self.assertTrue(any("quarantined" in str(s["observed"]) for s in scenes["S08"]["steps"]))

    def test_live_mode_stops_at_the_model_gate_with_exit_6(self):
        tmp = tempfile.mkdtemp()
        settings = _settings(tmp, backend="ollama", ollama_url="http://127.0.0.1:9")
        out = io.StringIO()
        code = demo.run_demo(_args(mode="live"), settings, out=out)
        data = json.loads(out.getvalue())
        self.assertEqual(code, 6)
        self.assertEqual([s["id"] for s in data["scenes"]], ["S00", "S01", "S02", "S03"])
        self.assertTrue(data["scenes"][-1]["blocked"])

    def test_cli_parser_accepts_demo(self):
        from ragformation.cli import build_parser

        args = build_parser().parse_args(["lear", "demo", "examiner", "--plain", "--scene", "0"])
        self.assertEqual((args.lear_command, args.mode, args.plain, args.scene), ("demo", "examiner", True, "0"))



def _scratch_settings(tmp: str) -> LearSettings:
    return dataclasses.replace(_settings(tmp), data_dir=Path(tmp) / "scratch-data", agent_db_path=None)


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        h.update(str(path.relative_to(root)).encode())
        if path.is_file():
            h.update(path.read_bytes())
    return h.hexdigest()


class LifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ragformation.lear import indexer
        from ragformation.lear.agent import LearAgent

        cls.tmp = tempfile.mkdtemp(prefix="lear-test-")
        cls.settings = _scratch_settings(cls.tmp)
        cls.agent = LearAgent(cls.settings)
        indexer.rebuild(cls.agent.engine, cls.settings)

    @classmethod
    def tearDownClass(cls):
        cls.agent.close()

    def test_spans_are_contiguous_monotonic_and_tile_the_request(self):
        answer = self.agent.ask(demo.GOLDEN_FALLBACK["lear-012"]["question"], user_id="t", session_id="life-1")
        life = answer["timings"]["lifecycle"]
        self.assertEqual(life["schema"], "lear-lifecycle-v1")
        names = [x["stage"] for x in life["stages"]]
        self.assertEqual(names, ["validate", "admission", "session", "compaction", "rewrite", "memory", "prompt",
                                 "engine.open", "fingerprint", "scopeGuard", "engine", "mapping", "persist"])
        previous_end = 0.0
        for x in life["stages"]:
            self.assertGreaterEqual(x["ms"], 0.0)
            self.assertGreaterEqual(x["startMs"] + 0.01, previous_end)
            self.assertLessEqual(abs(x["startMs"] - previous_end), 0.01)
            previous_end = x["startMs"] + x["ms"]
        self.assertLessEqual(abs(sum(x["ms"] for x in life["stages"]) - life["totalMs"]), 0.1)
        self.assertLessEqual(answer["timings"]["totalMs"], life["totalMs"] + 0.01)

    def test_scope_abstention_has_a_scope_guard_span_but_no_engine_span(self):
        answer = self.agent.ask(demo.GOLDEN_FALLBACK["lear-068"]["question"], user_id="t")
        self.assertEqual(answer["status"], "ABSTAINED")
        names = [x["stage"] for x in answer["timings"]["lifecycle"]["stages"]]
        self.assertIn("scopeGuard", names)
        self.assertNotIn("engine", names)

    def test_selected_ids_are_identical_with_and_without_demo_diagnostics(self):
        question = demo.GOLDEN_FALLBACK["lear-012"]["question"]
        plain = self.agent.ask(question, user_id="t2", save=False)["retrieval"]["selected"]
        again = self.agent.ask(question, user_id="t3", save=False)["retrieval"]["selected"]
        out = io.StringIO()
        demo.run_demo(_args(scene="5"), _settings(tempfile.mkdtemp()), out=out)
        s05 = json.loads(out.getvalue())["scenes"][0]
        shown = [c["chunkId"] for c in s05["data"]["candidates"] if c["selected"]]
        self.assertEqual(plain, again)
        self.assertEqual(sorted(plain), sorted(shown))
        self.assertEqual(s05["data"]["context"] and [c for c, _ in s05["data"]["context"]], plain)


class SafetyTests(unittest.TestCase):
    def test_scratch_deletion_refuses_anything_but_lear_demo_temp_dirs(self):
        ok = Path(tempfile.mkdtemp(prefix="lear-demo-"))
        other = Path(tempfile.mkdtemp(prefix="keep-me-"))
        nested = Path(tempfile.mkdtemp(prefix="outer-")) / "lear-demo-nested"
        nested.mkdir()
        link = Path(tempfile.gettempdir()) / f"lear-demo-link-{os.getpid()}"
        link.symlink_to(other, target_is_directory=True)
        try:
            self.assertTrue(demo.safe_scratch(ok))
            self.assertFalse(demo.safe_scratch(other))
            self.assertFalse(demo.safe_scratch(nested))
            self.assertFalse(demo.safe_scratch(link))
            self.assertFalse(demo.safe_scratch(Path(tempfile.gettempdir()) / "lear-demo-missing"))
        finally:
            link.unlink()

    def test_permanent_data_directory_is_byte_identical_after_a_live_demo(self):
        import sqlite3

        tmp = Path(tempfile.mkdtemp())
        permanent = tmp / "permanent-data"
        (permanent / "lear").mkdir(parents=True)
        (permanent / "lear" / "notes.txt").write_text("user data", encoding="utf-8")
        con = sqlite3.connect(permanent / "lear" / "agent.db")
        con.execute("create table t (x)")
        con.execute("insert into t values (1)")
        con.commit()
        con.close()
        before = _tree_hash(permanent)
        settings = LearSettings.from_env(backend="baseline", data_dir=permanent, docs_dir=DOCS)
        out = io.StringIO()
        demo.run_demo(_args(scene="2,4,7"), settings, out=out)
        self.assertEqual(json.loads(out.getvalue())["exitCode"], 0)
        self.assertEqual(_tree_hash(permanent), before)

    def test_capture_is_atomic_redacted_by_default_and_full_on_request(self):
        tmp = Path(tempfile.mkdtemp())
        question = demo.GOLDEN_FALLBACK["lear-012"]["question"]
        for full in (False, True):
            target = tmp / f"bundle-{full}.json"
            out = io.StringIO()
            code = demo.run_demo(_args(mode="capture", json=False, scene="0,6", out=str(target), full=full), _settings(str(tmp)), out=out)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue())["contentRedacted"], not full)
            raw = target.read_text(encoding="utf-8")
            bundle, check = demo.load_bundle(target)
            self.assertTrue(check["valid"], check)
            self.assertEqual(question in raw, full)
            self.assertEqual("[REDACTED question lear-012" in raw, not full)
            for key in demo.REQUIRED_META_V2:
                self.assertIn(key, bundle["meta"])
        self.assertEqual(sorted(p.name for p in tmp.iterdir() if p.name.startswith(".")), [])  # no temp files left

    def test_atomic_write_keeps_the_old_bundle_when_the_replace_fails(self):
        target = Path(tempfile.mkdtemp()) / "b.json"
        target.write_text("old", encoding="utf-8")
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                demo.write_atomic(target, "new")
        self.assertEqual(target.read_text(encoding="utf-8"), "old")
        self.assertEqual([p.name for p in target.parent.iterdir()], ["b.json"])


@unittest.skipUnless(sys.platform != "win32" and hasattr(os, "fork"), "needs a POSIX pty")
class PtyKeyTests(unittest.TestCase):
    """Drive the real director in a pseudo-terminal: keys, arrows, Ctrl-C, terminal restore."""

    PROMPT = demo.CONTROLS.encode()

    def _spawn(self, scenes: str = "0,1,9"):
        import pty

        tmp = tempfile.mkdtemp()
        cmd = [sys.executable, "-m", "ragformation", "lear", "--backend", "baseline", "--data-dir", str(Path(tmp) / "d"),
               "--docs-dir", str(DOCS), "demo", "director", "--speed", "0", "--scene", scenes]
        pid, fd = pty.fork()
        if pid == 0:  # child: a real controlling terminal
            os.environ.update({"TERM": "xterm-256color", "COLUMNS": "110", "LINES": "40", "PYTHONPATH": str(REPO_ROOT)})
            os.chdir(str(REPO_ROOT))
            os.execv(sys.executable, cmd)
        return pid, fd

    def _read_until(self, fd: int, buf: bytearray, count: int, timeout: float = 90.0) -> None:
        deadline = time.time() + timeout
        while buf.count(self.PROMPT) < count and time.time() < deadline:
            if select.select([fd], [], [], 0.2)[0]:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    return
                if not chunk:
                    return
                buf.extend(chunk)

    def _finish(self, pid: int, fd: int, buf: bytearray) -> int:
        deadline = time.time() + 60
        while time.time() < deadline:
            done, status = os.waitpid(pid, os.WNOHANG)
            if select.select([fd], [], [], 0.1)[0]:
                try:
                    chunk = os.read(fd, 65536)
                    buf.extend(chunk)
                except OSError:
                    pass
            if done:
                os.close(fd)
                return os.waitstatus_to_exitcode(status)
        os.kill(pid, 9)
        os.waitpid(pid, 0)
        self.fail("demo did not exit")

    @staticmethod
    def _headers(buf: bytearray):
        text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", buf.decode("utf-8", "replace"))
        return re.findall(r"(S\d\d)  [A-Z][^\n]*?\[(?:LIVE|RECORDED|UNAVAILABLE|ILLUSTRATIVE|BLOCKED)", text)

    def test_keys_navigate_forward_back_arrows_digits_and_quit(self):
        pid, fd = self._spawn()
        buf = bytearray()
        for n, key in enumerate([b"n", b"b", b"\x1b[C", b"9", b"q"], start=1):
            self._read_until(fd, buf, n)
            os.write(fd, key)
        code = self._finish(pid, fd, buf)
        self.assertEqual(code, 0, buf[-400:])
        self.assertEqual(self._headers(buf), ["S00", "S01", "S00", "S01", "S09"])
        self.assertTrue(bytes(buf).rstrip(b"\r\n").endswith(RESET.encode()), buf[-80:])

    def test_ctrl_c_exits_130_and_restores_the_terminal(self):
        pid, fd = self._spawn("0,1")
        buf = bytearray()
        self._read_until(fd, buf, 1)
        os.write(fd, b"\x03")
        code = self._finish(pid, fd, buf)
        self.assertEqual(code, 130, buf[-400:])
        self.assertIn(b"interrupted", bytes(buf))
        self.assertIn(RESET.encode(), bytes(buf[-200:]))


if __name__ == "__main__":
    unittest.main()
