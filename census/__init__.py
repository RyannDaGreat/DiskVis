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

import sys
import time

from census.io import load_census, load_census_folder, save_census
from census.lod import analyze, lod
from census.scan_local import scan as _scan_local

LOD_TARGET_DEFAULT = 500_000


def _run_lod(output: str, lod_target: int, show_progress: bool) -> None:
    """Command. Run LOD on a census file, saving alongside it."""
    base = output.replace('.jsonl.zst', '')
    lod_output = f"{base}.lod.jsonl.zst"
    if show_progress:
        print(f"\nGenerating LOD ({lod_target:,} target)...", file=sys.stderr)
    lod(output, target=lod_target, output=lod_output, show_progress=show_progress)


def scan(
    root: str = '/',
    output: str = 'census_output.jsonl.zst',
    checkpoint_dir: str = None,
    checkpoint_interval: int = 100_000,
    one_file_system: bool = True,
    follow_symlinks: bool = False,
    show_progress: bool = True,
    disk_usage: bool = True,
    report_errors: bool = False,
    lod_target: int = LOD_TARGET_DEFAULT,
    no_lod: bool = False,
) -> str:
    """
    Command. Scan a local directory tree and save as census.

    By default, also generates a LOD (Level of Detail) file alongside
    the census for faster browser visualization. Use --no_lod to skip.

    Args:
        root (str): Directory to scan. Default: '/'
        output (str): Output file path. Default: 'census_output.jsonl.zst'
        checkpoint_dir (str): Where to store checkpoints. Default: None (auto)
        checkpoint_interval (int): Dirs between checkpoints. Default: 100000
        one_file_system (bool): Stay on same mount. Default: True
        follow_symlinks (bool): Follow symbolic links. Default: False
        show_progress (bool): Print progress to stderr. Default: True
        disk_usage (bool): Disk usage (st_blocks * 512) vs apparent size. Default: True
        report_errors (bool): Print permission/access errors to stderr. Default: False
        lod_target (int): LOD target entry count. Default: 500000
        no_lod (bool): Skip LOD generation. Default: False

    Returns:
        str: The output file path.
    """
    result = _scan_local(
        root=root, output=output, checkpoint_dir=checkpoint_dir,
        checkpoint_interval=checkpoint_interval, one_file_system=one_file_system,
        follow_symlinks=follow_symlinks, show_progress=show_progress,
        disk_usage=disk_usage, report_errors=report_errors,
    )
    if not no_lod:
        _run_lod(result, lod_target, show_progress)
    return result


def scan_s3(
    root: str,
    output: str = 'census_output.jsonl.zst',
    checkpoint_dir: str = None,
    checkpoint_interval: int = 100_000,
    workers: int = 16,
    show_progress: bool = True,
    lod_target: int = LOD_TARGET_DEFAULT,
    no_lod: bool = False,
) -> str:
    """
    Command. Scan an S3 path and save as census.

    By default, also generates a LOD file. Use --no_lod to skip.

    Args:
        root (str): S3 URI like 's3://bucket/prefix'.
        output (str): Output file path. Default: 'census_output.jsonl.zst'
        checkpoint_dir (str): Where to store checkpoints. Default: None (auto)
        checkpoint_interval (int): Entries between checkpoints. Default: 100000
        workers (int): Parallel listing threads. Default: 16
        show_progress (bool): Print progress to stderr. Default: True
        lod_target (int): LOD target entry count. Default: 500000
        no_lod (bool): Skip LOD generation. Default: False

    Returns:
        str: The output file path.
    """
    from census.scan_s3 import scan as _scan_s3_impl
    result = _scan_s3_impl(
        root=root, output=output, checkpoint_dir=checkpoint_dir,
        checkpoint_interval=checkpoint_interval, workers=workers,
        show_progress=show_progress,
    )
    if not no_lod:
        _run_lod(result, lod_target, show_progress)
    return result


def load(path: str) -> None:
    """
    Command. Load and print stats from a census file.

    Args:
        path (str): Path to .jsonl.zst file.
    """
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
    print(f"Merging checkpoints from {folder}...", file=sys.stderr)
    census = load_census_folder(folder)

    compressed_size = save_census(output, census['files'], census['root'], census['timestamp'])

    total_size = sum(f[1] for f in census['files'])
    print(f"Merged: {len(census['files']):,} files, {total_size / 1e9:.2f} GB")
    print(f"Saved: {output} ({compressed_size / 1e6:.2f} MB)")


CLI = {
    'scan': scan,
    'scan-s3': scan_s3,
    'lod': lod,
    'load': load,
    'merge': merge,
    'analyze': analyze,
}
