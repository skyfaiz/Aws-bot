"""Comment-preserving edits of single scalar values in config.yaml."""
import json
import re

import yaml

_KEY_RE = re.compile(r"^(\s*)([A-Za-z_][\w]*)\s*:(.*)$")
_VAL_RE = re.compile(r"""^(\s*)("(?:[^"\\]|\\.)*"|'[^']*'|[^#]*?)(\s+#.*|\s*)$""")
BLOCKED_PREFIXES = ("api.access_token", "paths.")


def flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        out.update(flatten(v, key + ".") if isinstance(v, dict) else {key: v})
    return out


def get_leaf(cfg: dict, path: str):
    cur = cfg
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(path)
        cur = cur[part]
    if isinstance(cur, dict):
        raise KeyError(f"{path} is a section, not a setting")
    return cur


def coerce(old, raw: str):
    """Convert the user's text to the same type as the existing value."""
    raw = raw.strip()
    if isinstance(old, bool):
        if raw.lower() in ("true", "on", "yes", "1"):
            return True
        if raw.lower() in ("false", "off", "no", "0"):
            return False
        raise ValueError("expected true/false")
    if isinstance(old, int):
        return int(raw)
    if isinstance(old, float):
        return float(raw)
    if old is None:
        if raw.lower() in ("null", "none", ""):
            return None
        for cast in (int, float):
            try:
                return cast(raw)
            except ValueError:
                pass
        return raw.strip("\"'")
    if raw.lower() in ("null", "none"):
        return None
    return raw.strip("\"'")


def _fmt(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return json.dumps(v)
    return repr(v)


def set_yaml_value(text: str, path: str, value) -> str:
    """Replace the scalar at dotted `path`, keeping indentation and trailing comments."""
    target = path.split(".")
    stack: list[tuple[int, str]] = []
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _KEY_RE.match(line.rstrip("\r\n"))
        if not m:
            continue
        indent, key, rest = len(m.group(1)), m.group(2), m.group(3)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if [k for _, k in stack] + [key] == target:
            vm = _VAL_RE.match(rest)
            if not rest.strip() or rest.strip().startswith("#") or not vm:
                raise KeyError(f"{path} is not a scalar setting")
            eol = line[len(line.rstrip("\r\n")):]
            lines[i] = f"{m.group(1)}{key}: {_fmt(value)}{vm.group(3) if vm.group(3).strip() else ''}{eol}"
            return "".join(lines)
        if not rest.strip() or rest.strip().startswith("#"):
            stack.append((indent, key))
    raise KeyError(path)
