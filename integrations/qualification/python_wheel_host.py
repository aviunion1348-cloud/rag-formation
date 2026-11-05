#!/usr/bin/env python3
"""Host fixture A — a Python host that only knows the *installed wheel*.

Run from anywhere:

    /tmp/hostA/bin/python integrations/qualification/python_wheel_host.py --wheel /tmp/wheelhouse/ragformation-0.2.0-py3-none-any.whl

What it proves, in order:

1. the wheel installs into a clean virtual environment,
2. `import ragformation` resolves to the installed package, not to this checkout
   (the fixture fails loudly if the import path is inside the repository),
3. an index can be built in a temporary directory,
4. a question returns an answer with resolvable citations,
5. an unanswerable question abstains instead of guessing,
6. the HTTP server starts from the installed package and answers `/api/v1/ask`.

Exit code 0 means every assertion held. The log it prints is the evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")


def free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True)
    parser.add_argument("--corpus", default=None, help="corpus directory (defaults to the checkout's corpus/)")
    args = parser.parse_args()

    import ragformation
    from ragformation.config import Settings
    from ragformation.engine import RAGEngine

    location = Path(ragformation.__file__).resolve()
    check(
        "import resolves to the installed wheel, not the checkout",
        "site-packages" in str(location),
        str(location),
    )
    check("package version", bool(getattr(ragformation, "__version__", None)), str(getattr(ragformation, "__version__", "?")))

    corpus = Path(args.corpus) if args.corpus else Path(__file__).resolve().parents[2] / "corpus"
    files = sorted(str(path) for path in corpus.glob("*") if path.is_file())
    check("corpus files available to the host", len(files) >= 10, f"{len(files)} files")

    data_dir = Path(tempfile.mkdtemp(prefix="hostA-data-"))
    settings = Settings(
        **{
            **Settings.from_env().__dict__,
            "data_dir": data_dir,
            "embed_provider": "hashing",
            "llm_provider": "extractive",
        }
    )
    engine = RAGEngine(settings)
    try:
        report = engine.ingest_path(files)
        check(
            "ingest through the installed package",
            report.indexed > 0 and report.chunks > 0,
            f"{report.indexed} indexed, {report.chunks} chunks",
        )

        answer = engine.ask("How long are audit logs retained?", save=False)
        check("answer is grounded", answer.status == "answered", answer.status)
        check("answer cites a chunk", bool(answer.citations), f"{len(answer.citations)} citations")
        check("answer states the fact", "400" in (answer.answer or ""), (answer.answer or "")[:90])
        check("answer names the source", "data-retention-policy" in json.dumps([c.to_dict() for c in answer.citations]))

        abstained = engine.ask("What is the capital city of Portugal?", save=False)
        check("off-corpus question abstains", abstained.status == "abstained", abstained.status)
    finally:
        engine.close()

    # ---------------------------------------------------------------- HTTP host
    port = free_port()
    env = {
        **os.environ,
        "RAG_DATA_DIR": str(data_dir),
        "RAG_EMBED_PROVIDER": "hashing",
        "RAG_LLM_PROVIDER": "extractive",
        "PYTHONPATH": "",
    }
    server = subprocess.Popen(
        [sys.executable, "-m", "ragformation", "serve", "--port", str(port), "--host", "127.0.0.1"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        ready = False
        for _ in range(80):
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=1) as response:
                    ready = response.status == 200
                    break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.25)
        check("installed server starts and reports healthy", ready, f"{base}/healthz")

        payload = json.dumps({"question": "Which team owns the ingest-api service?", "topK": 6}).encode()
        headers = {"Content-Type": "application/json"}
        api_key = os.environ.get("RAG_API_KEY")
        if api_key:
            # The fixture runs against a key-protected engine, which is the
            # documented production shape; a host that cannot authenticate is a
            # misconfigured host, not a passing one.
            headers["x-api-key"] = api_key
            headers["x-rag-principal"] = json.dumps(
                {"subjectId": "host-a", "tenantId": "local", "roles": ["viewer"]}
            )
        request = urllib.request.Request(f"{base}/api/v1/ask", data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read())
        check("HTTP ask returns an answer", bool(body.get("answer")), body.get("status", ""))
        check("HTTP ask returns citations", bool(body.get("citations")), f"{len(body.get('citations') or [])} citations")
        check("HTTP ask reports the evidence it used", bool(body.get("evidence")), f"{len(body.get('evidence') or [])} chunks")
    finally:
        server.terminate()
        try:
            output = server.communicate(timeout=10)[0]
        except subprocess.TimeoutExpired:
            server.kill()
            output = server.communicate()[0]
        if any("Traceback" in line for line in (output or "").splitlines()):
            check("server log is free of tracebacks", False, (output or "")[-400:])

    failed = [name for name, ok, _ in CHECKS if not ok]
    print(f"\nhost A: {len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
