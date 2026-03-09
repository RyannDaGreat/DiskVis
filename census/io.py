#!/usr/bin/env python3
"""
Shared census file I/O: read, write, and merge .jsonl.zst files.

Census format: zstd-compressed JSONL.
    Line 1: header {"root": ..., "timestamp": ..., "count": ...}
    Lines 2+: file entries [inode, size, path]
"""
from __future__ import annotations

import json
import os
from glob import glob

import zstandard as zstd


# =============================================================================
# CONFIG
# =============================================================================

ZSTD_LEVEL = 3
CENSUS_EXT = '.jsonl.zst'


# =============================================================================
# I/O
# =============================================================================

def save_census(path: str, files: list, root: str, timestamp: int) -> int:
    """
    Command (writes file). Save census as JSONL + zstd.

    Args:
        path (str): Output file path.
        files (list): List of [inode, size, path] entries.
        root (str): Root directory that was scanned.
        timestamp (int): Unix timestamp of scan.

    Returns:
        int: Compressed size in bytes.

    Examples:
        >>> import tempfile, os
        >>> f = tempfile.NamedTemporaryFile(suffix='.jsonl.zst', delete=False)
        >>> size = save_census(f.name, [[123, 456, '/a/b']], '/a', 1234567890)
        >>> size > 0
        True
        >>> os.unlink(f.name)
    """
    header = {'root': root, 'timestamp': timestamp, 'count': len(files)}
    lines = [json.dumps(header)]
    lines.extend(json.dumps(f) for f in files)
    jsonl_bytes = '\n'.join(lines).encode()
    compressed = zstd.ZstdCompressor(level=ZSTD_LEVEL).compress(jsonl_bytes)
    with open(path, 'wb') as f:
        f.write(compressed)
    return len(compressed)


def load_census(path: str) -> dict:
    """
    Query (reads file). Load census from JSONL + zstd.

    Args:
        path (str): Path to .jsonl.zst file.

    Returns:
        dict: Census with keys 'root', 'timestamp', 'files'.

    Examples:
        >>> import tempfile, os
        >>> f = tempfile.NamedTemporaryFile(suffix='.jsonl.zst', delete=False)
        >>> _ = save_census(f.name, [[123, 456, '/a/b']], '/a', 1234567890)
        >>> data = load_census(f.name)
        >>> data['root']
        '/a'
        >>> data['files']
        [[123, 456, '/a/b']]
        >>> os.unlink(f.name)
    """
    with open(path, 'rb') as f:
        compressed = f.read()
    jsonl_bytes = zstd.ZstdDecompressor().decompress(compressed)
    lines = jsonl_bytes.decode().split('\n')
    header = json.loads(lines[0])
    files = [json.loads(line) for line in lines[1:] if line]
    return {'root': header['root'], 'timestamp': header['timestamp'], 'files': files}


def load_census_folder(folder: str) -> dict:
    """
    Query (reads files). Load all checkpoints from folder, merge in order.

    Args:
        folder (str): Path to checkpoint folder.

    Returns:
        dict: Merged census with keys 'root', 'timestamp', 'files'.

    Examples:
        >>> import tempfile, os
        >>> d = tempfile.mkdtemp()
        >>> _ = save_census(f'{d}/checkpoint_000.jsonl.zst', [[1, 100, '/a']], '/', 1000)
        >>> _ = save_census(f'{d}/checkpoint_001.jsonl.zst', [[2, 200, '/b']], '/', 2000)
        >>> data = load_census_folder(d)
        >>> len(data['files'])
        2
        >>> data['files'][0]
        [1, 100, '/a']
        >>> import shutil; shutil.rmtree(d)
    """
    pattern = os.path.join(folder, f'checkpoint_*{CENSUS_EXT}')
    checkpoint_files = sorted(glob(pattern))
    if not checkpoint_files:
        raise FileNotFoundError(f"No checkpoints found in {folder}")

    censuses = [load_census(f) for f in checkpoint_files]
    return merge_censuses(censuses)


def merge_censuses(censuses: list) -> dict:
    """
    Pure. Concatenate files arrays from multiple censuses.

    Args:
        censuses (list): List of census dicts.

    Returns:
        dict: Merged census with combined files.

    Examples:
        >>> c1 = {'root': '/', 'timestamp': 1000, 'files': [[1, 100, '/a']]}
        >>> c2 = {'root': '/', 'timestamp': 2000, 'files': [[2, 200, '/b']]}
        >>> merged = merge_censuses([c1, c2])
        >>> merged['files']
        [[1, 100, '/a'], [2, 200, '/b']]
        >>> merged['timestamp']
        2000
    """
    if not censuses:
        raise ValueError("No censuses to merge")

    all_files = []
    for c in censuses:
        all_files.extend(c['files'])

    return {
        'root': censuses[0]['root'],
        'timestamp': censuses[-1]['timestamp'],
        'files': all_files,
    }
