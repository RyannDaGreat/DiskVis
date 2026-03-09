#!/usr/bin/env python3
"""
LOD (Level of Detail) reducer for census files.

Reduces millions of files to a target count while preserving visual accuracy.
Small files get aggregated into their parent folders.

Usage:
    python scan_census.py lod root.jsonl.zst --target=500000
    python scan_census.py lod root.jsonl.zst --target=500000 --output=root.lod.jsonl.zst
    python scan_census.py analyze root.jsonl.zst
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

from census.io import load_census, save_census


# =============================================================================
# CONFIG
# =============================================================================

# Fraction of target count reserved for individually-kept large files.
# Remaining slots go to folder aggregates of small files.
LARGE_FILE_RATIO = 0.8


# =============================================================================
# PURE FUNCTIONS
# =============================================================================

def find_size_threshold(sorted_sizes: list[int], file_slots: int) -> int:
    """
    Pure. Find the minimum file size to keep individually.

    Files at or above this size are kept; files below are aggregated
    into parent-folder entries.

    Args:
        sorted_sizes (list[int]): File sizes sorted descending.
        file_slots (int): How many individual files we can keep.

    Returns:
        int: Size threshold in bytes. 0 if all files fit.

    Examples:
        >>> find_size_threshold([1000, 500, 200, 100, 50], 3)
        100
        >>> find_size_threshold([1000, 500], 5)
        0
        >>> find_size_threshold([1000, 500, 200], 2)
        200
    """
    if file_slots >= len(sorted_sizes):
        return 0
    return sorted_sizes[file_slots]


def partition_by_threshold(files: list, threshold: int) -> tuple[list, dict]:
    """
    Pure. Split files into large (keep) and small (aggregate by folder).

    Args:
        files (list): Census entries [inode, size, path].
        threshold (int): Minimum size to keep individually.

    Returns:
        tuple: (large_files, small_by_folder) where small_by_folder maps
            folder_path -> {'size': int, 'count': int}.

    Examples:
        >>> large, small = partition_by_threshold(
        ...     [[1, 1000, '/a/big'], [2, 50, '/a/tiny'], [3, 500, '/b/med']],
        ...     threshold=100,
        ... )
        >>> large
        [[1, 1000, '/a/big'], [3, 500, '/b/med']]
        >>> small['/a']
        {'size': 50, 'count': 1}
    """
    large_files = []
    small_by_folder = defaultdict(lambda: {'size': 0, 'count': 0})

    for inode, size, path in files:
        if size >= threshold:
            large_files.append([inode, size, path])
        else:
            folder = os.path.dirname(path)
            small_by_folder[folder]['size'] += size
            small_by_folder[folder]['count'] += 1

    return large_files, dict(small_by_folder)


def merge_small_folders(folder_data: dict, folder_budget: int) -> dict:
    """
    Pure. Reduce folder aggregates to fit within budget.

    Keeps the largest folders by total size, merges the rest into
    their parent directories.

    Args:
        folder_data (dict): folder_path -> {'size': int, 'count': int}.
        folder_budget (int): Maximum number of folder entries.

    Returns:
        dict: Reduced folder_path -> {'size': int, 'count': int}.

    Examples:
        >>> data = {'/a': {'size': 1000, 'count': 5}, '/b': {'size': 500, 'count': 3},
        ...         '/c': {'size': 100, 'count': 1}}
        >>> result = merge_small_folders(data, folder_budget=2)
        >>> result['/a']['size']
        1000
        >>> result['/b']['size']
        500
    """
    if len(folder_data) <= folder_budget or folder_budget <= 0:
        return folder_data

    folder_items = [(info['size'], path, info['count']) for path, info in folder_data.items()]
    folder_items.sort(key=lambda x: -x[0])

    kept_folders = {}
    merged_up = defaultdict(lambda: {'size': 0, 'count': 0})

    for i, (size, path, count) in enumerate(folder_items):
        if i < folder_budget:
            kept_folders[path] = {'size': size, 'count': count}
        else:
            parent = os.path.dirname(path)
            if not parent or parent == path:
                parent = '/'
            merged_up[parent]['size'] += size
            merged_up[parent]['count'] += count

    for parent, info in merged_up.items():
        if parent in kept_folders:
            kept_folders[parent]['size'] += info['size']
            kept_folders[parent]['count'] += info['count']
        elif len(kept_folders) < folder_budget:
            kept_folders[parent] = info
        else:
            grandparent = os.path.dirname(parent)
            if grandparent in kept_folders:
                kept_folders[grandparent]['size'] += info['size']
                kept_folders[grandparent]['count'] += info['count']
            elif '/' in kept_folders:
                kept_folders['/']['size'] += info['size']
                kept_folders['/']['count'] += info['count']
            else:
                kept_folders['/'] = info

    return kept_folders


def build_output(large_files: list, folder_data: dict) -> list:
    """
    Pure. Combine kept files and folder aggregates into final census entries.

    Folder aggregates use inode=0 and a trailing '/' on the path.

    Args:
        large_files (list): [inode, size, path] entries kept individually.
        folder_data (dict): folder_path -> {'size': int, 'count': int}.

    Returns:
        list: Sorted [inode, size, path] entries.

    Examples:
        >>> build_output([[1, 1000, '/b/big']], {'/a': {'size': 50, 'count': 3}})
        [[0, 50, '/a/'], [1, 1000, '/b/big']]
    """
    output = large_files.copy()

    for folder_path, info in folder_data.items():
        if info['size'] > 0:
            if folder_path == '/':
                display_path = '/'
            else:
                display_path = folder_path.rstrip('/') + '/'
            output.append([0, info['size'], display_path])

    output.sort(key=lambda x: x[2])
    return output


def format_size(size_bytes: int) -> str:
    """
    Pure. Human-readable size string.

    Args:
        size_bytes (int): Size in bytes.

    Returns:
        str: Formatted string like "1.5 GB", "200 MB", "4 KB", or "512 B".

    Examples:
        >>> format_size(1_500_000_000)
        '1.5 GB'
        >>> format_size(200_000_000)
        '200.0 MB'
        >>> format_size(4000)
        '4 KB'
        >>> format_size(512)
        '512 B'
    """
    if size_bytes >= 1e9:
        return f"{size_bytes / 1e9:.1f} GB"
    elif size_bytes >= 1e6:
        return f"{size_bytes / 1e6:.1f} MB"
    elif size_bytes >= 1e3:
        return f"{size_bytes / 1e3:.0f} KB"
    return f"{size_bytes} B"


# =============================================================================
# COMMANDS
# =============================================================================

def lod(
    input_path: str,
    target: int = 500_000,
    output: str = None,
    show_progress: bool = True,
) -> dict:
    """
    Command. Reduce census to target entry count using LOD aggregation.

    Reads input census, writes reduced output census.

    Algorithm:
    1. Keep all files above a computed size threshold
    2. For remaining small files, aggregate into parent folder entries
    3. Threshold is computed to hit target count

    Args:
        input_path (str): Path to input .jsonl.zst file.
        target (int): Target number of entries. Default: 500000
        output (str): Output path. Default: input with .lod inserted.
        show_progress (bool): Print progress to stderr. Default: True

    Returns:
        dict: Stats about the operation.
    """
    if output is None:
        base = input_path.replace('.jsonl.zst', '')
        output = f"{base}.lod.jsonl.zst"

    if show_progress:
        print(f"Loading {input_path}...", file=sys.stderr)

    data = load_census(input_path)
    files = data['files']
    root_path = data['root']
    timestamp = data['timestamp']
    original_count = len(files)

    if show_progress:
        print(f"Loaded {original_count:,} files", file=sys.stderr)

    # Find size threshold
    if show_progress:
        print("Sorting by size to find threshold...", file=sys.stderr)

    sorted_sizes = sorted((f[1] for f in files), reverse=True)
    file_slots = int(target * LARGE_FILE_RATIO)
    threshold = find_size_threshold(sorted_sizes, file_slots)

    if show_progress:
        print(f"Size threshold: {threshold:,} bytes ({threshold / 1024:.1f} KB)", file=sys.stderr)

    # Partition
    large_files, small_by_folder = partition_by_threshold(files, threshold)

    if show_progress:
        print(f"Large files (kept): {len(large_files):,}", file=sys.stderr)
        print(f"Small files aggregated into {len(small_by_folder):,} folders", file=sys.stderr)

    # Merge folders to fit budget
    if show_progress:
        print("Merging small folder aggregates...", file=sys.stderr)

    folder_budget = target - len(large_files)
    folder_data = merge_small_folders(small_by_folder, folder_budget)

    if show_progress:
        print(f"Final folder aggregates: {len(folder_data):,}", file=sys.stderr)

    # Build output
    output_files = build_output(large_files, folder_data)

    # Stats
    total_original = sum(f[1] for f in files)
    total_output = sum(f[1] for f in output_files)
    folders_in_output = sum(1 for f in output_files if f[2].endswith('/'))
    files_in_output = len(output_files) - folders_in_output

    if show_progress:
        print(f"\nResults:", file=sys.stderr)
        print(f"  Original: {original_count:,} files", file=sys.stderr)
        print(f"  Output: {len(output_files):,} entries ({files_in_output:,} files, {folders_in_output:,} folders)", file=sys.stderr)
        print(f"  Size coverage: {total_output / total_original * 100:.1f}%", file=sys.stderr)

    # Save
    compressed_size = save_census(output, output_files, root_path, timestamp)

    if show_progress:
        original_size = os.path.getsize(input_path)
        print(f"\nSaved: {output}", file=sys.stderr)
        print(f"  Compressed: {compressed_size / 1e6:.2f} MB (was {original_size / 1e6:.2f} MB)", file=sys.stderr)

    return {
        'original_entries': original_count,
        'output_entries': len(output_files),
        'files_kept': files_in_output,
        'folders_created': folders_in_output,
        'size_coverage': total_output / total_original,
        'compressed_size': compressed_size,
    }


def analyze(input_path: str) -> None:
    """
    Command. Analyze census size distribution to help choose target count.

    Args:
        input_path (str): Path to .jsonl.zst file.
    """
    print(f"Loading {input_path}...", file=sys.stderr)
    data = load_census(input_path)
    files = data['files']

    print(f"\nTotal files: {len(files):,}")
    total_size = sum(f[1] for f in files)
    print(f"Total size: {total_size / 1e12:.2f} TB")

    sorted_files = sorted(files, key=lambda x: -x[1])

    print(f"\n{'Target':>10} | {'Min Size':>12} | {'Coverage':>10}")
    print("-" * 40)

    for target in [50_000, 100_000, 250_000, 500_000, 750_000, 1_000_000]:
        if target > len(files):
            continue
        top_n = sorted_files[:target]
        coverage = sum(f[1] for f in top_n) / total_size * 100
        min_size = top_n[-1][1]
        print(f"{target:>10,} | {format_size(min_size):>12} | {coverage:>9.1f}%")
