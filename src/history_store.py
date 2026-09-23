# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, RLock
from uuid import UUID, uuid4

from .history_events import (
    HISTORY_SCHEMA_NAME,
    HISTORY_SCHEMA_VERSION,
    CleanupEvent,
    HistoryEventValidationError,
    event_from_dict,
    event_to_json,
)

SOURCE_ID_PROFILE_KEY = "card_janitor_history_source_id"
HISTORY_FILENAME = "cleanup-history.jsonl"


class HistoryStorageError(OSError):
    """A local cleanup-history filesystem operation failed."""


@dataclass(frozen=True)
class HistoryRecord:
    line_number: int
    raw_json: str
    event: CleanupEvent | None = None
    error: str | None = None
    unsupported_schema: str | None = None


@dataclass(frozen=True)
class HistoryPage:
    records: tuple[HistoryRecord, ...]
    next_before_line: int | None


_locks_guard = Lock()
_path_locks: dict[str, RLock] = {}


def _path_lock(path: Path) -> RLock:
    key = str(path.absolute())
    with _locks_guard:
        return _path_locks.setdefault(key, RLock())


def default_history_root() -> Path:
    return Path(__file__).parent / "user_files" / "history"


def _canonical_source_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError):
        return None
    canonical = str(parsed)
    return canonical if value == canonical else None


def source_id_for_profile(profile: dict[str, object]) -> str:
    existing = existing_source_id_for_profile(profile)
    if existing is not None:
        return existing
    source_id = str(uuid4())
    profile[SOURCE_ID_PROFILE_KEY] = source_id
    return source_id


def existing_source_id_for_profile(profile: dict[str, object]) -> str | None:
    """Return an existing history identity without creating one while merely viewing."""
    return _canonical_source_id(profile.get(SOURCE_ID_PROFILE_KEY))


def _decode_record(line_number: int, line: str) -> HistoryRecord:
    raw_json = line.rstrip("\r\n")
    try:
        value = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        return HistoryRecord(line_number, raw_json, error=f"invalid JSON: {exc.msg}")
    if not isinstance(value, dict):
        return HistoryRecord(line_number, raw_json, error="event must be an object")
    schema = value.get("schema")
    if not isinstance(schema, dict):
        return HistoryRecord(line_number, raw_json, error="event has no valid schema")
    name = schema.get("name")
    version = schema.get("version")
    if name != HISTORY_SCHEMA_NAME or isinstance(version, bool) or not isinstance(version, int):
        return HistoryRecord(line_number, raw_json, error="event has no supported schema identity")
    if version != HISTORY_SCHEMA_VERSION:
        return HistoryRecord(
            line_number,
            raw_json,
            unsupported_schema=f"{name} v{version}",
        )
    try:
        event = event_from_dict(value)
    except HistoryEventValidationError as exc:
        return HistoryRecord(line_number, raw_json, error=str(exc))
    return HistoryRecord(line_number, raw_json, event=event)


def _decode_bytes_record(line_number: int, line: bytes) -> HistoryRecord:
    try:
        decoded = line.decode("utf-8")
    except UnicodeDecodeError:
        return HistoryRecord(
            line_number,
            line.decode("utf-8", errors="replace").rstrip("\r\n"),
            error="line is not valid UTF-8",
        )
    return _decode_record(line_number, decoded)


def _flush_file(handle: object) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def _flush_directory(path: Path) -> None:
    flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


class HistoryStore:
    def __init__(self, source_id: str, *, root: Path | None = None) -> None:
        canonical = _canonical_source_id(source_id)
        if canonical is None:
            raise ValueError("history source_id must be a canonical UUID")
        self.source_id = canonical
        self.root = root or default_history_root()
        self.directory = self.root / canonical
        self.path = self.directory / HISTORY_FILENAME
        self._lock = _path_lock(self.path)

    def append(self, event: CleanupEvent) -> None:
        if event.source_id != self.source_id:
            raise HistoryEventValidationError("event source does not match history store")
        encoded = event_to_json(event) + "\n"
        try:
            with self._lock:
                created = not self.directory.exists()
                self.directory.mkdir(parents=True, exist_ok=True)
                if created:
                    _flush_directory(self.directory.parent)
                with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(encoded)
                    _flush_file(handle)
        except OSError as exc:
            raise HistoryStorageError(f"could not append cleanup history: {exc}") from exc

    def read_recent(
        self,
        *,
        limit: int = 100,
        before_line: int | None = None,
    ) -> HistoryPage:
        if limit <= 0:
            raise ValueError("history page limit must be positive")
        if before_line is not None and before_line <= 1:
            return HistoryPage((), None)
        if not self.path.exists():
            return HistoryPage((), None)
        records: deque[HistoryRecord] = deque(maxlen=limit)
        eligible = 0
        try:
            with self._lock, self.path.open("rb") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if before_line is not None and line_number >= before_line:
                        break
                    eligible += 1
                    records.append(_decode_bytes_record(line_number, line))
        except OSError as exc:
            raise HistoryStorageError(f"could not read cleanup history: {exc}") from exc
        newest_first = tuple(reversed(records))
        next_before = min(item.line_number for item in records) if eligible > len(records) else None
        return HistoryPage(newest_first, next_before)

    def export(
        self,
        destination: Path,
    ) -> int:
        if destination.absolute() == self.path.absolute():
            raise ValueError("history export destination cannot be the active log")
        destination = destination.absolute()
        exported = 0
        temporary: Path | None = None
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as output:
                temporary = Path(output.name)
                with self._lock:
                    if self.path.exists():
                        with self.path.open("rb") as source:
                            for line in source:
                                output.write(line)
                                exported += 1
                _flush_file(output)
            temporary.replace(destination)
            _flush_directory(destination.parent)
        except OSError as exc:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise HistoryStorageError(f"could not export cleanup history: {exc}") from exc
        return exported

    def clear(self) -> bool:
        with self._lock:
            staged = (
                list(self.root.glob(f".{self.source_id}.clearing-*")) if self.root.exists() else []
            )
            if self.directory.exists():
                clearing = self.root / f".{self.source_id}.clearing-{uuid4()}"
                try:
                    self.directory.replace(clearing)
                    _flush_directory(self.root)
                except OSError as exc:
                    raise HistoryStorageError(
                        f"could not prepare cleanup history for clearing: {exc}"
                    ) from exc
                staged.append(clearing)
            if not staged:
                return False
            try:
                for path in staged:
                    shutil.rmtree(path)
                _flush_directory(self.root)
            except OSError as exc:
                raise HistoryStorageError(f"could not clear cleanup history: {exc}") from exc
            return True
