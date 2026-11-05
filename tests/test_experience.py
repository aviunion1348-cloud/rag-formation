"""The S3 Experience: assets, contracts, offline behaviour and HTTP wiring.

Two layers of checks live here:

* static contract checks (always run) — the files exist, the DOM ids the scripts
  look up exist, every formation the scene script names is implemented, every
  shader attribute the renderer binds is declared, the recording is labelled as
  a demo, and nothing reaches out to a third-party host;
* the JavaScript contract test (``tests/experience_contract.mjs``) which is run
  through Node when Node is available.

What is *not* verified here, and must not be claimed: that WebGL shaders compile
on a GPU, or that the film looks right. There is no browser in this environment;
``scripts/experience_smoke.mjs`` covers the DOM path under jsdom when it is
installed, and the WebGL path is explicitly listed as unverified in
``docs/experience.md``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from helpers import REPO_ROOT, TempDirTestCase

from ragformation.api import Api
from ragformation.engine import RAGEngine
from ragformation.api import RequestContext

EXPERIENCE = REPO_ROOT / "ragformation" / "web" / "experience"
JS = EXPERIENCE / "js"

SCENE_KINDS = None  # filled lazily from scenes.js


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class ExperienceAssetsTests(unittest.TestCase):
    def test_required_files_exist(self):
        for name in ("index.html", "styles.css", "package.json", "demo_trace.json"):
            self.assertTrue((EXPERIENCE / name).is_file(), f"missing {name}")
        for name in ("main.js", "renderer.js", "scenes.js", "timeline.js", "events.js", "explorer.js"):
            self.assertTrue((JS / name).is_file(), f"missing js/{name}")
        # The fixture is generated, not hand-written: keep the recorder with it.
        self.assertTrue((REPO_ROOT / "scripts" / "record_experience_demo.py").is_file())

    def test_dom_ids_used_by_scripts_exist_in_markup(self):
        html = read(EXPERIENCE / "index.html")
        html_ids = set(re.findall(r'id="([^"]+)"', html))
        used = set(re.findall(r'\$\("([^"]+)"\)', read(JS / "main.js")))
        used |= set(re.findall(r'getElementById\("([^"]+)"\)', read(JS / "main.js")))
        missing = sorted(used - html_ids)
        self.assertFalse(missing, f"main.js looks up ids that are not in index.html: {missing}")

    def test_scripts_reference_no_third_party_hosts(self):
        pattern = re.compile(r"""(?:src|href)\s*=\s*["']https?://""", re.IGNORECASE)
        for path in [EXPERIENCE / "index.html", EXPERIENCE / "styles.css", *JS.glob("*.js")]:
            self.assertIsNone(pattern.search(read(path)), f"{path.name} loads a remote asset")

    def test_styles_honour_reduced_motion_and_are_self_contained(self):
        css = read(EXPERIENCE / "styles.css")
        self.assertIn("prefers-reduced-motion", css)
        self.assertNotIn("@import", css)
        self.assertNotIn("url(http", css.replace(" ", ""))
        # Focus visibility is part of accessibility, not decoration.
        self.assertIn(":focus-visible", css)

    def test_scene_script_is_a_complete_sixteen_scene_film(self):
        source = read(JS / "scenes.js")
        ids = re.findall(r'^\s{4}id: "([a-z-]+)"', source, re.MULTILINE)
        self.assertEqual(len(ids), 16, f"expected 16 scenes, found {len(ids)}: {ids}")
        self.assertEqual(len(set(ids)), 16, "scene ids must be unique")
        numbers = re.findall(r'^\s{4}number: "(\d\d)"', source, re.MULTILINE)
        self.assertEqual(numbers, [f"{i:02d}" for i in range(16)])

        kinds = set(re.findall(r'formation: \{ kind: "([a-z0-9]+)"', source))
        renderer = read(JS / "renderer.js")
        implemented = set(re.findall(r"^  ([a-z]+)\(count, random", renderer, re.MULTILINE))
        missing = sorted(kinds - implemented)
        self.assertFalse(missing, f"formations used by scenes but not implemented: {missing}")
        # A dead formation would be untested code.
        self.assertFalse(sorted(implemented - kinds), "unused formation builders in renderer.js")

    def test_scenes_use_real_event_stage_names(self):
        """Live mode can only follow stages the engine actually publishes."""

        engine_source = read(REPO_ROOT / "ragformation" / "engine.py")
        published = {
            stage.replace("_", ".")
            for stage in re.findall(
                r'"(ingestion[._][a-z_.]+|retrieval[._][a-z_.]+|query[._][a-z_.]+|source\.[a-z_]+|index\.[a-z_]+)"',
                engine_source,
            )
        }
        published |= {
            "abstention",
            "citation.validation",
            "context.assembly",
            "generation",
            "guardrail.scan",
            "response.ready",
        }
        scenes = read(JS / "scenes.js")
        stage_arrays = " ".join(re.findall(r"stages: \[([^\]]*)\]", scenes))
        declared = set(re.findall(r'"(ingestion\.[a-z_.]+|retrieval\.[a-z_.]+|query\.[a-z_.]+)"', stage_arrays))
        unknown = sorted(declared - published)
        self.assertFalse(unknown, f"scenes reference stages the engine never publishes: {unknown}")

    def test_renderer_shader_contract(self):
        """Every attribute/uniform the JS binds must be declared in both shaders."""

        source = read(JS / "renderer.js")
        bound = set(re.findall(r'attrib\("(a_[a-zA-Z]+)"\)', source))
        bound |= set(re.findall(r'getUniformLocation\(program, "(u_[a-zA-Z]+)"\)', source))
        self.assertGreaterEqual(len(bound), 6, "renderer binds suspiciously few shader slots")

        for marker in ("VERTEX_300", "VERTEX_100", "FRAGMENT_300", "FRAGMENT_100"):
            block = re.search(rf"const {marker} = `(.*?)`;", source, re.S)
            self.assertIsNotNone(block, f"{marker} missing")
        vertex = re.search(r"const VERTEX_300 = `(.*?)`;", source, re.S).group(1)
        fragment = re.search(r"const FRAGMENT_300 = `(.*?)`;", source, re.S).group(1)
        legacy_vertex = re.search(r"const VERTEX_100 = `(.*?)`;", source, re.S).group(1)

        for name in bound:
            if name.startswith("a_"):
                self.assertIn(name, vertex, f"{name} not declared in the WebGL2 vertex shader")
                self.assertIn(name, legacy_vertex, f"{name} not declared in the WebGL1 vertex shader")
            else:
                self.assertIn(name, vertex, f"{name} not declared in the vertex shader")
        self.assertIn("out vec4 outColour", fragment)
        self.assertIn("gl_FragColor", re.search(r"const FRAGMENT_100 = `(.*?)`;", source, re.S).group(1))

    def test_demo_fixture_is_honestly_labelled(self):
        demo = json.loads(read(EXPERIENCE / "demo_trace.json"))
        self.assertEqual(demo["kind"], "demo")
        labelling = demo["labelling"]
        self.assertEqual(labelling["mode"], "demo")
        self.assertIn("recorded", labelling["note"].lower())
        self.assertIn("no language model", labelling["note"].lower())
        # Hashing must never be presented as semantic.
        self.assertFalse(labelling["semantic"])
        self.assertEqual(labelling["embeddingProvider"], "hashing")
        self.assertEqual(labelling["generationProvider"], "extractive")
        self.assertEqual(demo["summary"]["status"], demo["status"])
        self.assertTrue(demo["events"], "fixture has no events")
        for event in demo["events"]:
            self.assertIn("stage", event)
            self.assertRegex(event["stage"], r"^[a-z][a-z_.]*$")
            self.assertIn("sequence", event)
            self.assertIn("traceId", event)
        stages = {event["stage"] for event in demo["events"]}
        for required in ("ingestion.source.accepted", "ingestion.chunking", "retrieval.semantic", "response.ready"):
            self.assertIn(required, stages)

    def test_transcript_has_text_for_screen_readers(self):
        source = read(JS / "scenes.js")
        captions = re.findall(r'caption:\s*\n?\s*"([^"]{40,})"', source)
        self.assertGreaterEqual(len(captions), 16)
        html = read(EXPERIENCE / "index.html")
        self.assertIn('aria-live="polite"', html)
        self.assertIn('class="skip-link"', html)
        self.assertIn('id="transcript"', html)

    def test_page_documents_its_own_fallbacks(self):
        main = read(JS / "main.js")
        renderer = read(JS / "renderer.js")
        self.assertIn("canvas2d", renderer)
        self.assertIn("no-eventsource", read(JS / "events.js"))
        self.assertIn("reducedMotion", main)
        html = read(EXPERIENCE / "index.html")
        self.assertIn("<noscript>", html)


class ExperienceNodeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_javascript_contract(self):
        script = REPO_ROOT / "tests" / "experience_contract.mjs"
        result = subprocess.run(
            ["node", str(script)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(REPO_ROOT),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_modules_are_es_modules(self):
        marker = json.loads(read(EXPERIENCE / "package.json"))
        self.assertEqual(marker["type"], "module")


class EventsRecentEndpointTests(TempDirTestCase):
    """The polling fallback used when EventSource is unavailable."""

    def setUp(self):
        super().setUp()
        self.engine = RAGEngine(self.settings)
        self.addCleanup(self.engine.close)
        self.engine.ingest_bytes(
            "policy.md",
            b"# Retention\n\nAccess review evidence is retained for 3 years in the archive.\n",
        )
        self.api = Api(self.engine, self.settings)
        self.engine.ask("How long is access review evidence retained?")

    def _get(self, since: int = 0):
        ctx = RequestContext(query={"since": str(since)})
        return self.api.dispatch("GET", "/api/v1/events/recent", {}, ctx=ctx)

    def test_returns_history_with_contract_metadata(self):
        status, payload = self._get()
        self.assertEqual(status, 200)
        self.assertEqual(payload["contract"], "ragformation.events")
        self.assertIn("eventVersion", payload)
        self.assertGreater(payload["count"], 0)
        self.assertEqual(payload["count"], len(payload["events"]))
        stages = [event["stage"] for event in payload["events"]]
        self.assertIn("query.received", stages)
        # Sequences are per trace (see docs/events.md): each trace must be
        # contiguous from 1 so a client can detect a gap within that trace.
        per_trace = {}
        for event in payload["events"]:
            per_trace.setdefault(event["traceId"], []).append(event["sequence"])
        for trace_id, sequences in per_trace.items():
            self.assertEqual(sequences, list(range(1, len(sequences) + 1)), trace_id)

    def test_since_filters_out_old_events(self):
        status, first = self._get(0)
        self.assertEqual(status, 200)
        last = max(event["sequence"] for event in first["events"])
        status, second = self._get(last)
        self.assertEqual(status, 200)
        # `since` is a per-trace resume point: nothing may repeat.
        first_ids = {event["eventId"] for event in first["events"]}
        self.assertFalse(first_ids & {event["eventId"] for event in second["events"]})

    def test_bad_since_is_treated_as_zero(self):
        ctx = RequestContext(query={"since": "not-a-number"})
        status, payload = self.api.dispatch("GET", "/api/v1/events/recent", {}, ctx=ctx)
        self.assertEqual(status, 200)
        self.assertGreater(payload["count"], 0)


if __name__ == "__main__":
    unittest.main()
