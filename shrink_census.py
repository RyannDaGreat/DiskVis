#!/usr/bin/env python3
"""
Shrink a census file by grouping small folders into summary entries.

Usage:
    python shrink_census.py root.census.zst --threshold=1000000  # 1MB default
    python shrink_census.py root.census.zst --threshold=10000000 --output=shrunk.census.zst
"""

import os
import sys
from collections import defaultdict

import fire
import zstandard as zstd


def load_census(path: str) -> dict:
    """Load census from zstd-compressed JSON."""
    with open(path, 'rb') as f:
        compressed = f.read()
    json_bytes = zstd.ZstdDecompressor().decompress(compressed)
    import json
    return json.loads(json_bytes)


def save_census(path: str, files: list, root: str, timestamp: int, level: int = 3) -> int:
    """Save census as zstd-compressed JSON."""
    import json
    census = {'root': root, 'timestamp': timestamp, 'files': files}
    json_bytes = json.dumps(census).encode()
    compressed = zstd.ZstdCompressor(level=level).compress(json_bytes)
    with open(path, 'wb') as f:
        f.write(compressed)
    return len(compressed)


def shrink(
    input_path: str,
    output: str = None,
    threshold: int = 1_000_000,
    show_progress: bool = True,
) -> dict:
    """
    Shrink census by grouping small folders.

    Folders whose total contents are below the threshold are replaced with
    a single FOLDER entry: [0, folder_total_size, "/path/to/folder/"].

    Args:
        input_path: Path to input .census.zst file.
        output: Output path. Default: input with .shrunk inserted.
        threshold: Size threshold in bytes. Folders below this get grouped.
        show_progress: Print progress to stderr.

    Returns:
        dict: Stats about the shrinking operation.
    """
    if output is None:
        base, ext = os.path.splitext(input_path)
        if base.endswith('.census'):
            base = base[:-7]
        output = f"{base}.shrunk.census.zst"

    if show_progress:
        print(f"Loading {input_path}...", file=sys.stderr)

    census = load_census(input_path)
    files = census['files']
    original_count = len(files)

    if show_progress:
        print(f"Loaded {original_count:,} files", file=sys.stderr)
        print(f"Building folder tree...", file=sys.stderr)

    # Build folder -> [files] mapping and folder -> total size
    folder_files = defaultdict(list)  # folder_path -> list of (inode, size, path)
    folder_sizes = defaultdict(int)   # folder_path -> total size of all descendants

    for inode, size, path in files:
        parent = os.path.dirname(path)
        folder_files[parent].append((inode, size, path))

        # Propagate size up to all ancestor folders
        current = parent
        while current:
            folder_sizes[current] += size
            parent_of_current = os.path.dirname(current)
            if parent_of_current == current:  # Hit root
                break
            current = parent_of_current

    if show_progress:
        print(f"Found {len(folder_files):,} folders", file=sys.stderr)
        print(f"Finding folders below {threshold:,} bytes threshold...", file=sys.stderr)

    # Find folders to collapse: those below threshold whose parent is above
    # We want the highest-level folders that are below threshold
    folders_to_collapse = set()
    for folder, total_size in folder_sizes.items():
        if total_size < threshold:
            parent = os.path.dirname(folder)
            # Only collapse if parent is above threshold (or is root)
            parent_size = folder_sizes.get(parent, float('inf'))
            if parent_size >= threshold or parent == folder:
                folders_to_collapse.add(folder)

    if show_progress:
        print(f"Collapsing {len(folders_to_collapse):,} folders", file=sys.stderr)

    # Build new file list
    new_files = []
    collapsed_paths = set()  # Track all paths inside collapsed folders

    # First, add FOLDER entries for collapsed folders
    for folder in sorted(folders_to_collapse):
        # Trailing slash indicates it's a folder summary
        new_files.append([0, folder_sizes[folder], folder + '/'])
        # Mark all descendant paths as collapsed
        for f_path in folder_files.keys():
            if f_path == folder or f_path.startswith(folder + '/'):
                for _, _, path in folder_files[f_path]:
                    collapsed_paths.add(path)

    # Then add files that weren't collapsed
    for inode, size, path in files:
        if path not in collapsed_paths:
            new_files.append([inode, size, path])

    # Sort by path for consistency
    new_files.sort(key=lambda x: x[2])

    new_count = len(new_files)

    if show_progress:
        print(f"Reduced from {original_count:,} to {new_count:,} entries "
              f"({100 * (1 - new_count/original_count):.1f}% reduction)", file=sys.stderr)

    # Save
    compressed_size = save_census(output, new_files, census['root'], census['timestamp'])

    if show_progress:
        original_size = os.path.getsize(input_path)
        print(f"Saved: {output}", file=sys.stderr)
        print(f"Size: {compressed_size / 1e6:.2f} MB "
              f"(was {original_size / 1e6:.2f} MB, "
              f"{100 * (1 - compressed_size/original_size):.1f}% smaller)", file=sys.stderr)

    return {
        'original_entries': original_count,
        'new_entries': new_count,
        'original_size': os.path.getsize(input_path),
        'new_size': compressed_size,
        'threshold': threshold,
        'folders_collapsed': len(folders_to_collapse),
    }


def analyze(input_path: str, show_top: int = 20) -> None:
    """
    Analyze a census file to help choose a threshold.

    Shows distribution of folder sizes to help pick a good threshold.
    """
    print(f"Loading {input_path}...", file=sys.stderr)
    census = load_census(input_path)
    files = census['files']

    print(f"Loaded {len(files):,} files", file=sys.stderr)
    print(f"Building folder sizes...", file=sys.stderr)

    # Build folder sizes
    folder_sizes = defaultdict(int)
    folder_file_counts = defaultdict(int)

    for inode, size, path in files:
        current = os.path.dirname(path)
        while current:
            folder_sizes[current] += size
            folder_file_counts[current] += 1
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent

    # Analyze distribution
    sizes = list(folder_sizes.values())
    sizes.sort()

    thresholds = [100_000, 500_000, 1_000_000, 5_000_000, 10_000_000, 50_000_000, 100_000_000]

    print(f"\n{'Threshold':>15} | {'Folders Below':>15} | {'Est. Entries':>15} | {'Reduction':>10}")
    print("-" * 65)

    for thresh in thresholds:
        below = sum(1 for s in sizes if s < thresh)
        # Rough estimate: each collapsed folder becomes 1 entry instead of many
        est_entries = len(files) - below * 5  # Assume ~5 files per small folder on avg
        est_entries = max(est_entries, below)  # At minimum, the folder entries
        reduction = 100 * (1 - est_entries / len(files))
        print(f"{thresh:>15,} | {below:>15,} | {est_entries:>15,} | {reduction:>9.1f}%")

    # Show largest folders
    print(f"\nTop {show_top} largest folders:")
    sorted_folders = sorted(folder_sizes.items(), key=lambda x: -x[1])
    for folder, size in sorted_folders[:show_top]:
        count = folder_file_counts[folder]
        print(f"  {size / 1e9:>8.2f} GB  {count:>8,} files  {folder}")


if __name__ == '__main__':
    fire.Fire({'shrink': shrink, 'analyze': analyze})
