#!/usr/bin/env python3
"""Print an anonymized, read-only quality report for the Vechasu catalog."""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.catalog_data_quality import build_quality_report  # noqa: E402


if __name__ == "__main__":
    print(json.dumps(build_quality_report(), ensure_ascii=False, indent=2, sort_keys=True))
