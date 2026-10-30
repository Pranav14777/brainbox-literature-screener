"""Load the supplied synthetic dataset into Records."""
from __future__ import annotations

import json
from pathlib import Path

from screener.models import Record


def load_synthetic_records(path: Path) -> list[Record]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        Record(
            record_id=r["record_id"],
            source_type="synthetic",  # fixed here, never taken from file content
            title=r["title"],
            abstract=r["abstract"],
            publication_year=r["publication_year"],
            publication_types=r.get("publication_types", []),
            pmid=r.get("pmid"),
        )
        for r in data["records"]
    ]
