"""Contract record loading from sample data, SQL dumps, or PostgreSQL."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from configuration import Settings
from data.db import load_contracts_from_db


class DataSourceError(RuntimeError):
    """Raised when contract records cannot be loaded from the requested source."""


COPY_RE = re.compile(
    r"^COPY\s+(?:(?:public\.)?postings|postings)\s*\((?P<fields>[^)]*)\)\s+FROM\s+stdin;",
    re.IGNORECASE,
)


def load_sample_contracts(settings: Settings) -> list[dict[str, Any]]:
    """Load bundled JSON records used by the offline chatbot path."""
    return _load_json_records(settings.sample_data_path, "sample")


def load_contracts_from_dump(settings: Settings) -> list[dict[str, Any]]:
    """Load records from a plain-text PostgreSQL COPY dump."""
    path = settings.dump_path
    if not path.exists():
        raise DataSourceError(f"Dump file does not exist: {path}")

    try:
        sql_text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise DataSourceError(
            "Only plain-text SQL dumps are supported by this loader. "
            "Export with pg_dump --format=plain or convert the dump before indexing."
        ) from exc

    records = parse_postings_sql(sql_text)
    if not records:
        raise DataSourceError(f"No postings COPY records found in dump: {path}")
    return records


def parse_postings_sql(sql_text: str) -> list[dict[str, Any]]:
    """Parse plain PostgreSQL COPY data for the postings table."""
    records: list[dict[str, Any]] = []
    active_fields: list[str] | None = None

    for raw_line in sql_text.splitlines():
        line = raw_line.rstrip("\n")

        if active_fields is None:
            match = COPY_RE.match(line.strip())
            if match:
                active_fields = [
                    field.strip().strip('"')
                    for field in match.group("fields").split(",")
                    if field.strip()
                ]
            continue

        if line == r"\.":
            active_fields = None
            continue

        values = _split_copy_row(line)
        if len(values) < len(active_fields):
            values.extend([None] * (len(active_fields) - len(values)))
        record = dict(zip(active_fields, values[: len(active_fields)]))
        records.append(record)

    return records


def load_contract_records(settings: Settings) -> tuple[list[dict[str, Any]], str]:
    """Load contract records and return a source label for status messages."""
    source = settings.data_source.strip().lower()

    if source == "sample":
        return load_sample_contracts(settings), "sample"
    if source == "dump":
        return load_contracts_from_dump(settings), "dump"
    if source == "db":
        return load_contracts_from_db(settings), "db"
    if source != "auto":
        raise DataSourceError("RAG_DATA_SOURCE must be one of: auto, db, dump, sample")

    errors: list[str] = []
    try:
        records = load_contracts_from_db(settings)
        if records:
            return records, "db"
        errors.append("db returned no records")
    except Exception as exc:
        errors.append(f"db unavailable: {exc}")

    if settings.dump_path.exists():
        try:
            records = load_contracts_from_dump(settings)
            if records:
                return records, "dump"
            errors.append("dump returned no records")
        except Exception as exc:
            errors.append(f"dump unavailable: {exc}")

    try:
        return load_sample_contracts(settings), "sample"
    except Exception as exc:
        errors.append(f"sample unavailable: {exc}")
        raise DataSourceError("Unable to load any contract records. " + " | ".join(errors)) from exc


def _load_json_records(path: Path, source_label: str) -> list[dict[str, Any]]:
    if not path.exists():
        raise DataSourceError(f"{source_label.title()} data file does not exist: {path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DataSourceError(f"{source_label.title()} data file is not valid JSON: {path}") from exc

    if not isinstance(payload, list):
        raise DataSourceError(f"{source_label.title()} data must be a JSON list: {path}")

    records = [dict(item) for item in payload if isinstance(item, dict)]
    if not records:
        raise DataSourceError(f"{source_label.title()} data contains no object records: {path}")
    return records


def _split_copy_row(line: str) -> list[Any]:
    return [_decode_copy_value(value) for value in line.split("\t")]


def _decode_copy_value(value: str) -> Any:
    if value == r"\N":
        return None

    replacements = {
        r"\b": "\b",
        r"\f": "\f",
        r"\n": "\n",
        r"\r": "\r",
        r"\t": "\t",
        r"\\": "\\",
    }
    decoded = value
    for escaped, replacement in replacements.items():
        decoded = decoded.replace(escaped, replacement)
    return decoded
