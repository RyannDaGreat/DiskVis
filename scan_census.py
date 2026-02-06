#!/usr/bin/env python3
"""
Resumable directory scanner with DFS traversal and checkpointing.

REQUIREMENTS FOR CONTRIBUTION:
1. Pure functions - No side effects, marked "Pure." in docstring, with >>> examples
2. Group related functions together in the file with a comment header
3. Stateful code is minimal and isolated - Document what state it touches
4. Config in one place - All constants at top of file, not scattered
5. No silent failures - Errors fail loudly, never swallow exceptions
6. DRY - Extract helpers, reuse code paths with configuration
7. Simplicity - Minimal structure now, expand as needed, no over-engineering

Usage:
    python scan_census.py scan /
    python scan_census.py scan /Users --output=users.census.zst
    python scan_census.py scan /mnt/nfs --checkpoint_interval=50000
    python scan_census.py load users.census.zst
    python scan_census.py merge /tmp/scan_abc123/ --output=merged.census.zst
"""

import hashlib
import json
import os
import sys
import tempfile
import time
from glob import glob
from pathlib import Path

import fire
import zstandard as zstd


# =============================================================================
# CONFIG
# =============================================================================

ZSTD_LEVEL = 3
CENSUS_EXT = '.census.zst'


# =============================================================================
# PURE FUNCTIONS - Census I/O
# =============================================================================

def save_census(path: str, files: list, root: str, timestamp: int) -> int:
    """
    Pure (except I/O). Save census as JSONL + zstd.

    Format: Line 1 is header {"root": ..., "timestamp": ..., "count": ...}
            Remaining lines are file entries [inode, size, path]

    Args:
        path (str): Output file path.
        files (list): List of [inode, size, path] entries.
        root (str): Root directory that was scanned.
        timestamp (int): Unix timestamp of scan.

    Returns:
        int: Compressed size in bytes.

    >>> import tempfile, os
    >>> f = tempfile.NamedTemporaryFile(suffix='.census.zst', delete=False)
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
    Pure (except I/O). Load census from JSONL + zstd.

    Args:
        path (str): Path to .census.zst file.

    Returns:
        dict: Census with keys 'root', 'timestamp', 'files'.

    >>> import tempfile, os
    >>> f = tempfile.NamedTemporaryFile(suffix='.census.zst', delete=False)
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
    Pure (except I/O). Load all checkpoints from folder, merge in order.

    Args:
        folder (str): Path to checkpoint folder.

    Returns:
        dict: Merged census with keys 'root', 'timestamp', 'files'.

    >>> import tempfile, os
    >>> d = tempfile.mkdtemp()
    >>> _ = save_census(f'{d}/checkpoint_000.census.zst', [[1, 100, '/a']], '/', 1000)
    >>> _ = save_census(f'{d}/checkpoint_001.census.zst', [[2, 200, '/b']], '/', 2000)
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


# =============================================================================
# PURE FUNCTIONS - Resume logic
# =============================================================================

def get_checkpoint_dir(root: str, explicit_dir: str | None, one_file_system: bool,
                       follow_symlinks: bool, disk_usage: bool) -> str:
    """
    Pure. Get checkpoint directory path.

    Args:
        root (str): Root directory being scanned.
        explicit_dir (str | None): Explicit checkpoint dir, or None for auto.
        one_file_system (bool): Whether scan stays on one filesystem.
        follow_symlinks (bool): Whether scan follows symlinks.
        disk_usage (bool): Whether storing disk usage vs apparent size.

    Returns:
        str: Path to checkpoint directory.

    >>> get_checkpoint_dir('/Users', None, True, False, True).startswith(tempfile.gettempdir())
    True
    >>> get_checkpoint_dir('/Users', '/tmp/my_checkpoints', True, False, True)
    '/tmp/my_checkpoints'
    """
    if explicit_dir:
        return explicit_dir

    h = hash_args(root, one_file_system, follow_symlinks, disk_usage)
    return os.path.join(tempfile.gettempdir(), f'scan_census_{h}')


def get_resume_point(folder: str) -> str | None:
    """
    Pure (except I/O). Find the last scanned path from latest checkpoint.

    Args:
        folder (str): Checkpoint folder path.

    Returns:
        str | None: Last path from latest checkpoint, or None if no checkpoints.

    >>> import tempfile, os
    >>> d = tempfile.mkdtemp()
    >>> _ = save_census(f'{d}/checkpoint_000.census.zst', [[1, 100, '/a/b']], '/', 1000)
    >>> _ = save_census(f'{d}/checkpoint_001.census.zst', [[2, 200, '/a/z']], '/', 2000)
    >>> get_resume_point(d)
    '/a/z'
    >>> import shutil; shutil.rmtree(d)
    """
    pattern = os.path.join(folder, f'checkpoint_*{CENSUS_EXT}')
    checkpoint_files = sorted(glob(pattern))
    if not checkpoint_files:
        return None

    latest = load_census(checkpoint_files[-1])
    if not latest['files']:
        return None

    return latest['files'][-1][2]  # [2] is path


def should_skip(path: str, resume_point: str | None) -> bool:
    """
    Pure. Check if path should be skipped (already scanned).

    Args:
        path (str): Current path being considered.
        resume_point (str | None): Last scanned path, or None if fresh scan.

    Returns:
        bool: True if path should be skipped.

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
# PURE FUNCTIONS - Path helpers
# =============================================================================

def hash_args(root: str, one_file_system: bool, follow_symlinks: bool, disk_usage: bool) -> str:
    """
    Pure. SHA256 hash of args that affect scan results.

    Args:
        root (str): Root directory.
        one_file_system (bool): Stay on same filesystem.
        follow_symlinks (bool): Follow symlinks.
        disk_usage (bool): Store disk usage vs apparent size.

    Returns:
        str: First 12 chars of SHA256 hash.

    >>> len(hash_args('/', True, False, True))
    12
    >>> hash_args('/', True, False, True) == hash_args('/', True, False, True)
    True
    >>> hash_args('/', True, False, True) != hash_args('/', False, False, True)
    True
    """
    data = f'{root}|{one_file_system}|{follow_symlinks}|{disk_usage}'
    return hashlib.sha256(data.encode()).hexdigest()[:12]


# =============================================================================
# STATEFUL - Scanner
# =============================================================================

def scan(
    root: str = '/',
    output: str = 'census_output.census.zst',
    checkpoint_dir: str = None,
    checkpoint_interval: int = 100_000,
    one_file_system: bool = True,
    follow_symlinks: bool = False,
    show_progress: bool = True,
    disk_usage: bool = True,
    report_errors: bool = False,
) -> str:
    """
    Stateful. Scan a directory tree and save as census.

    Touches: filesystem (read), checkpoint folder (write).

    Args:
        root (str): Directory to scan. Default: '/'
            Example: /Users, /mnt/nfs
        output (str): Output file path. Default: 'census_output.census.zst'
            Example: backup.census.zst
        checkpoint_dir (str): Where to store checkpoints. Default: None (auto temp folder)
            Example: /tmp/my_scan_checkpoints
        checkpoint_interval (int): Dirs between checkpoints. Default: 100000
            Example: 50000
        one_file_system (bool): Stay on same mount, don't cross devices. Default: True
        follow_symlinks (bool): Follow symbolic links. Default: False
        show_progress (bool): Print progress to stderr. Default: True
        disk_usage (bool): Store actual disk usage (st_blocks * 512). Default: True
            False = apparent file size (st_size)
        report_errors (bool): Print permission/access errors to stderr. Default: False

    Returns:
        str: The output file path

    Examples:
        python scan_census.py scan /
        python scan_census.py scan /Users --output=users.census.zst
        python scan_census.py scan /mnt/nfs --checkpoint_interval=50000
        python scan_census.py scan / --one_file_system=False --report_errors
    """
    root = os.path.abspath(os.path.expanduser(root))

    # Get root device for one_file_system check
    root_dev = os.stat(root).st_dev if one_file_system else None

    # Setup checkpoint directory
    ckpt_dir = get_checkpoint_dir(root, checkpoint_dir, one_file_system, follow_symlinks, disk_usage)
    os.makedirs(ckpt_dir, exist_ok=True)

    # Check for resume point
    resume_point = get_resume_point(ckpt_dir)
    if resume_point and show_progress:
        print(f"Resuming from: {resume_point}", file=sys.stderr)

    # Count existing checkpoints for numbering
    existing_checkpoints = sorted(glob(os.path.join(ckpt_dir, f'checkpoint_*{CENSUS_EXT}')))
    checkpoint_num = len(existing_checkpoints)

    # DFS traversal state
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
        except PermissionError as e:
            if report_errors:
                print(f"Permission denied: {current}", file=sys.stderr)
            continue
        except OSError as e:
            if report_errors:
                print(f"Error scanning {current}: {e}", file=sys.stderr)
            continue

        # Sort for deterministic order (alphabetical)
        entries.sort(key=lambda e: e.name)

        # Process entries - add dirs to stack in reverse order (so alphabetically first is popped first)
        subdirs = []
        for entry in entries:
            try:
                # Skip if resuming and already scanned
                if should_skip(entry.path, resume_point):
                    continue

                is_dir = entry.is_dir(follow_symlinks=follow_symlinks)
                is_file = entry.is_file(follow_symlinks=follow_symlinks)

                if is_dir:
                    # Check one_file_system
                    if one_file_system:
                        try:
                            if entry.stat(follow_symlinks=follow_symlinks).st_dev != root_dev:
                                continue
                        except OSError:
                            continue
                    subdirs.append(entry.path)

                elif is_file:
                    try:
                        stat = entry.stat(follow_symlinks=follow_symlinks)
                        inode = stat.st_ino
                        size = stat.st_blocks * 512 if disk_usage else stat.st_size
                        files.append([inode, size, entry.path])
                        total_size += size
                    except OSError as e:
                        if report_errors:
                            print(f"Error stat {entry.path}: {e}", file=sys.stderr)

            except OSError as e:
                if report_errors:
                    print(f"Error processing {entry.path}: {e}", file=sys.stderr)

        # Add subdirs to stack in reverse order for correct alphabetical DFS
        stack.extend(reversed(subdirs))

        dirs_completed += 1

        # Progress
        if show_progress and dirs_completed % 1000 == 0:
            print(f"  {dirs_completed:,} dirs, {len(files):,} files, {total_size / 1e9:.1f} GB...", file=sys.stderr)

        # Checkpoint
        if dirs_completed % checkpoint_interval == 0 and files:
            ckpt_path = os.path.join(ckpt_dir, f'checkpoint_{checkpoint_num:03d}{CENSUS_EXT}')
            save_census(ckpt_path, files, root, int(time.time()))
            if show_progress:
                print(f"  Checkpoint saved: {ckpt_path}", file=sys.stderr)
            checkpoint_num += 1
            files = []  # Reset for next chunk

    # Save final chunk if any remaining files
    if files:
        ckpt_path = os.path.join(ckpt_dir, f'checkpoint_{checkpoint_num:03d}{CENSUS_EXT}')
        save_census(ckpt_path, files, root, int(time.time()))
        if show_progress:
            print(f"  Checkpoint saved: {ckpt_path}", file=sys.stderr)

    # Merge all checkpoints
    census = load_census_folder(ckpt_dir)

    if show_progress:
        print(f"Done: {len(census['files']):,} files, {sum(f[1] for f in census['files']) / 1e9:.2f} GB", file=sys.stderr)

    # Save final output if specified
    if output:
        compressed_size = save_census(output, census['files'], census['root'], census['timestamp'])
        if show_progress:
            print(f"Saved: {output} ({compressed_size / 1e6:.2f} MB)", file=sys.stderr)

        # Clean up checkpoint folder
        import shutil
        shutil.rmtree(ckpt_dir)
        if show_progress:
            print(f"Cleaned up checkpoints: {ckpt_dir}", file=sys.stderr)

    return output


def load(path: str) -> None:
    """
    Load and print stats from a census file.

    Args:
        path (str): Path to .census.zst file.
            Example: users.census.zst

    Examples:
        python scan_census.py load users.census.zst
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
    Merge checkpoint folder into a single census file.

    Args:
        folder (str): Path to checkpoint folder.
            Example: /tmp/scan_census_abc123
        output (str): Output file path.
            Example: merged.census.zst

    Examples:
        python scan_census.py merge /tmp/scan_census_abc123 --output=merged.census.zst
    """
    print(f"Merging checkpoints from {folder}...", file=sys.stderr)
    census = load_census_folder(folder)

    compressed_size = save_census(output, census['files'], census['root'], census['timestamp'])

    total_size = sum(f[1] for f in census['files'])
    print(f"Merged: {len(census['files']):,} files, {total_size / 1e9:.2f} GB")
    print(f"Saved: {output} ({compressed_size / 1e6:.2f} MB)")


# =============================================================================
# CLI
# =============================================================================

if __name__ == '__main__':
    fire.Fire({'scan': scan, 'load': load, 'merge': merge})
