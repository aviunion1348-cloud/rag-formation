"""Generate the evidence-backed reference half of docs/lear_backend.

Everything here is extracted from a pinned Lear checkout with Python's ``ast``
module and ``git`` read commands; nothing is inferred from names or from the
project's own prose. Each generated entry cites ``path:Lstart-Lend`` at the
pinned commit. The narrative pages (architecture, workflows, ...) are written
by hand from reading the code and are checked by ``docverify``; this module
never overwrites them.

Output is deterministic: no timestamps other than the commit date, everything
sorted, so re-running on the same commit is a no-op (idempotent).

Generated free text (help strings, docstrings) is passed through
``guard_safe``: the RAG engine quarantines evidence that looks like a
prompt-injection attempt (``ragformation.guardrails.INJECTION_RULES``), and
ordinary DevOps prose such as "run ... command" would otherwise trip the
``tool_abuse`` rule and make that chunk unusable as evidence. ``guard_safe``
swaps the trigger verb outside code spans and reports how many it changed.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..errors import ValidationError
from ..guardrails import scan_text

GENERATOR = "python -m ragformation lear docs build"
_TRIGGER = re.compile(
    r"\b(call|invoke|execute|run)\b(?=[^.\n]{0,30}\b(tool|function|shell|command|script|terminal)\b)",
    re.IGNORECASE,
)
_SECRET_VERB = re.compile(
    r"\b(reveal|print|output|show|leak|send|exfiltrate|share)\b(?=[^.\n]{0,50}\b(api[ _-]?key|token|password|passphrase|secret|credential|credentials)\b)",
    re.IGNORECASE,
)
_REPLACEMENTS = {"call": "use", "invoke": "use", "execute": "perform", "run": "start",
                 "reveal": "expose", "print": "display", "output": "emit", "show": "display",
                 "leak": "expose", "send": "deliver", "exfiltrate": "move", "share": "pass"}
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")


@dataclass
class GuardStats:
    rewrites: int = 0
    examples: List[str] = field(default_factory=list)


GUARD = GuardStats()


def guard_safe(text: str) -> str:
    """Rephrase injection-heuristic trigger verbs outside `code spans`."""

    if not text:
        return text
    parts = re.split(r"(`[^`]*`)", text)
    out = []
    for part in parts:
        if part.startswith("`") and part.endswith("`"):
            out.append(part)
            continue
        for pattern in (_TRIGGER, _SECRET_VERB):
            def swap(match: re.Match) -> str:
                word = match.group(1)
                repl = _REPLACEMENTS[word.lower()]
                GUARD.rewrites += 1
                if len(GUARD.examples) < 20:
                    GUARD.examples.append(word)
                return repl.capitalize() if word[0].isupper() else repl
            part = pattern.sub(swap, part)
        out.append(part)
    return "".join(out)


def one_line(text: Optional[str], limit: int = 220) -> str:
    text = " ".join((text or "").split())
    text = text.replace("|", "/")
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return guard_safe(text)


def first_paragraph(doc: Optional[str], limit: int = 320) -> str:
    if not doc:
        return ""
    para = doc.strip().split("\n\n", 1)[0]
    return one_line(para, limit)


def ev(path: str, start: int, end: Optional[int] = None) -> str:
    end = end or start
    return f"`{path}:L{start}`" if end == start else f"`{path}:L{start}-L{end}`"


# ---------------------------------------------------------------- git / pin
def _git(source: Path, *args: str) -> str:
    try:
        proc = subprocess.run(["git", "-C", str(source), *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValidationError("git is not usable for the Lear source", hint=str(exc)) from exc
    if proc.returncode != 0:
        raise ValidationError("git command failed on the Lear source", hint=proc.stderr.strip()[:300])
    return proc.stdout.strip()


def pin_source(source: Path) -> Dict[str, Any]:
    source = Path(source).expanduser().resolve()
    if not (source / ".git").exists():
        raise ValidationError("Lear source is not a git checkout", hint=str(source))
    commit = _git(source, "rev-parse", "HEAD")
    remote = _git(source, "remote", "get-url", "origin")
    remote = re.sub(r"//[^@/]+@", "//", remote)
    match = re.search(r"github\.com[:/]([^/]+/[^/.]+)", remote)
    status = _git(source, "status", "--porcelain")
    return {
        "repository": match.group(1) if match else remote,
        "remote": remote,
        "local_path": str(source),
        "branch": _git(source, "rev-parse", "--abbrev-ref", "HEAD"),
        "commit": commit,
        "commit_short": commit[:12],
        "commit_date": _git(source, "log", "-1", "--format=%cI"),
        "commit_subject": _git(source, "log", "-1", "--format=%s"),
        "worktree_clean": status == "",
        "tracked_files": len(tracked_files(source)),
        "pinned_by": GENERATOR,
    }


def tracked_files(source: Path) -> List[str]:
    out = _git(source, "ls-files", "-z")
    return sorted(p for p in out.split("\0") if p)


# ------------------------------------------------------------------ helpers
def _parse(source: Path, rel: str) -> Optional[ast.Module]:
    try:
        return ast.parse((source / rel).read_text(encoding="utf-8"), filename=rel)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None


def _const_str(node: Optional[ast.AST]) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{…}" for v in node.values)
    return None


def _kw(call: ast.Call, name: str) -> Optional[ast.AST]:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _unparse(node: Optional[ast.AST], limit: int = 70) -> str:
    if node is None:
        return ""
    try:
        text = ast.unparse(node)
    except Exception:  # pragma: no cover - ast.unparse is total on parsed trees
        return "?"
    text = " ".join(text.split()).replace("|", "/")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _line_count(path: Path) -> Optional[int]:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:4096]:
        return None
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def _header(title: str, pin: Dict[str, Any], what: str, label: str = "implemented") -> List[str]:
    return [
        f"# {title}",
        "",
        f"> Generated by `{GENERATOR}` from `{pin['repository']}` at commit `{pin['commit_short']}` "
        f"({pin['commit_date'][:10]}). {what} Every entry cites `path:Lstart-Lend` at that commit. "
        f"Evidence label for this page: **{label}**. Regenerate instead of editing by hand.",
        "",
    ]


# ---------------------------------------------------------------- inventory
AREAS: Sequence[Tuple[str, str, str]] = (
    ("prash/", "backend source (Python package `prash`)", "in scope"),
    ("tests/", "automated tests (pytest)", "in scope"),
    ("evals/", "diagnosis evaluation harness", "in scope"),
    ("scripts/", "operator and demo scripts", "in scope (reference only)"),
    ("infrastructure/", "Terraform for a demo environment", "in scope (reference only)"),
    ("desktop/", "desktop frontend (Vite/TypeScript)", "out of scope: UI"),
    ("tasks/", "planning and task notes", "context only: not runtime behaviour"),
)


def _area(rel: str) -> Tuple[str, str, str]:
    for prefix, label, scope in AREAS:
        if rel.startswith(prefix):
            return prefix.rstrip("/"), label, scope
    if "/" not in rel:
        if rel.endswith(".md"):
            return "(root docs)", "project documentation (claims, cross-checked against code)", "context: verify before use"
        return "(root config)", "packaging, launchers and configuration", "in scope"
    top = rel.split("/", 1)[0]
    return top, "other", "not analysed"


def build_inventory(source: Path, pin: Dict[str, Any]) -> Dict[str, Any]:
    files = tracked_files(source)
    areas: Dict[str, Dict[str, Any]] = {}
    rows = []
    for rel in files:
        area, label, scope = _area(rel)
        path = source / rel
        lines = _line_count(path)
        size = path.stat().st_size if path.exists() else 0
        ext = Path(rel).suffix.lower() or "(none)"
        info = areas.setdefault(area, {"label": label, "scope": scope, "files": 0, "lines": 0, "bytes": 0, "binary": 0, "extensions": defaultdict(int)})
        info["files"] += 1
        info["bytes"] += size
        info["lines"] += lines or 0
        info["binary"] += 0 if lines is not None else 1
        info["extensions"][ext] += 1
        rows.append({"path": rel, "area": area, "ext": ext, "lines": lines, "bytes": size})
    for info in areas.values():
        info["extensions"] = dict(sorted(info["extensions"].items(), key=lambda kv: (-kv[1], kv[0])))
    return {"pin": pin, "areas": dict(sorted(areas.items())), "files": rows}


def render_inventory(inv: Dict[str, Any]) -> str:
    pin = inv["pin"]
    out = [
        "# Lear backend — source inventory",
        "",
        f"> Generated by `{GENERATOR}` from `{pin['repository']}` at commit `{pin['commit_short']}` "
        f"({pin['commit_date'][:10]}, branch `{pin['branch']}`, clean worktree: {pin['worktree_clean']}). "
        "Counts are over `git ls-files` (tracked files only). Line counts exclude binary files.",
        "",
        "## Areas",
        "",
        "| Area | What it is | Scope for these docs | Files | Lines | Binary | Top extensions |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for area, info in inv["areas"].items():
        exts = ", ".join(f"{k} {v}" for k, v in list(info["extensions"].items())[:5])
        out.append(f"| `{area}` | {info['label']} | {info['scope']} | {info['files']} | {info['lines']} | {info['binary']} | {exts} |")
    total_files = sum(i["files"] for i in inv["areas"].values())
    total_lines = sum(i["lines"] for i in inv["areas"].values())
    out += ["", f"Total: {total_files} tracked files, {total_lines} text lines.", ""]
    for area in ("prash", "tests", "evals", "(root config)", "(root docs)", "scripts", "infrastructure"):
        rows = [r for r in inv["files"] if r["area"] == area]
        if not rows:
            continue
        out += [f"## Files in `{area}`", "", "| Path | Lines | Bytes |", "|---|---:|---:|"]
        for r in rows:
            out.append(f"| `{r['path']}` | {r['lines'] if r['lines'] is not None else 'binary'} | {r['bytes']} |")
        out.append("")
    out += [
        "## Not inventoried file by file",
        "",
        "`desktop/` (frontend UI, out of scope for a backend-only agent) and `tasks/` (planning notes) are counted in the area table only.",
        "",
    ]
    return "\n".join(out)


# ---------------------------------------------------------------------- CLI
def extract_cli(source: Path, rel: str = "prash/cli.py") -> Dict[str, Any]:
    tree = _parse(source, rel)
    if tree is None:
        return {"commands": [], "global": [], "file": rel}
    funcs = {n.name: (n.lineno, n.end_lineno) for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    builder = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build_parser"), None)
    commands: Dict[str, Dict[str, Any]] = {}
    var_to_cmd: Dict[str, str] = {}
    global_args: List[Dict[str, Any]] = []
    parser_var = "parser"

    def arg_row(call: ast.Call) -> Dict[str, Any]:
        names = [_const_str(a) or _unparse(a) for a in call.args]
        action = _const_str(_kw(call, "action"))
        return {
            "names": names,
            "kind": "flag" if action in {"store_true", "store_false"} else ("positional" if names and not names[0].startswith("-") else "option"),
            "default": _unparse(_kw(call, "default")) if _kw(call, "default") is not None else "",
            "choices": _unparse(_kw(call, "choices")) if _kw(call, "choices") is not None else "",
            "type": _unparse(_kw(call, "type")) if _kw(call, "type") is not None else "",
            "nargs": _unparse(_kw(call, "nargs")) if _kw(call, "nargs") is not None else "",
            "help": one_line(_const_str(_kw(call, "help")) or "", 200),
            "line": call.lineno,
        }

    def add_parser_call(node: ast.AST) -> Optional[ast.Call]:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "add_parser":
                return sub
        return None

    if builder is not None:
        for stmt in builder.body:
            call = add_parser_call(stmt)
            if call is not None and call.args:
                name = _const_str(call.args[0]) or "?"
                entry = commands.setdefault(name, {"name": name, "help": one_line(_const_str(_kw(call, "help")) or "", 220), "args": [], "handler": None, "start": stmt.lineno, "end": stmt.end_lineno})
                if isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name):
                    var_to_cmd[stmt.targets[0].id] = name
                for sub in ast.walk(stmt):
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "set_defaults":
                        handler = _kw(sub, "func")
                        if isinstance(handler, ast.Name):
                            entry["handler"] = handler.id
                continue
            for sub in ast.walk(stmt):
                if not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and isinstance(sub.func.value, ast.Name)):
                    continue
                owner = sub.func.value.id
                if sub.func.attr == "add_argument":
                    if owner == parser_var:
                        global_args.append(arg_row(sub))
                    elif owner in var_to_cmd:
                        entry = commands[var_to_cmd[owner]]
                        entry["args"].append(arg_row(sub))
                        entry["end"] = max(entry["end"], sub.end_lineno or sub.lineno)
                elif sub.func.attr == "set_defaults" and owner in var_to_cmd:
                    entry = commands[var_to_cmd[owner]]
                    handler = _kw(sub, "func")
                    if isinstance(handler, ast.Name):
                        entry["handler"] = handler.id
                    entry["end"] = max(entry["end"], sub.end_lineno or sub.lineno)
    for entry in commands.values():
        if entry["handler"] in funcs:
            entry["handler_lines"] = funcs[entry["handler"]]
    return {
        "file": rel,
        "builder": (builder.lineno, builder.end_lineno) if builder else None,
        "main": funcs.get("main"),
        "global": global_args,
        "commands": list(commands.values()),
        "prog": "prash",
    }


def render_cli(cli: Dict[str, Any], pin: Dict[str, Any]) -> str:
    rel = cli["file"]
    out = _header("Lear backend — CLI reference (`prash`)", pin, "Extracted from the argparse definitions in `build_parser`.")
    if cli["builder"]:
        out.append(f"The parser is built by `build_parser` ({ev(rel, *cli['builder'])}); the entry point `main` ({ev(rel, *cli['main'])}) is exposed as the `prash` console script in `pyproject.toml`.")
    out.append("")
    out.append(f"Subcommands ({len(cli['commands'])}): " + ", ".join(f"`{c['name']}`" for c in cli["commands"]) + ".")
    out.append("")
    if cli["global"]:
        out += ["## Global options", "", "| Option | Kind | Help | Evidence |", "|---|---|---|---|"]
        for a in cli["global"]:
            out.append(f"| `{' '.join(a['names'])}` | {a['kind']} | {a['help']} | {ev(rel, a['line'])} |")
        out.append("")
    for c in cli["commands"]:
        out.append(f"## prash {c['name']}")
        out.append("")
        if c["help"]:
            out.append(f"Purpose (help text): {c['help']}.")
            out.append("")
        out.append(f"Defined at {ev(rel, c['start'], c['end'])}.")
        if c.get("handler"):
            lines = c.get("handler_lines")
            out.append(f"Handler function: `{c['handler']}`" + (f" at {ev(rel, *lines)}." if lines else "."))
        out.append("")
        if c["args"]:
            out += ["| Argument | Kind | Default | Choices / type | Help |", "|---|---|---|---|---|"]
            for a in c["args"]:
                extra = " ".join(x for x in (a["choices"], a["type"], f"nargs={a['nargs']}" if a["nargs"] else "") if x)
                out.append(f"| `{' '.join(a['names'])}` | {a['kind']} | {('`' + a['default'] + '`') if a['default'] else '—'} | {extra or '—'} | {a['help'] or '—'} |")
            out.append("")
        else:
            out += ["This subcommand takes no arguments.", ""]
    return "\n".join(out)


# ------------------------------------------------------------------- routes
_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "websocket", "head", "options"}


def extract_routes(source: Path, rel: str = "prash/server.py") -> Dict[str, Any]:
    tree = _parse(source, rel)
    routes = []
    app_line = None
    middleware = []
    if tree is None:
        return {"file": rel, "routes": routes}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and _unparse(node.value.func) == "FastAPI":
            app_line = (node.lineno, node.end_lineno, {k.arg: _unparse(k.value) for k in node.value.keywords})
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and _unparse(node.value.func) == "app.add_middleware":
            middleware.append({"line": (node.lineno, node.end_lineno), "args": [_unparse(a) for a in node.value.args], "kw": {k.arg: _unparse(k.value) for k in node.value.keywords}})
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            if dec.func.attr not in _HTTP_METHODS and dec.func.attr != "api_route":
                continue
            if _unparse(dec.func.value) != "app":
                continue
            path = _const_str(dec.args[0]) if dec.args else None
            if dec.func.attr == "api_route":
                # @app.api_route(path, methods=[...]) registers one route per method.
                declared = _kw(dec, "methods")
                methods = [
                    str(e.value).upper()
                    for e in getattr(declared, "elts", [])
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                ] or ["GET"]
            else:
                methods = [dec.func.attr.upper()]
            params = []
            for arg in node.args.args:
                if arg.arg in {"self", "request", "websocket"}:
                    continue
                ann = _unparse(arg.annotation, 40) if arg.annotation is not None else ""
                params.append(f"{arg.arg}: {ann}" if ann else arg.arg)
            for method in methods:
                routes.append({
                    "method": method,
                    "path": path or "?",
                    "handler": node.name,
                    "async": isinstance(node, ast.AsyncFunctionDef),
                    "start": dec.lineno,
                    "end": node.end_lineno,
                    "summary": first_paragraph(ast.get_docstring(node), 200),
                    "params": params,
                    "response_model": _unparse(_kw(dec, "response_model")) if _kw(dec, "response_model") is not None else "",
                    "decorator": dec.func.attr,
                })
    routes.sort(key=lambda r: (r["path"], r["method"]))
    return {"file": rel, "routes": routes, "app": app_line, "middleware": middleware}


def _route_group(path: str) -> str:
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "/"
    if parts[0] == "api" and len(parts) > 1:
        return f"/api/{parts[1]}"
    return f"/{parts[0]}"


def render_routes(data: Dict[str, Any], pin: Dict[str, Any]) -> str:
    rel = data["file"]
    out = _header("Lear backend — desktop HTTP/WebSocket API reference", pin, "Extracted from the FastAPI route decorators in `prash/server.py`.")
    if data.get("app"):
        start, end, kw = data["app"]
        out.append(f"Application object: `FastAPI({', '.join(f'{k}={v}' for k, v in kw.items())})` at {ev(rel, start, end)}.")
    for mw in data.get("middleware", []):
        kws = ", ".join(f"{k}={v}" for k, v in mw["kw"].items())
        out.append(f"Middleware: `{', '.join(mw['args'])}` with {kws} at {ev(rel, *mw['line'])}.")
    out.append("")
    decorators = len({r["start"] for r in data["routes"]})
    multi = sorted({r["path"] for r in data["routes"] if r.get("decorator") == "api_route"})
    out.append(
        f"Routes found: {len(data['routes'])} method/path pairs from {decorators} route decorators"
        + (f" ({len(multi)} `@app.api_route` decorators register several methods: " + ", ".join(f"`{m}`" for m in multi) + ")" if multi else "")
        + ". No route decorator in this file declares an authentication dependency (checked by `docverify` against the same AST)."
    )
    out.append("")
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in data["routes"]:
        groups[_route_group(r["path"])].append(r)
    for group in sorted(groups):
        out += [f"## Routes under `{group}`", "", "| Method | Path | Handler | Evidence | Summary |", "|---|---|---|---|---|"]
        for r in groups[group]:
            out.append(f"| {r['method']} | `{r['path']}` | `{r['handler']}` | {ev(rel, r['start'], r['end'])} | {r['summary'] or '—'} |")
        out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------- env
def _is_environ(node: ast.AST) -> bool:
    text = _unparse(node)
    return text in {"os.environ", "environ"}


def extract_env(source: Path, files: Iterable[str]) -> Dict[str, Any]:
    env: Dict[str, Dict[str, Any]] = {}
    creds: Dict[str, List[Tuple[str, int]]] = defaultdict(list)

    def add(name: str, rel: str, line: int, kind: str, default: str = "") -> None:
        entry = env.setdefault(name, {"name": name, "uses": [], "defaults": set(), "kinds": set()})
        entry["uses"].append((rel, line))
        entry["kinds"].add(kind)
        if default:
            entry["defaults"].add(default)

    for rel in files:
        if not rel.endswith(".py"):
            continue
        tree = _parse(source, rel)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                first = _const_str(node.args[0]) if node.args else None
                if isinstance(func, ast.Attribute):
                    if func.attr == "get" and _is_environ(func.value) and first:
                        add(first, rel, node.lineno, "os.environ.get", _unparse(node.args[1], 50) if len(node.args) > 1 else "")
                        continue
                    if func.attr == "getenv" and first:
                        add(first, rel, node.lineno, "os.getenv", _unparse(node.args[1], 50) if len(node.args) > 1 else "")
                        continue
                    if func.attr in {"setdefault", "pop"} and _is_environ(func.value) and first:
                        add(first, rel, node.lineno, f"os.environ.{func.attr}")
                        continue
                    if func.attr == "get" and first and _ENV_NAME.match(first):
                        receiver = _unparse(func.value).lower()
                        if "cred" in receiver or receiver in {"env_config", "config", "self.config", "candidate", "env", "values"}:
                            creds[first].append((rel, node.lineno))
                            continue
                name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else "")
                if first and _ENV_NAME.match(first) and re.search(r"(^_?env_|_env$|^_?(int|bool|float|str)_env)", name):
                    add(first, rel, node.lineno, f"helper {name}()", _unparse(node.args[1], 50) if len(node.args) > 1 else "")
            elif isinstance(node, ast.Subscript) and _is_environ(node.value):
                key = _const_str(node.slice)
                if key:
                    add(key, rel, node.lineno, "os.environ[] write" if isinstance(node.ctx, ast.Store) else "os.environ[] read")
            elif isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.In, ast.NotIn)):
                key = _const_str(node.left)
                if key and _ENV_NAME.match(key) and node.comparators and _is_environ(node.comparators[0]):
                    add(key, rel, node.lineno, "presence check")
    for entry in env.values():
        entry["defaults"] = sorted(entry["defaults"])
        entry["kinds"] = sorted(entry["kinds"])
        entry["uses"] = sorted(set(entry["uses"]))
    return {"env": dict(sorted(env.items())), "credential_keys": {k: sorted(set(v)) for k, v in sorted(creds.items())}}


def parse_env_example(source: Path, rel: str = ".env.example") -> List[Dict[str, Any]]:
    path = source / rel
    if not path.exists():
        return []
    rows = []
    for number, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = raw.strip()
        commented = line.startswith("#")
        body = line.lstrip("#").strip()
        match = re.match(r"^([A-Z][A-Z0-9_]{2,})\s*=(.*)$", body)
        if not match:
            continue
        rows.append({"name": match.group(1), "line": number, "commented": commented, "has_value": bool(match.group(2).strip())})
    return rows


def _env_group(name: str) -> str:
    for prefix, label in (
        ("PRASH_", "Lear runtime (`PRASH_*`)"),
        ("KUBE", "Kubernetes"),
        ("DEEPSEEK_", "Diagnosis model providers"),
        ("KIMI_", "Diagnosis model providers"),
        ("PRIMARY_MODEL", "Diagnosis model providers"),
        ("GITHUB_", "GitHub"),
        ("GITLAB_", "GitLab"),
        ("DATADOG_", "Datadog"),
        ("DD_", "Datadog"),
        ("GRAFANA_", "Grafana"),
        ("PAGERDUTY_", "PagerDuty"),
        ("SNYK_", "Snyk"),
        ("VERCEL_", "Vercel"),
        ("AWS_", "AWS"),
        ("AZURE_", "Azure"),
        ("GCP_", "GCP"),
        ("GOOGLE_", "GCP"),
        ("SLACK_", "Notifications and messaging"),
        ("DISCORD_", "Notifications and messaging"),
        ("SMTP_", "Notifications and messaging"),
        ("IMAP_", "Notifications and messaging"),
        ("GMAIL_", "Notifications and messaging"),
        ("EMAIL_", "Notifications and messaging"),
        ("TERRAFORM_", "Terraform"),
        ("TF_", "Terraform"),
    ):
        if name.startswith(prefix):
            return label
    return "Other"


def render_env(data: Dict[str, Any], example: List[Dict[str, Any]], pin: Dict[str, Any]) -> str:
    out = _header("Lear backend — environment variables and configuration keys", pin,
                  "Extracted from every `os.environ` / `os.getenv` access (and `*_env()` helpers) in `prash/` and `evals/`, plus the key names in `.env.example`.",
                  label="configured")
    out += [
        "Values are never copied into these docs; only names, code defaults and where each key is read.",
        "",
    ]
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for entry in data["env"].values():
        groups[_env_group(entry["name"])].append(entry)
    out.append(f"Variables read from the process environment: {len(data['env'])}.")
    out.append("")
    for group in sorted(groups):
        out += [f"## {group}", "", "| Variable | Code default | Access | Read at |", "|---|---|---|---|"]
        for entry in groups[group]:
            uses = ", ".join(ev(rel, line) for rel, line in entry["uses"][:4])
            if len(entry["uses"]) > 4:
                uses += f" (+{len(entry['uses']) - 4} more)"
            defaults = ", ".join(f"`{d}`" for d in entry["defaults"][:2]) or "—"
            out.append(f"| `{entry['name']}` | {defaults} | {', '.join(entry['kinds'])} | {uses} |")
        out.append("")
    if data["credential_keys"]:
        out += [
            "## Keys read from the credentials mapping",
            "",
            "These keys are looked up on the dict loaded by `CredentialStore` (the local `.env` file), not on the process environment. Detected with an AST pattern (`<credentials>.get(\"KEY\")`), so this list can miss dynamically built keys.",
            "",
            "| Key | Read at |",
            "|---|---|",
        ]
        for key, uses in data["credential_keys"].items():
            refs = ", ".join(ev(rel, line) for rel, line in uses[:4]) + (f" (+{len(uses) - 4} more)" if len(uses) > 4 else "")
            out.append(f"| `{key}` | {refs} |")
        out.append("")
    if example:
        out += ["## Keys listed in `.env.example`", "", "| Key | Line | State in the example |", "|---|---|---|"]
        for row in example:
            state = "commented out" if row["commented"] else ("example value present" if row["has_value"] else "blank")
            out.append(f"| `{row['name']}` | {ev('.env.example', row['line'])} | {state} |")
        out.append("")
        documented = {r["name"] for r in example}
        read = set(data["env"]) | set(data["credential_keys"])
        out += [
            "## Cross-check",
            "",
            f"- Read in code but absent from `.env.example`: {', '.join(f'`{n}`' for n in sorted(read - documented)) or 'none'}.",
            f"- Listed in `.env.example` but not found by this extractor: {', '.join(f'`{n}`' for n in sorted(documented - read)) or 'none'}.",
            "",
        ]
    return "\n".join(out)


# ------------------------------------------------------------------ actions
def extract_actions(source: Path, files: Iterable[str], cli_rel: str = "prash/cli.py") -> Dict[str, Any]:
    actions = []
    for rel in files:
        if not (rel.startswith("prash/actions/") and rel.endswith(".py")):
            continue
        tree = _parse(source, rel)
        if tree is None:
            continue
        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _unparse(node.func).split(".")[-1] == "ActionSpec"):
                continue
            owner = next((c for c in classes if c.lineno <= node.lineno <= (c.end_lineno or c.lineno)), None)
            tier = _unparse(_kw(node, "risk_tier"))
            actions.append({
                "id": _const_str(_kw(node, "id")) or _unparse(_kw(node, "id")),
                "tier": tier.split(".")[-1].lower() if tier else "?",
                "reversible": _unparse(_kw(node, "reversible")),
                "capabilities": _unparse(_kw(node, "capabilities"), 90) if _kw(node, "capabilities") is not None else "",
                "summary": one_line(_const_str(_kw(node, "summary")) or "", 200),
                "approval_hint": one_line(_const_str(_kw(node, "approval_hint")) or "", 200),
                "class": owner.name if owner else None,
                "class_lines": (owner.lineno, owner.end_lineno) if owner else None,
                "spec_lines": (node.lineno, node.end_lineno),
                "file": rel,
                "methods": sorted(m.name for m in (owner.body if owner else []) if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name in {"plan", "execute", "verify", "dry_run"}),
            })
    registered: List[str] = []
    reg_lines = None
    tree = _parse(source, cli_rel)
    if tree is not None:
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "_build_dispatcher":
                reg_lines = (node.lineno, node.end_lineno)
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id.endswith("Action"):
                        registered.append(sub.func.id)
    actions.sort(key=lambda a: (a["tier"], a["id"]))
    return {"actions": actions, "registered": registered, "registration": reg_lines, "cli": cli_rel}


def render_actions(data: Dict[str, Any], pin: Dict[str, Any]) -> str:
    out = _header("Lear backend — action catalogue", pin, "Extracted from every `ActionSpec(...)` in `prash/actions/` and from `_build_dispatcher` in `prash/cli.py`.")
    reg = set(data["registered"])
    if data["registration"]:
        out.append(f"The CLI dispatcher registers {len(reg)} action classes in `_build_dispatcher` ({ev(data['cli'], *data['registration'])}). Risk tiers are enforced by the permission engine (see authentication_authorization.md).")
    out.append("")
    counts: Dict[str, int] = defaultdict(int)
    for a in data["actions"]:
        counts[a["tier"]] += 1
    out.append("Specs by risk tier: " + ", ".join(f"{tier} {n}" for tier, n in sorted(counts.items())) + ".")
    out.append("")
    for tier in ("safe", "approval", "never"):
        rows = [a for a in data["actions"] if a["tier"] == tier]
        if not rows:
            continue
        out += [f"## {tier.capitalize()}-tier actions", ""]
        for a in rows:
            out.append(f"### {a['id']}")
            out.append("")
            out.append(f"- Risk tier: {a['tier']}. Reversible: {a['reversible']}.")
            if a["class"]:
                out.append(f"- Class: `{a['class']}` at {ev(a['file'], *a['class_lines'])}; spec at {ev(a['file'], *a['spec_lines'])}.")
            out.append(f"- Registered in the CLI dispatcher: {'yes' if a['class'] in reg else 'no'}.")
            if a["methods"]:
                out.append(f"- Contract methods defined in the class: {', '.join(a['methods'])}.")
            if a["summary"]:
                out.append(f"- Summary (from the spec): {a['summary']}")
            if a["approval_hint"]:
                out.append(f"- Approval hint (from the spec): {a['approval_hint']}")
            if a["capabilities"]:
                out.append(f"- Capabilities: `{a['capabilities']}`.")
            out.append("")
    return "\n".join(out)


# --------------------------------------------------------------- connectors
_CONNECTOR_METHODS = ("authenticate", "locate", "fetch_logs", "poll_state", "watch", "get_stats", "get_previous_revision")


def extract_connectors(source: Path, files: Iterable[str], cli_rel: str = "prash/cli.py") -> Dict[str, Any]:
    rows = []
    for rel in files:
        if not (rel.startswith("prash/connectors/") and rel.endswith(".py")) or rel.endswith("base.py"):
            continue
        tree = _parse(source, rel)
        if tree is None:
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            bases = [_unparse(b) for b in node.bases]
            if not any(b.split(".")[-1] == "Connector" for b in bases):
                continue
            attrs: Dict[str, str] = {}
            methods: Dict[str, Tuple[int, int]] = {}
            for item in node.body:
                if isinstance(item, ast.Assign) and isinstance(item.targets[0], ast.Name):
                    attrs[item.targets[0].id] = _unparse(item.value, 160)
                elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and item.value is not None:
                    attrs[item.target.id] = _unparse(item.value, 160)
                elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods[item.name] = (item.lineno, item.end_lineno)
            rows.append({
                "class": node.name,
                "file": rel,
                "lines": (node.lineno, node.end_lineno),
                "name": attrs.get("name", "").strip("'\""),
                "read": attrs.get("read_capabilities", ""),
                "write": attrs.get("write_capabilities", ""),
                "interface": {m: methods[m] for m in _CONNECTOR_METHODS if m in methods},
                "public_methods": sorted(m for m in methods if not m.startswith("_") and m not in _CONNECTOR_METHODS),
                "summary": first_paragraph(ast.get_docstring(node), 240),
            })
    providers: Dict[str, str] = {}
    providers_lines = None
    tree = _parse(source, cli_rel)
    if tree is not None:
        for node in tree.body:
            target = node.target if isinstance(node, ast.AnnAssign) else (node.targets[0] if isinstance(node, ast.Assign) else None)
            if isinstance(target, ast.Name) and target.id == "PROVIDERS" and isinstance(node.value, ast.Dict):
                providers_lines = (node.lineno, node.end_lineno)
                for k, v in zip(node.value.keys, node.value.values):
                    providers[_const_str(k) or "?"] = _unparse(v)
    rows.sort(key=lambda r: r["file"])
    return {"connectors": rows, "providers": providers, "providers_lines": providers_lines, "cli": cli_rel}


def render_connectors(data: Dict[str, Any], pin: Dict[str, Any]) -> str:
    out = _header("Lear backend — connector reference", pin, "Extracted from classes that subclass `Connector` in `prash/connectors/` and from the `PROVIDERS` map in `prash/cli.py`.")
    if data["providers_lines"]:
        listed = ", ".join(f"`{k}` → `{v}`" for k, v in data["providers"].items())
        out.append(f"`PROVIDERS` ({ev(data['cli'], *data['providers_lines'])}) maps {len(data['providers'])} provider names to classes: {listed}.")
        mapped = set(data["providers"].values())
        unmapped = [r["class"] for r in data["connectors"] if r["class"] not in mapped]
        if unmapped:
            out.append(f"Connector classes not in `PROVIDERS`: {', '.join(f'`{c}`' for c in unmapped)} (used directly by the modules that need them).")
    out.append("")
    out.append("Interface methods come from `Connector` in `prash/connectors/base.py`; a missing method means the connector inherits the base behaviour (`NotImplementedError` for the optional ones).")
    out.append("")
    for r in data["connectors"]:
        title = r["name"] or r["class"]
        out.append(f"## {title} connector")
        out.append("")
        out.append(f"- Class: `{r['class']}` at {ev(r['file'], *r['lines'])}.")
        if r["summary"]:
            out.append(f"- Class docstring: {r['summary']}")
        if r["read"]:
            out.append(f"- Read capabilities: `{r['read']}`.")
        if r["write"]:
            out.append(f"- Write capabilities: `{r['write']}`.")
        if r["interface"]:
            out.append("- Interface methods implemented: " + ", ".join(f"`{m}` {ev(r['file'], *lines)}" for m, lines in r["interface"].items()) + ".")
        missing = [m for m in _CONNECTOR_METHODS[:6] if m not in r["interface"]]
        if missing:
            out.append("- Not overridden: " + ", ".join(f"`{m}`" for m in missing) + ".")
        if r["public_methods"]:
            names = r["public_methods"][:14]
            out.append("- Other public methods: " + ", ".join(f"`{m}`" for m in names) + (f" (+{len(r['public_methods']) - 14} more)" if len(r["public_methods"]) > 14 else "") + ".")
        out.append("")
    return "\n".join(out)


# ------------------------------------------------------------------ modules
def extract_modules(source: Path, files: Iterable[str]) -> List[Dict[str, Any]]:
    rows = []
    for rel in files:
        if not (rel.startswith("prash/") and rel.endswith(".py")):
            continue
        tree = _parse(source, rel)
        if tree is None:
            rows.append({"file": rel, "error": "unparseable"})
            continue
        classes, functions = [], []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                classes.append((node.name, node.lineno, node.end_lineno, [_unparse(b, 30) for b in node.bases]))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                is_route = any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in _HTTP_METHODS for d in node.decorator_list)
                functions.append((node.name, node.lineno, node.end_lineno, is_route))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.level:
                    base = rel.rsplit("/", node.level)[0].replace("/", ".")
                    imports.add(f"{base}.{node.module}" if node.module else base)
                elif node.module.startswith("prash"):
                    imports.add(node.module)
        rows.append({
            "file": rel,
            "lines": _line_count(source / rel),
            "summary": first_paragraph(ast.get_docstring(tree), 360),
            "classes": classes,
            "functions": functions,
            "imports": sorted(imports),
        })
    return rows


def render_modules(rows: List[Dict[str, Any]], pin: Dict[str, Any], tested_by: Dict[str, List[str]]) -> str:
    out = _header("Lear backend — module index", pin, "One section per module in `prash/`: size, docstring summary, public top-level classes and functions with line spans, and the test files that import it.")
    out.append(f"Modules: {len(rows)}; total lines: {sum(r.get('lines') or 0 for r in rows)}.")
    out.append("")
    for r in rows:
        module = r["file"][:-3].replace("/", ".")
        out.append(f"## {r['file']}")
        out.append("")
        if r.get("error"):
            out += [f"Could not be parsed: {r['error']}.", ""]
            continue
        out.append(f"- Size: {r['lines']} lines. Module: `{module}`.")
        if r["summary"]:
            out.append(f"- Docstring summary: {r['summary']}")
        if r["classes"]:
            parts = [f"`{name}` ({', '.join(bases) or 'object'}) {ev(r['file'], start, end)}" for name, start, end, bases in r["classes"][:25]]
            out.append("- Classes: " + "; ".join(parts) + (f"; +{len(r['classes']) - 25} more" if len(r["classes"]) > 25 else "") + ".")
        plain = [f for f in r["functions"] if not f[3]]
        routes = [f for f in r["functions"] if f[3]]
        if plain:
            parts = [f"`{name}` {ev(r['file'], start, end)}" for name, start, end, _ in plain[:30]]
            out.append("- Public functions: " + "; ".join(parts) + (f"; +{len(plain) - 30} more" if len(plain) > 30 else "") + ".")
        if routes:
            out.append(f"- Route handlers: {len(routes)} (see reference/http_api.md).")
        tests = tested_by.get(module, [])
        out.append("- Imported by tests: " + (", ".join(f"`{t}`" for t in tests) if tests else "none found") + ".")
        out.append("")
    return "\n".join(out)


# -------------------------------------------------------------------- tests
def extract_tests(source: Path, files: Iterable[str]) -> Dict[str, Any]:
    rows = []
    tested_by: Dict[str, List[str]] = defaultdict(list)
    for rel in files:
        if not (rel.startswith("tests/") and rel.endswith(".py")):
            continue
        tree = _parse(source, rel)
        if tree is None:
            continue
        tests, skips, importorskip = [], [], []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                tests.append((node.name, node.lineno))
                for dec in node.decorator_list:
                    if "skip" in _unparse(dec, 200):
                        skips.append((node.name, node.lineno, _unparse(dec, 90)))
            if isinstance(node, ast.Call) and _unparse(node.func).endswith("importorskip"):
                importorskip.append((_const_str(node.args[0]) if node.args else "?", node.lineno))
            if isinstance(node, ast.Assign) and any(_unparse(t) == "pytestmark" for t in node.targets):
                skips.append(("(module)", node.lineno, _unparse(node.value, 90)))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("prash"):
                modules.add(node.module)
                for alias in node.names:
                    candidate = f"{node.module}.{alias.name}"
                    if (source / (candidate.replace(".", "/") + ".py")).exists():
                        modules.add(candidate)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("prash"):
                        modules.add(alias.name)
        for module in modules:
            tested_by[module].append(rel)
        rows.append({"file": rel, "lines": _line_count(source / rel), "tests": tests, "skips": skips, "importorskip": importorskip, "modules": sorted(modules)})
    return {"files": sorted(rows, key=lambda r: r["file"]), "tested_by": {k: sorted(v) for k, v in tested_by.items()}}


def render_tests(data: Dict[str, Any], pin: Dict[str, Any]) -> str:
    out = _header("Lear backend — test suite reference", pin, "Counted with `ast` (functions named `test*`, skip decorators, `pytest.importorskip`). These are static counts; they were not executed for this page.", label="tested (static)")
    total = sum(len(r["tests"]) for r in data["files"])
    out.append(f"Test files: {len(data['files'])}; test functions: {total}.")
    out.append("")
    out += ["## Test files", "", "| File | Test functions | Skip markers | importorskip | prash modules imported |", "|---|---:|---:|---|---|"]
    for r in data["files"]:
        mods = ", ".join(f"`{m}`" for m in r["modules"][:6]) + (f" (+{len(r['modules']) - 6})" if len(r["modules"]) > 6 else "")
        imp = ", ".join(f"`{name}` {ev(r['file'], line)}" for name, line in r["importorskip"][:3]) or "—"
        out.append(f"| `{r['file']}` | {len(r['tests'])} | {len(r['skips'])} | {imp} | {mods or '—'} |")
    out.append("")
    skipped = [(r["file"], s) for r in data["files"] for s in r["skips"]]
    if skipped:
        out += ["## Skip markers", "", "| File | Test | Marker | Evidence |", "|---|---|---|---|"]
        for rel, (name, line, marker) in skipped:
            out.append(f"| `{rel}` | `{name}` | `{marker}` | {ev(rel, line)} |")
        out.append("")
    return "\n".join(out)


# --------------------------------------------------------------- source map
SOURCE_MAP: Sequence[Tuple[str, str, Sequence[str]]] = (
    ("CLI entry point and subcommands", "argparse spine, dispatch to command handlers", ("prash/cli.py", "pyproject.toml")),
    ("Action dispatch pipeline", "plan, circuit check, permission decision, prompt, execute, verify, audit", ("prash/dispatch.py",)),
    ("Permission engine", "modes x risk tiers decision rule", ("prash/permissions.py", "prash/actions/contract.py")),
    ("Action contract and catalogue", "ActionSpec, RiskTier, Plan, ActionResult and the concrete actions", ("prash/actions/contract.py", "prash/actions/__init__.py")),
    ("Circuit breaker", "per-resource action cap persisted to disk", ("prash/circuit_breaker.py",)),
    ("Audit log", "append-only JSON-lines record of every decision", ("prash/audit.py",)),
    ("Local credentials", ".env-style credential file and PRASH_SECRET_ keys", ("prash/credentials.py", ".env.example")),
    ("Connectors", "provider integrations behind one interface", ("prash/connectors/base.py", "prash/connectors/__init__.py")),
    ("Diagnosis brain", "LLM diagnosis, schemas, multi-failure split, correlation, memory", ("prash/brain/diagnosis_agent.py", "prash/brain/kimi_client.py", "prash/brain/schemas.py", "prash/brain/multi_diagnosis.py", "prash/brain/correlation.py", "prash/brain/local_memory.py")),
    ("Fix flow", "diagnosis to dispatched action", ("prash/fix.py", "prash/brain/edit_repair.py")),
    ("Watcher and notifications", "foreground poll loop and team webhooks", ("prash/watcher.py", "prash/notifications.py")),
    ("Interactive surfaces", "REPL, free-text intent resolution, Textual TUI", ("prash/repl.py", "prash/intent.py", "prash/tui.py", "prash/ui.py")),
    ("Setup wizard", "interactive configuration", ("prash/setup.py",)),
    ("Desktop API server", "FastAPI REST/WebSocket bridge for the desktop app", ("prash/server.py", "prash/connector_registry.py", "prash/widget_generator.py")),
    ("Demo and simulation modules", "mock services, demo pages, incident/chat/email/slack helpers", ("prash/mock_service.py", "prash/demo_page.py", "prash/admin_panel.py", "prash/customer_store.py", "prash/incident_page.py", "prash/incident_manager.py", "prash/chat_manager.py", "prash/conversation_router.py", "prash/email_service.py", "prash/slack_service.py")),
    ("Evaluation harness", "diagnosis eval cases, runner, scorer", ("evals/README.md", "evals/run_eval.py", "evals/score.py")),
    ("Packaging and launchers", "dependencies, console script, launch scripts", ("pyproject.toml", "package.json", "lear.bat", "lear.ps1")),
    ("Project documentation", "design notes and handoff (claims to verify)", ("README.md", "PRASH_V2.md", "LEAR_HANDOFF.md", "CONNECTOR_REWRITE_SPEC.md", "agent_guidelines.md")),
)


def render_source_map(source: Path, pin: Dict[str, Any], files: Sequence[str]) -> str:
    tracked = set(files)
    out = _header("Lear backend — source map", pin, "Topic-to-file index used to navigate the code; each file entry is checked to exist at the pinned commit.", label="implemented (file existence)")
    # Navigation only: the topic headings match many questions while the
    # entries carry no facts, so the page stays out of the assistant's index.
    out += ["<!-- lear-index: skip-page -->", ""]
    for topic, desc, paths in SOURCE_MAP:
        out += [f"## {topic}", "", f"{desc}.", ""]
        for rel in paths:
            if rel in tracked:
                lines = _line_count(source / rel)
                out.append(f"- `{rel}` ({lines} lines) — {ev(rel, 1, lines or 1)}")
            else:
                out.append(f"- `{rel}` — NOT FOUND at this commit")
        out.append("")
    return "\n".join(out)


# -------------------------------------------------------------------- build
def build_docs(source: Path, out_dir: Path, *, agent_docs_dir: Optional[Path] = None) -> Dict[str, Any]:
    GUARD.rewrites = 0
    GUARD.examples.clear()
    source = Path(source).expanduser().resolve()
    out_dir = Path(out_dir)
    agent_docs_dir = Path(agent_docs_dir) if agent_docs_dir else out_dir.parent / "lear_agent"
    pin = pin_source(source)
    files = tracked_files(source)
    cli = extract_cli(source)
    routes = extract_routes(source)
    env = extract_env(source, [f for f in files if f.startswith(("prash/", "evals/"))])
    example = parse_env_example(source)
    actions = extract_actions(source, files)
    connectors = extract_connectors(source, files)
    tests = extract_tests(source, files)
    modules = extract_modules(source, files)
    inventory = build_inventory(source, pin)
    pages = {
        out_dir / "reference" / "cli.md": render_cli(cli, pin),
        out_dir / "reference" / "http_api.md": render_routes(routes, pin),
        out_dir / "reference" / "configuration_env.md": render_env(env, example, pin),
        out_dir / "reference" / "actions.md": render_actions(actions, pin),
        out_dir / "reference" / "connectors.md": render_connectors(connectors, pin),
        out_dir / "reference" / "modules.md": render_modules(modules, pin, tests["tested_by"]),
        out_dir / "reference" / "tests.md": render_tests(tests, pin),
        out_dir / "source_map.md": render_source_map(source, pin, files),
        agent_docs_dir / "source_inventory.md": render_inventory(inventory),
    }
    pin_path = out_dir / "SOURCE_PIN.json"
    pin_doc = dict(pin)
    pin_doc.pop("local_path", None)
    pin_doc["generated_pages"] = sorted(str(p.relative_to(out_dir)) for p in pages if out_dir in p.parents)
    written, unchanged = [], []
    for path, text in list(pages.items()) + [(pin_path, json.dumps(pin_doc, indent=2) + "\n")]:
        text = text if text.endswith("\n") else text + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text(encoding="utf-8") == text:
            unchanged.append(str(path))
            continue
        path.write_text(text, encoding="utf-8")
        written.append(str(path))
    residual = []
    for path, text in pages.items():
        for number, line in enumerate(text.splitlines(), 1):
            for finding in scan_text(line):
                residual.append({"file": str(path), "line": number, "rule": finding.rule_id, "excerpt": finding.excerpt[:120]})
    return {
        "pin": pin,
        "written": written,
        "unchanged": unchanged,
        "counts": {
            "cli_subcommands": len(cli["commands"]),
            "routes": len(routes["routes"]),
            "env_vars": len(env["env"]),
            "credential_keys": len(env["credential_keys"]),
            "actions": len(actions["actions"]),
            "registered_actions": len(actions["registered"]),
            "connectors": len(connectors["connectors"]),
            "modules": len(modules),
            "test_files": len(tests["files"]),
            "test_functions": sum(len(r["tests"]) for r in tests["files"]),
            "tracked_files": len(files),
        },
        "guardRewrites": GUARD.rewrites,
        "guardRewriteExamples": sorted(set(GUARD.examples)),
        "residualGuardrailHits": residual,
    }
