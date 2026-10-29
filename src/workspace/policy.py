"""Frozen v1 profile policy; consumers use its complete hashed configuration."""
from dataclasses import dataclass
from pathlib import PurePosixPath

from src.workspace.contracts import rules_id


@dataclass(frozen=True)
class CapturePolicy:
    editable_root: str = "sphinx"
    max_entries: int = 10000
    max_tree_bytes: int = 64 * 1024 * 1024
    max_delta_files: int = 200
    max_delta_bytes: int = 4 * 1024 * 1024
    max_corpus_bytes: int = 8 * 1024 * 1024
    max_python_file_bytes: int = 1024 * 1024
    max_nodes: int = 100000

    def __post_init__(self):
        from src.workspace.contracts import validate_path
        validate_path(self.editable_root)
        for name, value in vars(self).items():
            if name != "editable_root" and (type(value) is not int or value <= 0):
                raise ValueError("policy limits must be positive integers")

    def config(self):
        return dict(version="capture-policy-v1", **vars(self),
                    excludes={"directories": [".git", "__pycache__", ".pytest_cache"],
                              "names": [".coverage"], "suffixes": [".pyc", ".pyo"]},
                    private_names=[".env*", ".ssh", ".aws", ".grader_private", ".harness_artifacts"],
                    path_policy="workspace-v1", type_policy="regular-only-no-links",
                    mode_policy="freeze-existing", editable_suffix=".py",
                    frozen_names=["conftest.py", "sitecustomize.py", "usercustomize.py", "setup.py"],
                    unsupported_parts=["build", "*.egg-info", ".venv", "venv", "env"],
                    materialization="clear-caches-v1", helper_version="trusted-scan-v1")

    @property
    def rules_id(self):
        return rules_id(self.config())

    def violations(self, base, entries):
        old, new = {e.path: e for e in base}, {e.path: e for e in entries}
        result = []
        for path in sorted(old.keys() | new.keys()):
            before, after = old.get(path), new.get(path)
            if before == after:
                continue
            parts = PurePosixPath(path).parts
            allowed = (path.startswith(self.editable_root + "/") and path.endswith(".py")
                       and parts[-1] not in self.config()["frozen_names"]
                       and not any(p in ("build", ".venv", "venv", "env") or p.endswith(".egg-info") for p in parts)
                       and not (before and after and before.mode != after.mode))
            if not allowed:
                result.append({"reason": "unsupported_change", "path": path})
        return tuple(result)
