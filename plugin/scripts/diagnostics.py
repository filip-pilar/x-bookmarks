#!/usr/bin/env python3
"""Export an existing library's sanitized diagnostics without starting its service."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from diagnostics import export_report
print(json.dumps(export_report(), indent=2))
