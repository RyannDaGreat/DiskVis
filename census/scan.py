#!/usr/bin/env python3
"""
Shared scan infrastructure: checkpointing, progress, finalization.

Used by both scan_local.py and scan_s3.py to avoid duplicating
the checkpoint/resume/progress/merge logic.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import time
from glob import glob
from typing import Optional

from census.io import CENSUS_EXT, load_census, load_census_folder, save_census


# =============================================================================
# PURE FUNCTIONS
# =============================================================================

def hash_scan_params(root: str, **params) -> str:
    """
    Pure. SHA256 hash of scan parameters that affect results.

    Different param combos get different checkpoint dirs so they
    don't collide.

    Args:
        root (str): Root path being scanned.
        **params: Arbitrary scan parameters (sorted by key for stability).

    Returns:
        str: First 12 chars of SHA256 hash.

    Examples:
        >>> len(hash_scan_params('/', one_file_system=True))
        12
        >>> hash_scan_params('/a', x=1) == hash_scan_params('/a', x=1)
        True
        >>> hash_scan_params('/a', x=1) != hash_scan_params('/a', x=2)
        True
    """
    items = sorted(params.items())
    data = f'{root}|' + '|'.join(f'{k}={v}' for k, v in items)
    return hashlib.sha256(data.encode()).hexdigest()[:12]


def should_skip(path: str, resume_point: Optional[str]) -> bool:
    """
    Pure. Check if path should be skipped (already scanned).

    Args:
        path (str): Current path being considered.
        resume_point (str | None): Last scanned path, or None if fresh scan.

    Returns:
        bool: True if path should be skipped.

    Examples:
        >>> should_skip('/a/b', '/a/c')
        True
        >>> should_skip('/a/d', '/a/c')
        False
        >>> should_skip('/a/c', '/a/c')
        True
        >>> should_skip('/b/a', '/a/z')
        False
        >>> should_skip('/a/b', None)
        False
    """
    if resume_point is None:
        return False
    return path <= resume_point


# =============================================================================
# CHECKPOINT MANAGEMENT
# =============================================================================

def init_checkpoints(root: str, checkpoint_dir: Optional[str],
                     show_progress: bool = True, **scan_params) -> tuple[str, Optional[str], int]:
    """
    Command (creates dirs, reads files). Set up checkpoint directory and find resume point.

    Args:
        root (str): Root path being scanned.
        checkpoint_dir (str | None): Explicit dir, or None for auto temp folder.
        show_progress (bool): Print resume info to stderr.
        **scan_params: Extra params passed to hash_scan_params for unique dir naming.

    Returns:
        tuple: (ckpt_dir, resume_point, checkpoint_num)
    """
    if checkpoint_dir:
        ckpt_dir = checkpoint_dir
    else:
        h = hash_scan_params(root, **scan_params)
        ckpt_dir = os.path.join(tempfile.gettempdir(), f'scan_census_{h}')

    os.makedirs(ckpt_dir, exist_ok=True)

    # Find resume point
    resume_point = _get_resume_point(ckpt_dir)
    if resume_point and show_progress:
        print(f"Resuming from: {resume_point}", file=sys.stderr)

    # Count existing checkpoints for numbering
    existing = sorted(glob(os.path.join(ckpt_dir, f'checkpoint_*{CENSUS_EXT}')))
    checkpoint_num = len(existing)

    return ckpt_dir, resume_point, checkpoint_num


def save_checkpoint(ckpt_dir: str, checkpoint_num: int, files: list,
                    root: str, show_progress: bool = True) -> None:
    """
    Command (writes file). Save a numbered checkpoint.

    Args:
        ckpt_dir (str): Checkpoint directory.
        checkpoint_num (int): Sequence number for this checkpoint.
        files (list): Entries to save.
        root (str): Root path of the scan.
        show_progress (bool): Print checkpoint path to stderr.
    """
    ckpt_path = os.path.join(ckpt_dir, f'checkpoint_{checkpoint_num:03d}{CENSUS_EXT}')
    save_census(ckpt_path, files, root, int(time.time()))
    if show_progress:
        print(f"  Checkpoint saved: {ckpt_path}", file=sys.stderr)


def maybe_checkpoint(ckpt_dir: str, checkpoint_num: int, files: list,
                     root: str, dirs_completed: int, interval: int,
                     show_progress: bool = True) -> tuple[list, int]:
    """
    Command. Save checkpoint if interval hit, then reset buffer.

    Args:
        ckpt_dir (str): Checkpoint directory.
        checkpoint_num (int): Current checkpoint sequence number.
        files (list): Accumulated entries since last checkpoint.
        root (str): Root path of the scan.
        dirs_completed (int): Directories processed so far.
        interval (int): Checkpoint every N directories.
        show_progress (bool): Print checkpoint info to stderr.

    Returns:
        tuple: (files, checkpoint_num) — files is reset to [] if checkpointed.
    """
    if dirs_completed % interval == 0 and files:
        save_checkpoint(ckpt_dir, checkpoint_num, files, root, show_progress)
        return [], checkpoint_num + 1
    return files, checkpoint_num


def finalize_scan(ckpt_dir: str, output: str, show_progress: bool = True) -> str:
    """
    Command. Merge all checkpoints into final output and clean up.

    Args:
        ckpt_dir (str): Checkpoint directory.
        output (str): Final output file path.
        show_progress (bool): Print stats to stderr.

    Returns:
        str: The output file path.
    """
    census = load_census_folder(ckpt_dir)

    if show_progress:
        total_size = sum(f[1] for f in census['files'])
        print(f"Done: {len(census['files']):,} files, {total_size / 1e9:.2f} GB", file=sys.stderr)

    compressed_size = save_census(output, census['files'], census['root'], census['timestamp'])
    if show_progress:
        print(f"Saved: {output} ({compressed_size / 1e6:.2f} MB)", file=sys.stderr)

    shutil.rmtree(ckpt_dir)
    if show_progress:
        print(f"Cleaned up checkpoints: {ckpt_dir}", file=sys.stderr)

    return output


# =============================================================================
# PROGRESS
# =============================================================================

PROGRESS_INTERVAL = 1000  # Print progress every N directories


def print_progress(dirs_completed: int, file_count: int, total_size: int) -> None:
    """
    Command. Print scan progress to stderr if interval hit.

    Args:
        dirs_completed (int): Directories completed so far.
        file_count (int): Files found so far.
        total_size (int): Total bytes found so far.
    """
    if dirs_completed % PROGRESS_INTERVAL == 0:
        print(f"  {dirs_completed:,} dirs, {file_count:,} files, {total_size / 1e9:.1f} GB...",
              file=sys.stderr, flush=True)


# =============================================================================
# INTERNAL
# =============================================================================

def _get_resume_point(folder: str) -> Optional[str]:
    """
    Query. Find the last scanned path from latest checkpoint.

    Args:
        folder (str): Checkpoint folder path.

    Returns:
        str | None: Last path from latest checkpoint, or None.
    """
    pattern = os.path.join(folder, f'checkpoint_*{CENSUS_EXT}')
    checkpoint_files = sorted(glob(pattern))
    if not checkpoint_files:
        return None

    latest = load_census(checkpoint_files[-1])
    if not latest['files']:
        return None

    return latest['files'][-1][2]  # [2] is path
