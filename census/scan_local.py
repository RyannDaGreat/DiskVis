#!/usr/bin/env python3
"""
Local filesystem scanner with DFS traversal and checkpointing.

Usage:
    python scan_census.py scan /
    python scan_census.py scan /Users --output=users.jsonl.zst
    python scan_census.py scan /mnt/nfs --checkpoint_interval=50000
"""
from __future__ import annotations

import os
import sys

from census.scan import (
    finalize_scan,
    init_checkpoints,
    maybe_checkpoint,
    print_progress,
    save_checkpoint,
    should_skip,
)


# =============================================================================
# CONFIG
# =============================================================================

POSIX_BLOCK_BYTES = 512  # POSIX standard: st_blocks counts 512-byte units


# =============================================================================
# HELPERS
# =============================================================================

def stat_entry(entry: os.DirEntry, follow_symlinks: bool, disk_usage: bool) -> tuple[int, int]:
    """
    Query (reads file metadata). Get inode and size from a directory entry.

    Args:
        entry (os.DirEntry): Directory entry to stat.
        follow_symlinks (bool): Whether to follow symlinks.
        disk_usage (bool): True for disk usage (st_blocks), False for apparent size (st_size).

    Returns:
        tuple[int, int]: (inode, size_in_bytes)
    """
    stat = entry.stat(follow_symlinks=follow_symlinks)
    inode = stat.st_ino
    size = stat.st_blocks * POSIX_BLOCK_BYTES if disk_usage else stat.st_size
    return inode, size


# =============================================================================
# COMMAND
# =============================================================================

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
) -> str:
    """
    Command. Scan a local directory tree and save as census.

    Touches: filesystem (read), checkpoint folder (write), output file (write).

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

    Returns:
        str: The output file path.
    """
    root = os.path.abspath(os.path.expanduser(root))
    root_dev = os.stat(root).st_dev if one_file_system else None

    ckpt_dir, resume_point, checkpoint_num = init_checkpoints(
        root, checkpoint_dir, show_progress,
        one_file_system=one_file_system, follow_symlinks=follow_symlinks, disk_usage=disk_usage,
    )

    files = []
    dirs_completed = 0
    total_size = 0
    stack = [root]

    if show_progress:
        print(f"Scanning {root}...", file=sys.stderr)

    while stack:
        current = stack.pop()

        try:
            entries = list(os.scandir(current))
        except PermissionError:
            if report_errors:
                print(f"Permission denied: {current}", file=sys.stderr)
            continue
        except OSError as e:
            if report_errors:
                print(f"Error scanning {current}: {e}", file=sys.stderr)
            continue

        entries.sort(key=lambda e: e.name)

        subdirs = []
        for entry in entries:
            try:
                if should_skip(entry.path, resume_point):
                    continue

                is_dir = entry.is_dir(follow_symlinks=follow_symlinks)
                is_file = entry.is_file(follow_symlinks=follow_symlinks)

                if is_dir:
                    if one_file_system:
                        try:
                            if entry.stat(follow_symlinks=follow_symlinks).st_dev != root_dev:
                                continue
                        except OSError as e:
                            print(f"Warning: cannot stat {entry.path} for device check: {e}",
                                  file=sys.stderr)
                            continue
                    subdirs.append(entry.path)

                elif is_file:
                    try:
                        inode, size = stat_entry(entry, follow_symlinks, disk_usage)
                        files.append([inode, size, entry.path])
                        total_size += size
                    except OSError as e:
                        if report_errors:
                            print(f"Error stat {entry.path}: {e}", file=sys.stderr)

            except OSError as e:
                if report_errors:
                    print(f"Error processing {entry.path}: {e}", file=sys.stderr)

        stack.extend(reversed(subdirs))
        dirs_completed += 1

        if show_progress:
            print_progress(dirs_completed, len(files), total_size)

        files, checkpoint_num = maybe_checkpoint(
            ckpt_dir, checkpoint_num, files, root,
            dirs_completed, checkpoint_interval, show_progress,
        )

    # Save final chunk
    if files:
        save_checkpoint(ckpt_dir, checkpoint_num, files, root, show_progress)

    return finalize_scan(ckpt_dir, output, show_progress)
