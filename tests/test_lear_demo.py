"""Tests for ``lear demo``: terminal capabilities, provenance, replay integrity,
exit-code preservation, and the live baseline scenes. No Ollama is needed."""

from __future__ import annotations

import argparse
import copy
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ragformation.lear import demo
from ragformation.lear.demo_render import EFFECTS, RESET, Renderer, TermCaps, detect_caps
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
                out=None, speed=0.0, auto=True, keep_scratch=False)
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

    def test_effect_library_is_documented_and_unique(self):
        names = [e.name for e in EFFECTS]
        self.assertEqual(len(names), len(set(names)))
        for e in EFFECTS:
            for field in (e.family, e.trigger, e.grammar, e.purpose, e.reduced, e.data):
                self.assertTrue(field)
        teletype = next(e for e in EFFECTS if e.name == "teletype")
        self.assertIn("never for extractive", teletype.data)

    def test_effects_json(self):
        out = io.StringIO()
        code = demo.run_demo(_args(mode="effects"), _settings(tempfile.mkdtemp()), out=out)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["count"], len(EFFECTS))


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

    def test_packaged_bundle_is_valid_and_not_qualifying(self):
        bundle, check = demo.load_bundle(demo.DEFAULT_BUNDLE)
        self.assertTrue(check["valid"], check)
        self.assertEqual(bundle["meta"]["provenance"], "LIVE_CAPTURE")
        self.assertFalse(bundle["meta"]["qualifying"])
        s06 = next(s for s in bundle["scenes"] if s["id"] == "S06")
        ttft = next(f for f in s06["facts"] if f["k"].startswith("model TTFT"))
        self.assertEqual(ttft["p"], "UNAVAILABLE")

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
        self.assertIn("NOT QUALIFYING", next(f for f in scenes["S06"]["facts"] if f["k"] == "generator")["v"])
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


if __name__ == "__main__":
    unittest.main()
