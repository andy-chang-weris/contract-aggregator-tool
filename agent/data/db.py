"""Read-only PostgreSQL access for contract postings."""

from __future__ import annotations

from typing import Any

from configuration import Settings


POSTING_FIELDS = [
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
    "description",
    "deadline",
    "award_date",
    "contract_value",
    "award_status",
    "acq_strategy",
    "source_listing_id",
    "date_scraped",
    "raw_response",
]


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def load_contracts_from_db(settings: Settings) -> list[dict[str, Any]]:
    """Load contract records from the existing postings table with a read-only SELECT."""
    try:
        import psycopg2  # type: ignore
        from psycopg2.extras import RealDictCursor  # type: ignore
    except Exception as exc:  # pragma: no cover - exercised when dependency missing
        raise RuntimeError(
            "psycopg2-binary is required for DB access. Install requirements.txt or use --source sample."
        ) from exc

    query = f"""
        SELECT {", ".join(POSTING_FIELDS)}
        FROM postings
        ORDER BY COALESCE(posted_date, date_scraped) DESC NULLS LAST, id DESC
        LIMIT %s
    """

    conn = psycopg2.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
        cursor_factory=RealDictCursor,
        connect_timeout=10,
    )
    try:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as cursor:
            cursor.execute(query, (settings.db_limit,))
            rows = cursor.fetchall()
    finally:
        conn.close()

    return [{key: _json_safe(value) for key, value in dict(row).items()} for row in rows]

