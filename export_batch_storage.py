"""Shared batch state for Autoscale workers.

Legacy files are read only for the initial generation. A generation pointer in
App Storage makes a clear visible to every worker, including newly started ones.
Each row is a separate object so concurrent appends cannot overwrite one another.
"""

import json
import time
from uuid import uuid4

from replit.object_storage import Client
from replit.object_storage.errors import ObjectNotFoundError

_storage = Client()
_PREFIX = "export_batches/"


def _base(kind: str) -> str:
    if kind not in {"pinterest", "vk"}:
        raise ValueError("Unknown export batch")
    return f"{_PREFIX}{kind}/"


def generation(kind: str) -> str:
    try:
        return _storage.download_as_text(_base(kind) + "current")
    except ObjectNotFoundError:
        return "initial"


def rows(kind: str, legacy_rows: list[dict]) -> list[dict]:
    current = generation(kind)
    result = list(legacy_rows) if current == "initial" else []
    prefix = _base(kind) + f"generations/{current}/"
    for obj in _storage.list(prefix=prefix):
        result.append(json.loads(_storage.download_as_text(obj.name)))
    return result


def append(kind: str, records: list[dict]) -> None:
    current = generation(kind)
    prefix = _base(kind) + f"generations/{current}/"
    for record in records:
        key = f"{prefix}{time.time_ns():020d}-{uuid4().hex}.json"
        _storage.upload_from_text(key, json.dumps(record, ensure_ascii=False))


def archive_and_clear(kind: str, archived_name: str, records: list[dict]) -> None:
    # Write the durable archive first; if this fails, the current batch remains.
    _storage.upload_from_text(
        _base(kind) + f"archives/{archived_name}",
        json.dumps(records, ensure_ascii=False),
    )
    _storage.upload_from_text(_base(kind) + "current", uuid4().hex)