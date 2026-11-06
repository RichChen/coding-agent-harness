"""Query texts built from the issue (no gold) and from what the model wrote in its runs."""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

# ---------------------------------------------------------------- from the issue text

_URL = re.compile(r"https?://\S+")
_FRAME = re.compile(r'File "([^"\n]+)", line \d+, in ([A-Za-z_]\w*)')
_SLASH_PATH = re.compile(r"(?<![\w/.-])[\w.-]+(?:/[\w.-]+)+")
_PY_FILE = re.compile(r"\b[\w-]+\.py\b")
_DOTTED = re.compile(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b")
_CAMEL = re.compile(r"\b(?:[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+|[a-z][a-z0-9]*[A-Z]\w*)\b")
_SNAKE = re.compile(r"\b_*[A-Za-z][A-Za-z0-9]*(?:_+[A-Za-z0-9]+)+_*\b|\b__\w+__\b")
_CALL = re.compile(r"\b([A-Za-z_]\w{2,})\(")
_BACKTICK = re.compile(r"`([^`\n]{1,80})`")
_DOTTED_STOP = {"e.g", "i.e", "etc", "vs"}


def title(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def first_paragraph(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        if line.strip():
            lines.append(line.rstrip())
        elif lines:
            break
    return "\n".join(lines).strip()


def identifiers(text: str) -> list[str]:
    """Paths, traceback frames, dotted names, camelCase, snake_case, calls and backtick spans, in order of first
    appearance, deduplicated."""
    text = _URL.sub(" ", text)
    found: list[tuple[int, str]] = []
    for m in _FRAME.finditer(text):
        found.append((m.start(1), m.group(1)))
        found.append((m.start(2), m.group(2)))
    for rx in (_SLASH_PATH, _PY_FILE, _CAMEL, _SNAKE):
        found.extend((m.start(), m.group(0)) for m in rx.finditer(text))
    for m in _DOTTED.finditer(text):
        if m.group(0).lower() not in _DOTTED_STOP:
            found.append((m.start(), m.group(0)))
    found.extend((m.start(1), m.group(1)) for m in _CALL.finditer(text))
    found.extend((m.start(1), m.group(1).strip()) for m in _BACKTICK.finditer(text))
    out: dict[str, None] = {}
    for _, tok in sorted(found, key=lambda x: x[0]):
        tok = tok.strip()
        if len(tok) > 1:
            out.setdefault(tok, None)
    return list(out)


def issue_variants(problem_statement: str) -> dict[str, dict]:
    """variant -> {"text", ...}. An empty identifier list falls back to the title."""
    ids = identifiers(problem_statement)
    return {
        "full": {"text": problem_statement},
        "title": {"text": title(problem_statement)},
        "first_para": {"text": first_paragraph(problem_statement)},
        "identifiers": {"text": "\n".join(ids) if ids else title(problem_statement),
                        "n_identifiers": len(ids), "fallback_title": not ids},
    }


# ---------------------------------------------------------------- from the model's runs

GREP_PROGS = {"grep", "egrep", "fgrep", "rg"}
_GREP_OPTS_WITH_VALUE = {
    "-A", "-B", "-C", "-m", "-f", "-d", "-D", "-g", "-t", "-T", "-M",
    "--include", "--exclude", "--exclude-dir", "--glob", "--type", "--type-not", "--max-count",
    "--context", "--after-context", "--before-context", "--max-columns", "--color", "--colour",
}
_SEPARATORS = {"&&", "||", ";", "|", "&", ";;", "|&"}


def _commands(command: str) -> list[tuple[list[str], bool]] | None:
    """Split a shell command into simple commands: (tokens, receives_pipe). None if it cannot be tokenized."""
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        tokens = list(lex)
    except ValueError:
        return None
    out: list[tuple[list[str], bool]] = []
    cur: list[str] = []
    piped = False
    for tok in tokens:
        if tok in _SEPARATORS or (tok and set(tok) <= set("&|;")):
            if cur:
                out.append((cur, piped))
            cur = []
            piped = tok in {"|", "|&"}
            continue
        cur.append(tok)
    if cur:
        out.append((cur, piped))
    return out


def grep_patterns(command: str) -> list[str] | None:
    """Patterns of grep/rg/git grep commands that search files (not the right side of a pipe)."""
    cmds = _commands(command)
    if cmds is None:
        return None
    out = []
    for tokens, piped in cmds:
        if piped:
            continue
        if tokens[0] in GREP_PROGS:
            args = tokens[1:]
        elif tokens[:2] == ["git", "grep"]:
            args = tokens[2:]
        else:
            continue
        explicit, positional, i, opts_done = [], [], 0, False
        while i < len(args):
            a = args[i]
            if not opts_done and a == "--":
                opts_done = True
            elif not opts_done and a in ("-e", "--regexp"):
                if i + 1 < len(args):
                    explicit.append(args[i + 1])
                i += 1
            elif not opts_done and a.startswith("--regexp="):
                explicit.append(a.split("=", 1)[1])
            elif not opts_done and a in _GREP_OPTS_WITH_VALUE:
                i += 1
            elif not opts_done and a.startswith("-") and len(a) > 1:
                pass
            else:
                positional.append(a)
            i += 1
        pattern = "\n".join(explicit) if explicit else (positional[0] if positional else "")
        if pattern.strip():
            out.append(pattern)
    return out


def read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_queries(events: list[dict]) -> tuple[list[dict], int]:
    """Search queries the model wrote, in order: [{"step", "source", "text"}], plus the number of run_command
    calls that could not be tokenized. step = model calls started so far (as in the CR-2 analysis)."""
    out, bad, step = [], 0, 0
    for event in events:
        kind = event.get("event_type")
        if kind == "model.started":
            step += 1
            continue
        if kind != "tool.intent":
            continue
        payload = event.get("payload") or {}
        name, args = payload.get("name"), payload.get("arguments") or {}
        if name == "search_code" and str(args.get("pattern") or "").strip():
            out.append({"step": step, "source": "search_code", "text": str(args["pattern"])})
        elif name == "retrieve_code" and str(args.get("query") or "").strip():
            out.append({"step": step, "source": "retrieve_code", "text": str(args["query"])})
        elif name == "run_command":
            pats = grep_patterns(str(args.get("command") or ""))
            if pats is None:
                bad += 1
                continue
            out.extend({"step": step, "source": "grep", "text": p} for p in pats)
    return out, bad


def model_variants(queries: list[dict], window: int) -> dict[str, dict]:
    """model_first: the run's first search query. model_first{window}: every search query issued in the first
    ``window`` model calls, deduplicated and joined. Variants without a query are absent."""
    out = {}
    search = [q for q in queries if q["source"] in ("search_code", "grep")]
    if search:
        q = search[0]
        out["model_first"] = {"text": q["text"], "step": q["step"], "source": q["source"]}
    early = list(dict.fromkeys(q["text"] for q in search if q["step"] <= window))
    if early:
        out[f"model_first{window}"] = {"text": "\n".join(early), "n_queries": len(early)}
    return out
