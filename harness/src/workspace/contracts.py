"""Immutable workspace-v1 data and identities; no capture or filesystem I/O.

IDs are ``domain:sha256(domain + NUL + canonical_json(payload))``. JSON objects
have string keys, arrays retain order, and floats are rejected (including audit
metadata). Callers must express fractional configuration as explicit decimal
strings or scaled integers. Sets of paths are sorted by UTF-8 bytes here.

Construction checks internal consistency. ``validate_against_base`` additionally
checks the complete base-relative delta; ``validate_blobs`` verifies referenced
bytes through an injected reader. None of these proves capture quiescence or
decides profile policy. A failed capture must never be represented by this DTO.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields
from types import MappingProxyType
from typing import Any, Protocol
from uuid import UUID

__all__ = [
    "ContractError", "FileEntry", "ArtifactRef", "WorkspaceCapture", "BlobReader",
    "canonical_json", "content_id", "blob_sha256", "validate_path", "tree_id",
    "rules_id", "corpus_rules_id", "candidate_id", "corpus_id", "index_id",
    "validation_key", "read_verified_blob",
]


class ContractError(ValueError):
    """Malformed data, inconsistent identities, or corrupt referenced bytes."""


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ContractError(f"{label} must be a nonempty string without NUL")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ContractError(f"{label} must be valid UTF-8") from exc
    return value


def _freeze(value: Any) -> Any:
    if value is None or type(value) in (bool, int):
        return value
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ContractError("JSON strings must be valid UTF-8") from exc
        return str(value)
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ContractError("JSON object keys must be strings")
        return MappingProxyType({_freeze(key): _freeze(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(child) for child in value)
    raise ContractError("JSON accepts only null, bool, int, string, object, array; no floats")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_plain(child) for child in value]
    return value


def canonical_json(value: Any) -> bytes:
    """Canonical UTF-8 JSON bytes. No normalization or numeric coercion."""
    return json.dumps(_plain(_freeze(value)), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def blob_sha256(data: bytes) -> str:
    """Hash raw file bytes, preserving encoding and line endings."""
    if not isinstance(data, bytes):
        raise ContractError("blob must be bytes")
    return hashlib.sha256(data).hexdigest()


def content_id(domain: str, payload: Any) -> str:
    """Reusable domain-separated identity (also for check/environment configs)."""
    _text(domain, "domain")
    if any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in domain):
        raise ContractError("domain must contain only lowercase ASCII letters, digits, hyphens")
    digest = hashlib.sha256(domain.encode("ascii") + b"\0" + canonical_json(payload)).hexdigest()
    return f"{domain}:{digest}"


def _sha(value: Any) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ContractError("sha256 must be 64 lowercase hex digits")


def _id(value: Any, domain: str) -> None:
    if not isinstance(value, str) or not value.startswith(domain + ":"):
        raise ContractError(f"expected {domain} identity")
    _sha(value[len(domain) + 1:])


def _nonnegative(value: Any, label: str) -> None:
    if type(value) is not int or value < 0:
        raise ContractError(f"{label} must be a nonnegative integer")


def validate_path(path: str) -> str:
    """Validate a POSIX relative path, preserving original Unicode and case."""
    _text(path, "path")
    if (path.startswith("/") or "\\" in path
            or (len(path) >= 2 and path[0].isascii() and path[0].isalpha() and path[1] == ":")
            or any(part in ("", ".", "..") for part in path.split("/"))):
        raise ContractError(f"invalid POSIX relative path: {path!r}")
    return path


def _paths(paths: Iterable[str]) -> tuple[str, ...]:
    if isinstance(paths, (str, bytes, Mapping)):
        raise ContractError("paths must be a collection, not a string or object")
    result = tuple(validate_path(path) for path in paths)
    if len(set(result)) != len(result):
        raise ContractError("duplicate path")
    spellings: dict[str, str] = {}
    file_paths = set(result)
    for path in result:
        parts = path.split("/")
        for count in range(1, len(parts) + 1):
            prefix = "/".join(parts[:count])
            folded = prefix.casefold()
            if folded in spellings and spellings[folded] != prefix:
                raise ContractError("case-folding path collision")
            spellings[folded] = prefix
            if count < len(parts) and prefix in file_paths:
                raise ContractError("file/directory ancestor conflict")
    return tuple(sorted(result, key=lambda path: path.encode("utf-8")))


def _object(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError("expected JSON object")
    return _freeze(value)


@dataclass(frozen=True)
class FileEntry:
    path: str
    kind: str
    mode: int
    size: int
    sha256: str

    def __post_init__(self) -> None:
        validate_path(self.path)
        if self.kind != "regular":
            raise ContractError("workspace-v1 supports only regular files")
        _nonnegative(self.mode, "mode")
        if self.mode > 0o7777:
            raise ContractError("mode must be stat.S_IMODE, without file type bits")
        _nonnegative(self.size, "size")
        _sha(self.sha256)

    def to_dict(self) -> dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in fields(self)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> FileEntry:
        return _from_dict(cls, value)


@dataclass(frozen=True)
class ArtifactRef:
    sha256: str
    size: int
    key: str

    def __post_init__(self) -> None:
        _sha(self.sha256)
        _nonnegative(self.size, "size")
        # A logical relative key only. Ownership/resolution belongs to the store.
        validate_path(self.key)
        if ":" in self.key:
            raise ContractError("artifact key cannot be a URI or drive path")

    def to_dict(self) -> dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in fields(self)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ArtifactRef:
        return _from_dict(cls, value)


def _from_dict(cls: type, value: Mapping[str, Any]) -> Any:
    if not isinstance(value, Mapping) or set(value) != {field.name for field in fields(cls)}:
        raise ContractError(f"{cls.__name__} requires exactly its declared fields")
    return cls(**value)


def _entries(entries: Iterable[FileEntry]) -> tuple[FileEntry, ...]:
    result = tuple(entries)
    if any(not isinstance(entry, FileEntry) for entry in result):
        raise ContractError("entries must contain FileEntry")
    order = _paths(entry.path for entry in result)
    by_path = {entry.path: entry for entry in result}
    return tuple(by_path[path] for path in order)


def tree_id(entries: Iterable[FileEntry]) -> str:
    return content_id("tree-v1", [entry.to_dict() for entry in _entries(entries)])


def rules_id(config: Mapping[str, Any]) -> str:
    """Hash the full candidate policy config, not a caller-chosen version label."""
    return content_id("rules-v1", _object(config))


def corpus_rules_id(config: Mapping[str, Any]) -> str:
    return content_id("corpus-rules-v1", _object(config))


def candidate_id(*, base_revision: str, image_digest: str, base_manifest_id: str,
                 manifest_id: str, rules_id: str) -> str:
    _text(base_revision, "base_revision")
    _id(image_digest, "sha256")
    _id(base_manifest_id, "tree-v1")
    _id(manifest_id, "tree-v1")
    _id(rules_id, "rules-v1")
    return content_id("candidate-v1", dict(base_revision=base_revision, image_digest=image_digest,
                      base_manifest_id=base_manifest_id, manifest_id=manifest_id, rules_id=rules_id))


def corpus_id(files: Iterable[tuple[FileEntry, str]], *, corpus_rules_id: str) -> str:
    """Hash an explicitly selected Python subset with source/test/test_support kinds.

    Selection/decoding/AST policy belongs to A. Mode and size are deliberately
    absent from this identity; kind here means material kind, not file type.
    """
    _id(corpus_rules_id, "corpus-rules-v1")
    selected = tuple(files)
    entries = _entries(entry for entry, _ in selected)
    kinds = {entry.path: kind for entry, kind in selected}
    for entry in entries:
        if not entry.path.endswith(".py") or kinds[entry.path] not in ("source", "test", "test_support"):
            raise ContractError("corpus requires Python paths and a supported material kind")
    return content_id("corpus-v1", dict(corpus_rules_id=corpus_rules_id,
                      files=[dict(path=e.path, sha256=e.sha256, kind=kinds[e.path]) for e in entries]))


def index_id(*, corpus_id: str, chunker: Mapping[str, Any], tokenizer: Mapping[str, Any],
             encoder: Mapping[str, Any], bm25: Mapping[str, Any], rrf: Mapping[str, Any], mode: str) -> str:
    """Callers supply full versioned configs, including encoder revision/precision.

    Explicit bm25 mode may use an empty encoder config; no fallback is implied.
    Config schemas belong to the consumers; no floats are coerced here.
    """
    _id(corpus_id, "corpus-v1")
    if mode not in ("bm25", "hybrid"):
        raise ContractError("index mode must be bm25 or hybrid")
    return content_id("index-v1", dict(corpus_id=corpus_id, chunker=_object(chunker),
                      tokenizer=_object(tokenizer), encoder=_object(encoder), bm25=_object(bm25),
                      rrf=_object(rrf), mode=mode))


def validation_key(*, candidate_id: str, check_id: str, environment_id: str) -> str:
    _id(candidate_id, "candidate-v1")
    _id(check_id, "check-v1")
    _id(environment_id, "environment-v1")
    return content_id("validation-v1", dict(candidate_id=candidate_id, check_id=check_id,
                      environment_id=environment_id))


class BlobReader(Protocol):
    """Injected controlled artifact store; never an arbitrary host root."""

    def read(self, ref: ArtifactRef) -> bytes: ...


def read_verified_blob(reader: BlobReader, ref: ArtifactRef) -> bytes:
    data = reader.read(ref)
    if not isinstance(data, bytes) or len(data) != ref.size:
        raise ContractError("blob size/type mismatch")
    if blob_sha256(data) != ref.sha256:
        raise ContractError("blob sha256 mismatch")
    return data


def _refs(items: Iterable[tuple[str, ArtifactRef]]) -> tuple[tuple[str, ArtifactRef], ...]:
    pairs = tuple(items)
    if any(not isinstance(pair, (list, tuple)) or len(pair) != 2
           or not isinstance(pair[1], ArtifactRef) for pair in pairs):
        raise ContractError("blob references must be (path, ArtifactRef) pairs")
    order = _paths(pair[0] for pair in pairs)
    refs = dict(pairs)
    return tuple((path, refs[path]) for path in order)


@dataclass(frozen=True)
class WorkspaceCapture:
    schema_version: str
    capture_id: str
    origin: Mapping[str, Any]
    base_revision: str
    image_digest: str
    base_manifest_id: str
    manifest_id: str
    candidate_id: str
    rules_id: str
    entries: tuple[FileEntry, ...]
    changed: tuple[tuple[str, ArtifactRef], ...]
    deleted: tuple[str, ...]
    corpus_blobs: tuple[tuple[str, ArtifactRef], ...]
    excluded: tuple[Mapping[str, Any], ...]
    admissible: bool
    violations: tuple[Mapping[str, Any], ...]
    captured_at: str
    barrier_receipt: ArtifactRef

    def __post_init__(self) -> None:
        if self.schema_version != "workspace-v1":
            raise ContractError("unsupported workspace schema")
        _text(self.capture_id, "capture_id")
        try:
            UUID(self.capture_id)
        except ValueError as exc:
            raise ContractError("capture_id must be UUID") from exc
        _text(self.captured_at, "captured_at")
        if type(self.admissible) is not bool:
            raise ContractError("admissible must be bool")
        if not isinstance(self.barrier_receipt, ArtifactRef):
            raise ContractError("barrier_receipt must be ArtifactRef")
        for name in ("entries", "changed", "deleted", "corpus_blobs", "excluded", "violations"):
            if not isinstance(getattr(self, name), (tuple, list)):
                raise ContractError(f"{name} must be an array")
        for name, value in (("origin", _object(self.origin)), ("entries", _entries(self.entries)),
                            ("changed", _refs(self.changed)), ("deleted", _paths(self.deleted)),
                            ("corpus_blobs", _refs(self.corpus_blobs)),
                            ("excluded", tuple(_object(item) for item in self.excluded)),
                            ("violations", tuple(_object(item) for item in self.violations))):
            object.__setattr__(self, name, value)
        if self.admissible and self.violations:
            raise ContractError("admissible capture cannot have violations")
        if not self.admissible and not self.violations:
            raise ContractError("inadmissible capture requires violations")
        if tree_id(self.entries) != self.manifest_id:
            raise ContractError("manifest_id mismatch")
        expected = candidate_id(base_revision=self.base_revision, image_digest=self.image_digest,
                                base_manifest_id=self.base_manifest_id, manifest_id=self.manifest_id,
                                rules_id=self.rules_id)
        if expected != self.candidate_id:
            raise ContractError("candidate_id mismatch")
        current = {entry.path: entry for entry in self.entries}
        if set(self.deleted) & (set(current) | {path for path, _ in self.changed}):
            raise ContractError("deleted path remains present or changed")
        # Exact membership also checks casing against the full manifest. Deleted
        # paths belong to the old tree, allowing delete+add renames/transitions.
        for name in ("changed", "corpus_blobs"):
            for path, ref in getattr(self, name):
                entry = current.get(path)
                if entry is None or (entry.sha256, entry.size) != (ref.sha256, ref.size):
                    raise ContractError(f"{name} reference does not match manifest")
                if name == "corpus_blobs" and not path.endswith(".py"):
                    raise ContractError("corpus_blobs requires Python paths")

    def validate_against_base(self, base: Iterable[FileEntry]) -> None:
        """Require all and only byte additions/changes and exact deletion tombstones.

        Mode changes remain representable but must carry an inadmissible violation.
        No filesystem permissions are inspected by this method.
        """
        entries = _entries(base)
        if tree_id(entries) != self.base_manifest_id:
            raise ContractError("base_manifest_id mismatch")
        old = {entry.path: entry for entry in entries}
        new = {entry.path: entry for entry in self.entries}
        changed = {path for path, entry in new.items() if path not in old or
                   (entry.sha256, entry.size) != (old[path].sha256, old[path].size)}
        if changed != {path for path, _ in self.changed} or set(old) - set(new) != set(self.deleted):
            raise ContractError("delta is not the complete base-relative byte delta")
        if self.admissible and any(old[path].mode != new[path].mode for path in old.keys() & new.keys()):
            raise ContractError("workspace-v1 does not admit mode changes")

    def validate_blobs(self, reader: BlobReader) -> None:
        """Verify every changed/corpus/barrier reference, including duplicate keys."""
        for ref in (self.barrier_receipt, *(ref for _, ref in self.changed),
                    *(ref for _, ref in self.corpus_blobs)):
            read_verified_blob(reader, ref)

    def to_dict(self) -> dict[str, Any]:
        result = {field.name: getattr(self, field.name) for field in fields(self)}
        result["entries"] = [entry.to_dict() for entry in self.entries]
        for name in ("changed", "corpus_blobs"):
            result[name] = [[path, ref.to_dict()] for path, ref in getattr(self, name)]
        result["barrier_receipt"] = self.barrier_receipt.to_dict()
        return _plain(result)

    def to_json(self) -> bytes:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> WorkspaceCapture:
        if not isinstance(value, Mapping) or set(value) != {field.name for field in fields(cls)}:
            raise ContractError("WorkspaceCapture requires exactly its declared fields")
        data = dict(value)
        for name in ("entries", "changed", "deleted", "corpus_blobs", "excluded", "violations"):
            if not isinstance(data[name], (tuple, list)):
                raise ContractError(f"{name} must be an array")
        data["entries"] = tuple(FileEntry.from_dict(item) for item in data["entries"])
        for name in ("changed", "corpus_blobs"):
            pairs = data[name]
            if not isinstance(pairs, (list, tuple)) or any(
                    not isinstance(pair, (list, tuple)) or len(pair) != 2 for pair in pairs):
                raise ContractError(f"invalid {name} reference pairs")
            data[name] = tuple((path, ArtifactRef.from_dict(ref)) for path, ref in pairs)
        data["barrier_receipt"] = ArtifactRef.from_dict(data["barrier_receipt"])
        return cls(**data)

    @classmethod
    def from_json(cls, data: str | bytes) -> WorkspaceCapture:
        if not isinstance(data, (str, bytes)):
            raise ContractError("workspace JSON must be str or UTF-8 bytes")
        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ContractError("duplicate JSON object key")
                result[key] = value
            return result

        try:
            # Decode explicitly: workspace-v1 is UTF-8, not JSON's UTF-16/32 autodetect.
            value = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data,
                               object_pairs_hook=unique_object)
        except (ValueError, UnicodeError) as exc:
            raise ContractError("invalid workspace JSON") from exc
        return cls.from_dict(value)
