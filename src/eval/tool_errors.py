"""Tell a harness tool bug apart from a normal tool failure.

Only tools whose program is harness code count. A traceback printed by a
run_command is the agent's own command, not a tool bug. Normal results such as
"file not found", "path denied" or invalid arguments do not count either.
"""
import re

HARNESS_TOOLS = frozenset({"read_file", "list_dir", "write_file", "edit_file", "search_replace", "search_code", "retrieve_code"})
IMPLEMENTATION_ERRORS = frozenset({
    "AttributeError", "ImportError", "ModuleNotFoundError", "SyntaxError", "IndentationError",
    "TabError", "NameError", "UnboundLocalError", "TypeError",
})
_EXCEPTION_LINE = re.compile(r"^([A-Za-z_][\w.]*)(?::.*)?$")
_JSON_TYPES = {"string": str, "integer": int, "boolean": bool, "object": dict, "array": list}


def traceback_exception(output: str):
    """Class name of the last exception in a Python traceback, or None when there is no traceback."""
    name = None
    inside = False
    for line in (output or "").splitlines():
        if line.startswith("Traceback (most recent call last):"):
            inside = True
            continue
        if inside and line and not line[0].isspace():
            match = _EXCEPTION_LINE.match(line)
            if match:
                name = match.group(1).rsplit(".", 1)[-1]
            inside = False
    return name


def arguments_valid(arguments, schema: dict) -> bool:
    """Required keys present, declared types right, no unknown keys when the schema forbids them."""
    if not isinstance(arguments, dict):
        return False
    props = schema.get("properties") or {}
    if any(key not in arguments for key in schema.get("required") or []):
        return False
    if schema.get("additionalProperties") is False and any(key not in props for key in arguments):
        return False
    for key, value in arguments.items():
        expected = _JSON_TYPES.get((props.get(key) or {}).get("type"))
        if expected is None:
            continue
        if expected is int and isinstance(value, bool):
            return False
        if not isinstance(value, expected):
            return False
    return True


def tool_exception(name: str, output: str, arguments=None, schema: dict | None = None):
    """Exception class name when a harness tool program crashed on valid arguments, else None."""
    if name not in HARNESS_TOOLS:
        return None
    exc = traceback_exception(output)
    if exc not in IMPLEMENTATION_ERRORS:
        return None
    if schema is not None and not arguments_valid(arguments, schema):
        return None
    return exc


def harness_tool_errors(events: list[dict], schemas: dict[str, dict]) -> list[dict]:
    """One row per tool.result whose harness program crashed. Arguments come from the matching tool.intent."""
    intents = {}
    for event in events:
        if event.get("event_type") == "tool.intent":
            intents[event.get("tool_call_id")] = (event.get("payload") or {}).get("arguments")
    rows = []
    for event in events:
        if event.get("event_type") != "tool.result":
            continue
        payload = event.get("payload") or {}
        name = payload.get("name")
        exc = tool_exception(name, payload.get("output") or "", intents.get(event.get("tool_call_id")), schemas.get(name))
        if exc:
            rows.append({"tool": name, "call_id": event.get("tool_call_id"), "exception": exc})
    return rows


def tool_schemas() -> dict[str, dict]:
    """Parameter schemas of the harness tools, as the model sees them."""
    from src.tools.edit import EditFileTool
    from src.tools.files import ListDirTool, ReadFileTool, WriteFileTool
    from src.tools.patch import SearchReplaceTool
    from src.tools.retrieve import RetrieveCodeTool
    from src.tools.search import SearchCodeTool

    tools = [ReadFileTool(), ListDirTool(), WriteFileTool(), EditFileTool(), SearchReplaceTool(), SearchCodeTool(),
             RetrieveCodeTool()]
    return {tool.name: tool.parameters for tool in tools}
