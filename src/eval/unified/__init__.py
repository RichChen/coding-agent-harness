"""Compatibility contracts shared by the evaluation adapters.

The unified layer deliberately wraps the existing runners.  It does not make
mini, repository pytest, and SWE-bench grading use the same execution model.
"""

from .contracts import (
    SCHEMA_VERSION,
    UnifiedContractError,
    GraderSpec,
    RunReceipt,
    RunSpec,
    TaskSpec,
    canonical_json_hash,
    normalize_legacy_result,
)
from .legacy import load_run_rows, normalize_run_rows, summarize_receipts
from .adapters import (
    ADAPTER_KINDS,
    ADAPTER_SCHEMA,
    AdapterError,
    AdapterRegistry,
    MiniAdapter,
    RepoPytestAdapter,
    SwebenchAdapter,
    ManifestEntry,
    MockAdapter,
    load_manifest_rows,
)

__all__ = [
    "SCHEMA_VERSION",
    "UnifiedContractError",
    "GraderSpec",
    "RunReceipt",
    "RunSpec",
    "TaskSpec",
    "canonical_json_hash",
    "normalize_legacy_result",
    "load_run_rows",
    "normalize_run_rows",
    "summarize_receipts",
    "ADAPTER_KINDS",
    "ADAPTER_SCHEMA",
    "AdapterError",
    "AdapterRegistry",
    "MiniAdapter",
    "RepoPytestAdapter",
    "SwebenchAdapter",
    "ManifestEntry",
    "MockAdapter",
    "load_manifest_rows",
]
