"""Convert contract records into indexable documents."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


METADATA_FIELDS = [
    "id",
    "source_site",
    "external_id",
    "url",
    "agency",
    "naics",
    "posted_date",
    "contract_type",
    "place_of_performance",
    "title",
    "organization",
    "deadline",
    "award_date",
    "contract_value",
    "award_status",
    "acq_strategy",
    "source_listing_id",
    "date_scraped",
]

TEXT_FIELDS = [
    "title",
    "agency",
    "organization",
    "contract_type",
    "naics",
    "place_of_performance",
    "award_status",
    "acq_strategy",
    "contract_value",
    "posted_date",
    "deadline",
    "description",
]


@dataclass(frozen=True)
class Document:
    doc_id: str
    text: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Document":
        return cls(doc_id=str(value["doc_id"]), text=str(value["text"]), metadata=dict(value["metadata"]))


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\x00", " ").strip()
    return " ".join(text.split())


def normalize_metadata(record: dict[str, Any]) -> dict[str, Any]:
    return {field: _clean(record.get(field)) for field in METADATA_FIELDS if _clean(record.get(field))}


def record_to_text(record: dict[str, Any]) -> str:
    lines: list[str] = []
    for field in TEXT_FIELDS:
        value = _clean(record.get(field))
        if value:
            label = field.replace("_", " ").title()
            lines.append(f"{label}: {value}")

    raw_response = _clean(record.get("raw_response"))
    if raw_response and not _clean(record.get("description")):
        lines.append(f"Raw Source Text: {raw_response[:4000]}")

    return "\n".join(lines).strip()


def records_to_documents(records: Iterable[dict[str, Any]]) -> list[Document]:
    documents: list[Document] = []
    for index, record in enumerate(records, start=1):
        metadata = normalize_metadata(record)
        doc_id = metadata.get("id") or metadata.get("external_id") or f"record-{index}"
        text = record_to_text(record)
        if not text:
            continue
        documents.append(Document(doc_id=str(doc_id), text=text, metadata=metadata))
    return documents
