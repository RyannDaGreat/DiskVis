#!/usr/bin/env python3
"""
Disk scanner using bfs, saves as JSON + zstd.

Usage:
    python scan_folder.py scan /path                      # scan and print stats
    python scan_folder.py scan /path --output=scan.json.zst
    python scan_folder.py scan /path --follow_symlinks
    python scan_folder.py load scan.json.zst              # load and print stats

Requires: bfs (brew install bfs / apt install bfs)

Scanning Tools Comparison:
┌──────────┬───────┬──────┬─────┬───────────┬─────────┬───────────┬────────┬──────┬────────┐
│ Tool     │ Stars │ brew │ apt │ Traversal │ Streams │ Save/Load │ Inodes │ NFS  │ Memory │
├──────────┼───────┼──────┼─────┼───────────┼─────────┼───────────┼────────┼──────┼────────┤
│ bfs      │ ★★★★★ │  ✓   │  ✓  │ BFS       │ Yes     │ No        │ Yes    │ Good │ Low    │
│ ncdu     │ ★★★★★ │  ✓   │  ✓  │ DFS       │ Yes     │ Yes(zstd) │ Yes    │ OK   │ Med    │
│ gdu      │ ★★★★  │  ✓   │  ✓  │ DFS       │ No      │ Yes(JSON) │ Yes    │ Bad  │ Med-Hi │
│ find     │ ★★★★  │  ✓   │  ✓  │ DFS       │ Yes     │ No        │ Yes    │ Bad  │ Low    │
│ QDirStat │ ★★★★  │  ✓   │  ✓  │ ?         │ No      │ Yes(gzip) │ No     │ Good │ Med    │
│ duc      │ ★★★   │  ✓   │  ✓  │ DFS       │ No      │ Yes(DB)   │ No     │ OK   │ Low    │
│ dust     │ ★★★   │  ✓   │ crg │ DFS       │ No      │ Yes(JSON) │ No     │ Bad  │ Med    │
│ fd       │ ★★    │  ✓   │  ✓  │ DFS       │ Yes     │ No        │ No     │ Bad  │ High   │
│ dua      │ ★★    │  ✓   │ crg │ DFS       │ No      │ No        │ No     │ Bad  │ Low    │
│ baobab   │ ★★    │  ✓   │  ✓  │ DFS       │ No      │ No        │ No     │ Bad  │ High   │
└──────────┴───────┴──────┴─────┴───────────┴─────────┴───────────┴────────┴──────┴────────┘

Storage Formats Comparison:
┌──────────────────┬───────┬───────┬─────────────┬─────────────┬───────────┬────────────┐
│ Format           │ Stars │ Human │ Compression │ Parse Speed │ Streaming │ Rand Acces │
├──────────────────┼───────┼───────┼─────────────┼─────────────┼───────────┼────────────┤
│ CBOR + zstd      │ ★★★★★ │ No    │ 95%         │ 5x JSON     │ Yes       │ No         │
│ SQLite           │ ★★★★★ │ No    │ 80%         │ Very Fast   │ Limited   │ Yes        │
│ TSV + zstd       │ ★★★★  │ Yes   │ 85%         │ Fast        │ Yes       │ No         │
│ JSON Lines +zstd │ ★★★★  │ Yes   │ 85%         │ Medium      │ Yes       │ No         │
│ MessagePack+zstd │ ★★★★  │ No    │ 90%         │ 10x JSON    │ Yes       │ No         │
│ ncdu binary      │ ★★★★  │ No    │ 90%         │ Fast        │ Yes       │ Partial    │
│ JSON + gzip      │ ★★★   │ Yes   │ 75%         │ Slow        │ Limited   │ No         │
│ Protobuf         │ ★★★   │ No    │ 85%         │ 4x JSON     │ Limited   │ No         │
│ Parquet          │ ★★    │ No    │ 70%         │ Fast        │ Limited   │ Yes        │
└──────────────────┴───────┴───────┴─────────────┴─────────────┴───────────┴────────────┘

Compression Algorithms:
┌───────────┬────────┬────────────┬──────────────┐
│ Algorithm │ Ratio  │ Compress   │ Decompress   │
├───────────┼────────┼────────────┼──────────────┤
│ zstd      │ 85-95% │ 500 MB/s   │ 1500 MB/s    │
│ gzip      │ 75%    │ 30 MB/s    │ 55 MB/s      │
│ lz4       │ 60%    │ 700 MB/s   │ 3000 MB/s    │
└───────────┴────────┴────────────┴──────────────┘
"""

import subprocess
import sys
import time
import json
from pathlib import Path

import fire
import zstandard as zstd
from rp.r import _ensure_bfs_installed


def scan_with_bfs(root: Path, follow_symlinks: bool = False):
    """
    Yield (inode, size, path) tuples using bfs.
    BFS traversal is better for NFS, streams output, includes inodes.
    """
    _ensure_bfs_installed()
    cmd = ['bfs']
    if follow_symlinks:
        cmd.append('-L')
    cmd.extend([str(root), '-type', 'f', '-printf', '%i\t%s\t%p\n'])

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=sys.stderr, text=True)
    for line in proc.stdout:
        line = line.rstrip('\n')
        parts = line.split('\t', 2)
        if len(parts) == 3:
            yield int(parts[0]), int(parts[1]), parts[2]
    proc.wait()


def save_json_zstd(files: list, output_path: Path, root: str):
    """Save file list to JSON + zstd compressed format."""
    data = {
        'version': 1,
        'root': root,
        'timestamp': int(time.time()),
        'files': files,
    }
    compressed = zstd.ZstdCompressor(level=3).compress(json.dumps(data, separators=(',', ':')).encode())
    with open(output_path, 'wb') as f:
        f.write(compressed)
    return len(compressed)


def load_json_zstd(input_path: Path) -> dict:
    """Load file list from JSON + zstd compressed format."""
    with open(input_path, 'rb') as f:
        compressed = f.read()
    return json.loads(zstd.ZstdDecompressor().decompress(compressed))


def scan(path: str = "~", output: str = None, follow_symlinks: bool = False):
    """
    Scan a folder and optionally save as JSON + zstd.

    Args:
        path: Path to scan.
        output: Output file (.json.zst).
        follow_symlinks: Follow symbolic links.
    """
    target = Path(path).expanduser().resolve()
    print(f"Scanning {target}...", file=sys.stderr)

    files = []
    total_size = 0

    for inode, size, fpath in scan_with_bfs(target, follow_symlinks=follow_symlinks):
        files.append([inode, size, fpath])
        if len(files) % 10000 == 0:
            print(f"  {len(files):,} files, {total_size / 1e9:.1f} GB...", file=sys.stderr)
        total_size += size

    print(f"Done: {len(files):,} files, {total_size / 1e9:.2f} GB", file=sys.stderr)

    if output:
        compressed_size = save_json_zstd(files, Path(output), str(target))
        raw_estimate = len(files) * 50
        ratio = (1 - compressed_size / raw_estimate) * 100 if raw_estimate > 0 else 0
        print(f"Saved: {output} ({compressed_size / 1e6:.2f} MB, ~{ratio:.0f}% compression)", file=sys.stderr)


def load(path: str):
    """Load and print stats from a saved scan."""
    print(f"Loading {path}...", file=sys.stderr)
    data = load_json_zstd(Path(path))
    files = data['files']
    print(f"Root: {data['root']}", file=sys.stderr)
    print(f"Scanned: {time.ctime(data['timestamp'])}", file=sys.stderr)
    print(f"Files: {len(files):,}", file=sys.stderr)
    total = sum(f[1] for f in files)
    print(f"Total: {total / 1e9:.2f} GB", file=sys.stderr)


if __name__ == "__main__":
    fire.Fire({"scan": scan, "load": load})
