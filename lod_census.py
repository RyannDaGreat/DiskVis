#!/usr/bin/env python3
"""
LOD (Level of Detail) reducer for census files.

Reduces millions of files to a target count while preserving visual accuracy.
Small files get aggregated into their parent folders.

Usage:
    python lod_census.py root.census.zst --target=500000
    python lod_census.py root.census.zst --target=500000 --output=root.lod.census.zst
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

import fire
import zstandard as zstd

ZSTD_LEVEL = 3


def load_census(path: str) -> tuple[list, str, int]:
    """Load census from JSONL + zstd format."""
    with open(path, 'rb') as f:
        data = zstd.ZstdDecompressor().decompress(f.read())
    lines = data.decode().split('\n')
    header = json.loads(lines[0])
    files = [json.loads(line) for line in lines[1:] if line]
    return files, header['root'], header['timestamp']


def save_census(path: str, files: list, root: str, timestamp: int) -> int:
    """Save census as JSONL + zstd."""
    header = {'root': root, 'timestamp': timestamp, 'count': len(files)}
    lines = [json.dumps(header)]
    lines.extend(json.dumps(f) for f in files)
    jsonl_bytes = '\n'.join(lines).encode()
    compressed = zstd.ZstdCompressor(level=ZSTD_LEVEL).compress(jsonl_bytes)
    with open(path, 'wb') as f:
        f.write(compressed)
    return len(compressed)


def lod(
    input_path: str,
    target: int = 500_000,
    output: str = None,
    show_progress: bool = True,
) -> dict:
    """
    Reduce census to target entry count using LOD aggregation.

    Algorithm:
    1. Keep all files above a computed size threshold
    2. For remaining small files, aggregate into parent folder entries
    3. Threshold is computed to hit target count

    Args:
        input_path: Path to input .census.zst file
        target: Target number of entries (default 500k)
        output: Output path (default: input with .lod inserted)
        show_progress: Print progress to stderr

    Returns:
        dict: Stats about the operation
    """
    if output is None:
        base = input_path.replace('.census.zst', '')
        output = f"{base}.lod.census.zst"

    if show_progress:
        print(f"Loading {input_path}...", file=sys.stderr)

    files, root_path, timestamp = load_census(input_path)
    original_count = len(files)

    if show_progress:
        print(f"Loaded {original_count:,} files", file=sys.stderr)

    # Sort files by size to find threshold
    if show_progress:
        print("Sorting by size to find threshold...", file=sys.stderr)

    sorted_files = sorted(files, key=lambda x: -x[1])

    # Find threshold: we want ~target large files, rest become folder aggregates
    # Reserve some slots for folder aggregates (estimate ~20% of target)
    file_slots = int(target * 0.8)
    if file_slots >= len(sorted_files):
        threshold = 0
    else:
        threshold = sorted_files[file_slots][1]

    if show_progress:
        print(f"Size threshold: {threshold:,} bytes ({threshold/1024:.1f} KB)", file=sys.stderr)

    # Separate large files (keep) vs small files (aggregate)
    large_files = []
    small_by_folder = defaultdict(lambda: {'size': 0, 'count': 0})

    for inode, size, path in files:
        if size >= threshold:
            large_files.append([inode, size, path])
        else:
            folder = os.path.dirname(path)
            small_by_folder[folder]['size'] += size
            small_by_folder[folder]['count'] += 1

    if show_progress:
        print(f"Large files (kept): {len(large_files):,}", file=sys.stderr)
        print(f"Small files aggregated into {len(small_by_folder):,} folders", file=sys.stderr)

    # Now we need to reduce folder count if large_files + folders > target
    # Recursively merge small folders into their parents
    if show_progress:
        print("Merging small folder aggregates...", file=sys.stderr)

    # Build folder hierarchy with aggregated sizes
    folder_data = dict(small_by_folder)  # copy

    # Calculate how many folder entries we can have
    folder_budget = target - len(large_files)

    if len(folder_data) > folder_budget and folder_budget > 0:
        # Need to merge folders - sort by size and keep largest
        folder_items = [(info['size'], path, info['count']) for path, info in folder_data.items()]
        folder_items.sort(key=lambda x: -x[0])

        # Keep top folders by size, merge rest into parents
        kept_folders = {}
        merged_up = defaultdict(lambda: {'size': 0, 'count': 0})

        for i, (size, path, count) in enumerate(folder_items):
            if i < folder_budget:
                kept_folders[path] = {'size': size, 'count': count}
            else:
                # Merge into parent
                parent = os.path.dirname(path)
                if not parent or parent == path:
                    parent = '/'
                merged_up[parent]['size'] += size
                merged_up[parent]['count'] += count

        # Add merged amounts to kept folders or create new entries
        for parent, info in merged_up.items():
            if parent in kept_folders:
                kept_folders[parent]['size'] += info['size']
                kept_folders[parent]['count'] += info['count']
            elif len(kept_folders) < folder_budget:
                kept_folders[parent] = info
            else:
                # Merge further up
                grandparent = os.path.dirname(parent)
                if grandparent in kept_folders:
                    kept_folders[grandparent]['size'] += info['size']
                    kept_folders[grandparent]['count'] += info['count']
                elif '/' in kept_folders:
                    kept_folders['/']['size'] += info['size']
                    kept_folders['/']['count'] += info['count']
                else:
                    kept_folders['/'] = info

        folder_data = kept_folders

    if show_progress:
        print(f"Final folder aggregates: {len(folder_data):,}", file=sys.stderr)

    # Build output
    output_files = large_files.copy()

    # Add folder aggregates (trailing / indicates folder)
    for folder_path, info in folder_data.items():
        if info['size'] > 0:  # Only add non-empty
            # Normalize path: /foo -> /foo/, but / stays /
            if folder_path == '/':
                display_path = '/'
            else:
                display_path = folder_path.rstrip('/') + '/'
            output_files.append([0, info['size'], display_path])

    # Sort by path
    output_files.sort(key=lambda x: x[2])

    # Stats
    total_original = sum(f[1] for f in files)
    total_output = sum(f[1] for f in output_files)
    folders_in_output = sum(1 for f in output_files if f[2].endswith('/'))
    files_in_output = len(output_files) - folders_in_output

    if show_progress:
        print(f"\nResults:", file=sys.stderr)
        print(f"  Original: {original_count:,} files", file=sys.stderr)
        print(f"  Output: {len(output_files):,} entries ({files_in_output:,} files, {folders_in_output:,} folders)", file=sys.stderr)
        print(f"  Size coverage: {total_output/total_original*100:.1f}%", file=sys.stderr)

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
    """Analyze census to help choose target count."""
    print(f"Loading {input_path}...", file=sys.stderr)
    files, root, timestamp = load_census(input_path)

    print(f"\nTotal files: {len(files):,}")
    total_size = sum(f[1] for f in files)
    print(f"Total size: {total_size / 1e12:.2f} TB")

    # Size distribution
    sorted_files = sorted(files, key=lambda x: -x[1])

    print(f"\n{'Target':>10} | {'Min Size':>12} | {'Coverage':>10}")
    print("-" * 40)

    for target in [50000, 100000, 250000, 500000, 750000, 1000000]:
        if target > len(files):
            continue
        top_n = sorted_files[:target]
        coverage = sum(f[1] for f in top_n) / total_size * 100
        min_size = top_n[-1][1]

        if min_size >= 1e9:
            size_str = f"{min_size/1e9:.1f} GB"
        elif min_size >= 1e6:
            size_str = f"{min_size/1e6:.1f} MB"
        elif min_size >= 1e3:
            size_str = f"{min_size/1e3:.0f} KB"
        else:
            size_str = f"{min_size} B"

        print(f"{target:>10,} | {size_str:>12} | {coverage:>9.1f}%")


if __name__ == '__main__':
    fire.Fire({'lod': lod, 'analyze': analyze})
