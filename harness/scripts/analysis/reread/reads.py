"""What a tool call read and what it wrote, from its arguments (and, for read_file, its numbered output).

Read units
- lines(path, start, end): read_file (actual range from the numbered output), `cat`/`nl` (whole file, end=None),
  `sed -n 'A,Bp'`, `head -n N` (1..N), `awk 'NR>=A && NR<=B'`. A `sed`/`head`/`tail` later in a pipeline narrows a
  single-file `cat`/`nl` source.
- tail(path, n): `tail -n N`; only comparable with another tail or a whole-file read of the same file.
- search(pattern, paths): search_code, and grep/egrep/rg/git grep with file or directory arguments (a grep that
  filters a single-file `cat` becomes a search of that file). Options such as -n, -r, -A/-B/-C are ignored, so
  the same pattern on the same paths with more context still counts as the same search. Basic-regex grep
  alternation `\\|` is written as `|` so it compares equal to the same pattern given to search_code or grep -E.
  Glob paths (`out/*.svg`) are resolved against the current directory like plain paths.

Writes: edit tools (write_file, search_replace, edit_file), output redirects, `sed -i`, `perl -i`, `tee`,
`cp`/`mv`/`rm`/`touch` targets, `git checkout/restore <paths>`. Commands that may write somewhere unknown
(`git stash`, `git checkout .`, `git apply`, `patch`, python with file-writing calls and no recognisable path)
are flagged unknown_write; the analysis treats them as touching every file. Commands that run code (python,
pytest, sphinx-build, make, tox, sh/bash scripts) are flagged ran: they may regenerate files outside the
repository sources (build and output directories, scratch directories outside the workspace).

Not reads: ls, find, wc, git diff/log/show/status, python/pytest runs. Commands the tokenizer cannot split are
marked unparsed and contribute no units.
"""

from __future__ import annotations

import posixpath
import re
import shlex
from dataclasses import dataclass, field

EDIT_TOOLS = {"write_file", "search_replace", "edit_file"}
WORKSPACE = "/workspace"


@dataclass(frozen=True)
class Unit:
    kind: str  # "lines" | "tail" | "search"
    path: str  # file for lines/tail; joined path list for search
    start: int | None = None
    end: int | None = None  # None = to end of file
    n: int | None = None  # tail length
    pattern: str | None = None
    source: str = ""


@dataclass
class CallIO:
    units: list[Unit] = field(default_factory=list)
    writes: set[str] = field(default_factory=set)
    unknown_write: bool = False
    ran: bool = False
    unparsed: bool = False
    file_lengths: dict[str, int] = field(default_factory=dict)


def norm_path(raw: str, cwd: str = "", allow_glob: bool = False) -> str | None:
    """Workspace-relative path ('' = workspace root); absolute paths outside the workspace stay absolute."""
    raw = raw.strip()
    if not raw or raw == "-" or raw.startswith("$") or (not allow_glob and ("*" in raw or "{" in raw)):
        return None
    if raw == WORKSPACE or raw.startswith(WORKSPACE + "/"):
        rel = raw[len(WORKSPACE):].lstrip("/")
    elif raw.startswith("/"):
        return posixpath.normpath(raw)
    elif raw.startswith("~"):
        return None
    else:
        rel = posixpath.join(cwd, raw) if cwd else raw
        if rel.startswith("/"):
            return posixpath.normpath(rel)
    rel = posixpath.normpath(rel)
    return "" if rel == "." else rel


# --- read_file -------------------------------------------------------------------------------------------------

_NUMBERED = re.compile(r"^(\d+)\|", re.MULTILINE)
_SHOWING = re.compile(r"\[showing lines (\d+)-(\d+) of (\d+);")


def read_file_io(args: dict, output: str | None, success: bool) -> CallIO:
    io = CallIO()
    if not success:
        return io
    path = norm_path(str(args.get("path", "")))
    if path is None:
        return io
    start = end = None
    if output:
        shown = _SHOWING.search(output)
        nums = _NUMBERED.findall(output.split("\n[exit_code=")[0])
        if shown:
            start, end, total = int(shown.group(1)), int(shown.group(2)), int(shown.group(3))
            io.file_lengths[path] = total
            if end < start:
                return io
        elif nums:
            start, end = int(nums[0]), int(nums[-1])
            io.file_lengths[path] = end
    if start is None:
        offset, limit = args.get("offset"), args.get("limit")
        start = offset if isinstance(offset, int) and offset > 0 else 1
        end = start + limit - 1 if isinstance(limit, int) and limit > 0 else None
    io.units.append(Unit("lines", path, start, end, source="read_file"))
    return io


def search_code_io(args: dict) -> CallIO:
    io = CallIO()
    pattern = args.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return io
    path = norm_path(str(args.get("path") or ".")) or ""
    glob = args.get("glob")
    key = path + (f"::{glob}" if glob else "")
    io.units.append(Unit("search", key, pattern=pattern, source="search_code"))
    return io


# --- run_command -----------------------------------------------------------------------------------------------

_HEREDOC = re.compile(r"<<(-?)[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
_OPERATORS = {"&&", "||", ";", "&", ";;", "|", "|&"}
_REDIRECTS = {">", ">>", "&>", ">|", ">&", "&>>", "<", "<<", "<<<", "<<-", "<>"}
_PREFIXES = {"timeout", "sudo", "env", "command", "time", "nice", "nohup", "stdbuf"}
_SED_PRINT = re.compile(r"^\s*(\d+)\s*(?:,\s*(\$|\d+|\+\d+))?\s*p\s*$")
_AWK_RANGE = re.compile(r"^\s*NR\s*>=?\s*(\d+)\s*&&\s*NR\s*<=?\s*(\d+)\s*(?:\{\s*print[^}]*\})?\s*$")
_AWK_RANGE2 = re.compile(r"^\s*NR\s*==\s*(\d+)\s*,\s*NR\s*==\s*(\d+)\s*(?:\{\s*print[^}]*\})?\s*$")
_PY_WRITES = re.compile(r"open\([^)]*['\"][wa]b?\+?['\"]|\.write_text\(|\.write_bytes\(|os\.remove\(|os\.unlink\("
                        r"|shutil\.(?:copy|move)|\.unlink\(|(?<!sys\.stdout)(?<!sys\.stderr)\.write\(")
_PATHLIKE = re.compile(r"['\"]((?:/workspace/)?[\w./-]+\.\w+)['\"]")
# grep/rg options that take a separate value
_GREP_VALUE_OPTS = {"-A", "-B", "-C", "-m", "-e", "-f", "-d", "-D", "-g", "-t", "-T", "--include", "--exclude",
                    "--exclude-dir", "--glob", "--type", "--max-count", "--context", "--after-context",
                    "--before-context", "--regexp", "--file", "-M", "--max-columns"}
_HEAD_TAIL_VALUE_OPTS = {"-n", "-c", "--lines", "--bytes"}
_RUNNERS = {"pytest", "py.test", "sphinx-build", "make", "tox", "bash", "sh", "runtests.py", "nosetests",
            "django-admin", "node", "npm", "npx"}


def _strip_heredocs(command: str) -> tuple[str, list[str]]:
    """Remove heredoc bodies; return the command and the bodies (scanned for python writes)."""
    lines = command.split("\n")
    out, bodies, i = [], [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        delims = [(m.group(1) == "-", m.group(3)) for m in _HEREDOC.finditer(line)]
        i += 1
        for dash, delim in delims:
            body = []
            while i < len(lines) and (lines[i].strip() if dash else lines[i]) != delim:
                body.append(lines[i])
                i += 1
            i += 1  # delimiter line
            bodies.append("\n".join(body))
    return "\n".join(out), bodies


def _newlines_to_separators(command: str) -> str:
    """Unquoted newlines end a command; replace them with ' ; ' so shlex keeps the boundary."""
    out, quote, escape = [], None, False
    for ch in command:
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\" and quote != "'":
            if quote is None:
                escape = True
            out.append(ch)
            continue
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "\n":
            out.append(" ; ")
            continue
        out.append(ch)
    return "".join(out)


def _tokenize(command: str) -> list[str] | None:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return None


@dataclass
class Simple:
    argv: list[str]
    out_targets: list[str]
    in_file: str | None
    heredoc: bool


def _split(tokens: list[str]) -> list[list[Simple]]:
    """Pipelines (split on && || ; &) of simple commands (split on |)."""
    pipelines: list[list[Simple]] = [[]]
    argv: list[str] = []
    outs: list[str] = []
    state = {"in": None, "heredoc": False}

    def flush():
        nonlocal argv, outs
        if argv or outs:
            pipelines[-1].append(Simple(argv, outs, state["in"], state["heredoc"]))
        argv, outs = [], []
        state["in"], state["heredoc"] = None, False

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _OPERATORS:
            flush()
            if tok not in ("|", "|&"):
                pipelines.append([])
            i += 1
            continue
        if tok in _REDIRECTS or (tok and set(tok) <= set("<>&|") and ("<" in tok or ">" in tok)):
            if argv and argv[-1].isdigit():
                argv.pop()
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            if tok.startswith("<<"):
                state["heredoc"] = True
            elif tok == "<":
                state["in"] = target
            elif not target.isdigit() and target not in _OPERATORS:
                outs.append(target)
            i += 2
            continue
        argv.append(tok)
        i += 1
    flush()
    return [p for p in pipelines if p]


def _head_tail_count(argv: list[str]) -> tuple[str | None, list[str]]:
    """The -n value (as written, may be '+K' or '-K') and the file arguments."""
    count, files, i = None, [], 1
    while i < len(argv):
        a = argv[i]
        if a in _HEAD_TAIL_VALUE_OPTS and i + 1 < len(argv):
            count = argv[i + 1] if a in ("-n", "--lines") else count
            i += 2
            continue
        if a.startswith("--lines="):
            count = a.split("=", 1)[1]
        elif re.fullmatch(r"-n[+-]?\d+", a):
            count = a[2:]
        elif re.fullmatch(r"-\d+", a):
            count = a[1:]
        elif a.startswith("-") and a != "-":
            pass
        else:
            files.append(a)
        i += 1
    return count, files


def _range_from_sed(argv: list[str]) -> tuple[list[tuple[int, int | None]] | None, list[str], bool, bool]:
    """(ranges or None if not a pure print-range script, files, quiet(-n), in_place)."""
    quiet = in_place = False
    scripts, files, i = [], [], 1
    while i < len(argv):
        a = argv[i]
        if a in ("-e", "--expression") and i + 1 < len(argv):
            scripts.append(argv[i + 1])
            i += 2
            continue
        if a == "-n" or a == "--quiet" or a == "--silent":
            quiet = True
        elif a.startswith("-i") or a == "--in-place" or a.startswith("--in-place="):
            in_place = True
        elif re.fullmatch(r"-[a-zA-Z]+", a):
            quiet = quiet or "n" in a
            in_place = in_place or "i" in a
        elif not scripts:
            scripts.append(a)
        else:
            files.append(a)
        i += 1
    ranges: list[tuple[int, int | None]] = []
    for script in scripts:
        for part in re.split(r"[;\n]", script):
            if not part.strip():
                continue
            m = _SED_PRINT.match(part)
            if not m:
                return None, files, quiet, in_place
            start = int(m.group(1))
            stop = m.group(2)
            end = start if stop is None else None if stop == "$" else start + int(stop[1:]) if stop.startswith("+") else int(stop)
            ranges.append((start, end))
    return (ranges if ranges else None), files, quiet, in_place


def _grep_parts(argv: list[str], start: int) -> tuple[list[str], list[str]]:
    patterns, rest, i = [], [], start
    while i < len(argv):
        a = argv[i]
        if a == "--":
            rest.extend(argv[i + 1:])
            break
        if a in _GREP_VALUE_OPTS and i + 1 < len(argv):
            if a in ("-e", "--regexp"):
                patterns.append(argv[i + 1])
            i += 2
            continue
        if a.startswith("--regexp="):
            patterns.append(a.split("=", 1)[1])
        elif a.startswith("-") and len(a) > 1:
            m = re.fullmatch(r"-([a-zA-Z]*)([ABCm])(\d*)", a)
            if m and not m.group(3) and i + 1 < len(argv):
                i += 2
                continue
        else:
            rest.append(a)
        i += 1
    if not patterns and rest:
        patterns.append(rest.pop(0))
    return patterns, rest


def run_command_io(command: str) -> CallIO:
    io = CallIO()
    text, bodies = _strip_heredocs(command)
    tokens = _tokenize(_newlines_to_separators(text))
    if tokens is None:
        io.unparsed = True
        return io
    cwd = ""
    for pipeline in _split(tokens):
        source: list[Unit] = []  # units produced by the head of the pipeline, refined by later stages
        for pos, cmd in enumerate(pipeline):
            argv = list(cmd.argv)
            while argv and (re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", argv[0]) or argv[0] in _PREFIXES):
                head = argv.pop(0)
                if head == "timeout" and argv and re.fullmatch(r"[\d.]+[smh]?", argv[0]):
                    argv.pop(0)
            for target in cmd.out_targets:
                if not target.startswith("/dev/"):
                    p = norm_path(target, cwd)
                    if p is not None:
                        io.writes.add(p)
            if not argv:
                continue
            name = posixpath.basename(argv[0])
            args = argv[1:]
            if name == "cd":
                if args and args[0] not in ("-", "."):
                    new = norm_path(args[0], cwd)
                    if new is not None:
                        cwd = new
                continue
            if name in ("cat", "nl", "less", "more", "bat"):
                files = [a for a in args if not a.startswith("-")]
                if cmd.in_file and not files:
                    files = [cmd.in_file]
                if cmd.heredoc and not files:
                    continue
                source = [Unit("lines", p, 1, None, source=name) for p in (norm_path(f, cwd) for f in files) if p]
                continue
            if name == "sed":
                ranges, files, quiet, in_place = _range_from_sed(argv)
                if in_place:
                    for f in files:
                        p = norm_path(f, cwd)
                        if p is not None:
                            io.writes.add(p)
                    continue
                if ranges is None or not quiet:
                    continue
                if files:
                    source = [Unit("lines", p, s, e, source="sed")
                              for p in (norm_path(f, cwd) for f in files) if p for s, e in ranges]
                elif pos > 0 and len(source) == 1 and source[0].kind == "lines" and source[0].start == 1 and source[0].end is None:
                    source = [Unit("lines", source[0].path, s, e, source="sed") for s, e in ranges]
                continue
            if name in ("head", "tail"):
                count, files = _head_tail_count(argv)
                if not files and pos > 0 and len(source) == 1 and source[0].kind == "lines" \
                        and source[0].start == 1 and source[0].end is None:
                    files, paths = None, [source[0].path]
                elif files:
                    paths = [p for p in (norm_path(f, cwd) for f in files) if p]
                else:
                    continue
                n = count or "10"
                if name == "head":
                    if re.fullmatch(r"\+?\d+", n):
                        source = [Unit("lines", p, 1, int(n.lstrip("+")), source="head") for p in paths]
                    else:
                        source = [Unit("lines", p, 1, None, source="head") for p in paths]
                else:
                    if n.startswith("+") and n[1:].isdigit():
                        source = [Unit("lines", p, int(n[1:]), None, source="tail") for p in paths]
                    elif n.lstrip("-").isdigit():
                        source = [Unit("tail", p, n=int(n.lstrip("-")), source="tail") for p in paths]
                continue
            if name == "awk":
                prog = next((a for a in args if not a.startswith("-")), None)
                files = [a for a in args[args.index(prog) + 1:]] if prog in args else []
                m = (_AWK_RANGE.match(prog) or _AWK_RANGE2.match(prog)) if prog else None
                if m and files:
                    source = [Unit("lines", p, int(m.group(1)), int(m.group(2)), source="awk")
                              for p in (norm_path(f, cwd) for f in files) if p]
                continue
            if name in ("grep", "egrep", "fgrep", "rg", "ag") or (name == "git" and args[:1] == ["grep"]):
                patterns, rest = _grep_parts(argv, 2 if name == "git" else 1)
                if not patterns:
                    continue
                pattern = "\n".join(patterns)
                basic = name in ("grep", "git") and not any(
                    re.fullmatch(r"-[a-zA-Z]*[EPF][a-zA-Z]*", a) or a in ("--extended-regexp", "--perl-regexp",
                                                                          "--fixed-strings") for a in args)
                if basic:
                    pattern = pattern.replace("\\|", "|")
                if rest:
                    paths = sorted(p if p is not None else f
                                   for f, p in ((f, norm_path(f, cwd, allow_glob=True)) for f in rest))
                elif pos == 0 and (name in ("rg", "ag", "git") or any(re.fullmatch(r"-[a-zA-Z]*[rR][a-zA-Z]*", a) for a in args)):
                    paths = [cwd]
                elif pos > 0 and len(source) == 1 and source[0].kind == "lines" and source[0].start == 1 and source[0].end is None:
                    paths = [source[0].path]
                else:
                    continue
                if pos == 0 or source:
                    source = [Unit("search", "|".join(paths), pattern=pattern, source="grep")]
                continue
            if name == "tee":
                for f in args:
                    if not f.startswith("-"):
                        p = norm_path(f, cwd)
                        if p is not None:
                            io.writes.add(p)
                continue
            if name in ("cp", "mv", "rm", "touch", "truncate"):
                files = [a for a in args if not a.startswith("-")]
                targets = files[-1:] if name in ("cp", "mv") else files
                if name == "mv":
                    targets = files
                for f in targets:
                    p = norm_path(f, cwd)
                    if p is not None:
                        io.writes.add(p)
                continue
            if name == "perl" and any(a.startswith("-") and "i" in a.lstrip("-")[:3] for a in args):
                for f in args:
                    if not f.startswith("-") and ("/" in f or f.endswith(".py")):
                        p = norm_path(f, cwd)
                        if p is not None:
                            io.writes.add(p)
                continue
            if name == "git":
                sub = args[0] if args else ""
                if sub in ("checkout", "restore"):
                    paths = [a for a in args[1:] if not a.startswith("-") and a not in ("HEAD",)]
                    if "--" in args:
                        paths = [a for a in args[args.index("--") + 1:]]
                    if not paths or "." in paths:
                        io.unknown_write = True
                    for f in paths:
                        p = norm_path(f, cwd)
                        if p is not None:
                            io.writes.add(p)
                elif sub in ("stash", "apply", "am", "reset", "clean", "cherry-pick", "revert", "merge", "rebase"):
                    io.unknown_write = True
                continue
            if name in ("patch",):
                io.unknown_write = True
                continue
            if name in _RUNNERS or name.startswith("python"):
                io.ran = True
            if name.startswith("python") or name in ("pytest", "bash", "sh"):
                code = " ".join(args) + ("\n" + "\n".join(bodies) if cmd.heredoc else "")
                if _PY_WRITES.search(code):
                    paths = {norm_path(m, cwd) for m in _PATHLIKE.findall(code)}
                    paths.discard(None)
                    if paths:
                        io.writes.update(paths)
                    else:
                        io.unknown_write = True
                continue
        io.units.extend(source)
    return io


def call_io(name: str, args: dict, output: str | None, success: bool) -> CallIO:
    if name == "read_file":
        return read_file_io(args, output, success)
    if name == "search_code":
        return search_code_io(args)
    if name == "run_command":
        command = args.get("command")
        if not isinstance(command, str):
            command = " ".join(str(a) for a in args.get("argv") or [])
        return run_command_io(command)
    if name in EDIT_TOOLS:
        io = CallIO()
        path = norm_path(str(args.get("path") or args.get("file_path") or ""))
        if success and path is not None:
            io.writes.add(path)
        return io
    return CallIO()
