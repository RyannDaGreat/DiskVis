"""
Census: disk usage scanning and visualization tools.

Submodules:
    io          - Read/write .jsonl.zst files
    scan        - Shared checkpoint/progress infrastructure
    scan_local  - Local filesystem DFS scanner
    scan_s3     - S3 ListObjectsV2 parallel scanner
    lod         - Level-of-detail reducer
"""
from __future__ import annotations

import time

from census.io import load_census, load_census_folder, save_census
from census.lod import analyze, lod
from census.scan_local import scan as scan_local


def _scan_s3_lazy(*args, **kwargs):
    """Lazy import to avoid boto3 dependency when not using S3."""
    from census.scan_s3 import scan
    return scan(*args, **kwargs)


def load(path: str) -> None:
    """
    Command. Load and print stats from a census file.

    Args:
        path (str): Path to .jsonl.zst file.
    """
    import sys
    print(f"Loading {path}...", file=sys.stderr)
    data = load_census(path)

    total_size = sum(f[1] for f in data['files'])

    print(f"Root: {data['root']}")
    print(f"Scanned: {time.ctime(data['timestamp'])}")
    print(f"Files: {len(data['files']):,}")
    print(f"Total: {total_size / 1e9:.2f} GB")


def merge(folder: str, output: str) -> None:
    """
    Command. Merge checkpoint folder into a single census file.

    Args:
        folder (str): Path to checkpoint folder.
        output (str): Output file path.
    """
    import sys
    print(f"Merging checkpoints from {folder}...", file=sys.stderr)
    census = load_census_folder(folder)

    compressed_size = save_census(output, census['files'], census['root'], census['timestamp'])

    total_size = sum(f[1] for f in census['files'])
    print(f"Merged: {len(census['files']):,} files, {total_size / 1e9:.2f} GB")
    print(f"Saved: {output} ({compressed_size / 1e6:.2f} MB)")


CLI = {
    'scan': scan_local,
    'scan-s3': _scan_s3_lazy,
    'lod': lod,
    'load': load,
    'merge': merge,
    'analyze': analyze,
}
