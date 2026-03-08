#!/usr/bin/env python3
"""
CLI entry point for census tools.

Usage:
    python scan_census.py scan /
    python scan_census.py scan /Users --output=users.census.zst
    python scan_census.py scan-s3 s3://bucket/prefix --workers=16
    python scan_census.py lod root.census.zst --target=500000
    python scan_census.py load census_output.census.zst
    python scan_census.py merge /tmp/scan_abc123/ --output=merged.census.zst
    python scan_census.py analyze census_output.census.zst
"""
from __future__ import annotations

import fire

from census import CLI

if __name__ == '__main__':
    fire.Fire(CLI)
