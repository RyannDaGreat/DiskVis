#!/usr/bin/env python3
"""
Disk scanner using bfs, saves as CBOR + zstd.

Usage:
    python scan_folder.py /path/to/scan              # scan and print stats
    python scan_folder.py /path/to/scan -o scan.cbor # scan and save to file
    python scan_folder.py -f scan.cbor               # load and print stats

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
import argparse
from pathlib import Path

import cbor2
import zstandard as zstd
from rp.r import _ensure_bfs_installed


def scan_with_bfs(root: Path, skip_hidden: bool = True):
    """
    Yield (inode, size, path) tuples using bfs.
    BFS traversal is better for NFS, streams output, includes inodes.
    """
    _ensure_bfs_installed()
    cmd = ['bfs', str(root), '-type', 'f', '-printf', '%i\t%s\t%p\n']
    if skip_hidden:
        cmd.insert(2, '-name')
        cmd.insert(3, '.*')
        cmd.insert(4, '-prune')
        cmd.insert(5, '-o')
        cmd.append('-print')
        # Rebuild: bfs ROOT -name '.*' -prune -o -type f -printf '...' -print
        cmd = ['bfs', str(root), '-name', '.*', '-prune', '-o', '-type', 'f', '-printf', '%i\t%s\t%p\n']

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=sys.stderr, text=True)
    for line in proc.stdout:
        line = line.rstrip('\n')
        parts = line.split('\t', 2)
        if len(parts) == 3:
            yield int(parts[0]), int(parts[1]), parts[2]
    proc.wait()


def save_cbor_zstd(files: list, output_path: Path, root: str):
    """Save file list to CBOR + zstd compressed format."""
    data = {
        'version': 1,
        'root': root,
        'timestamp': int(time.time()),
        'files': files,  # list of [inode, size, path]
    }
    compressed = zstd.ZstdCompressor(level=3).compress(cbor2.dumps(data))
    with open(output_path, 'wb') as f:
        f.write(compressed)
    return len(compressed)


def load_cbor_zstd(input_path: Path) -> dict:
    """Load file list from CBOR + zstd compressed format."""
    with open(input_path, 'rb') as f:
        compressed = f.read()
    return cbor2.loads(zstd.ZstdDecompressor().decompress(compressed))


def main():
    parser = argparse.ArgumentParser(description='Scan folders and save as CBOR + zstd')
    parser.add_argument('path', nargs='?', help='Path to scan')
    parser.add_argument('-o', '--output', help='Output file (.cbor)')
    parser.add_argument('-f', '--file', help='Load from file instead of scanning')
    parser.add_argument('-a', '--all', action='store_true', help='Include hidden files')
    args = parser.parse_args()

    if args.file:
        print(f"Loading {args.file}...", file=sys.stderr)
        data = load_cbor_zstd(Path(args.file))
        files = data['files']
        print(f"Root: {data['root']}", file=sys.stderr)
        print(f"Scanned: {time.ctime(data['timestamp'])}", file=sys.stderr)
        print(f"Files: {len(files):,}", file=sys.stderr)
        total = sum(f[1] for f in files)
        print(f"Total: {total / 1e9:.2f} GB", file=sys.stderr)
        return

    target = Path(args.path).expanduser().resolve() if args.path else Path.home()
    print(f"Scanning {target}...", file=sys.stderr)

    files = []
    count = 0
    total_size = 0

    for inode, size, path in scan_with_bfs(target, skip_hidden=not args.all):
        files.append([inode, size, path])
        count += 1
        total_size += size
        if count % 10000 == 0:
            print(f"  {count:,} files, {total_size / 1e9:.1f} GB...", file=sys.stderr)

    print(f"Done: {count:,} files, {total_size / 1e9:.2f} GB", file=sys.stderr)

    if args.output:
        compressed_size = save_cbor_zstd(files, Path(args.output), str(target))
        raw_estimate = count * 50  # rough estimate: 50 bytes per file entry
        ratio = (1 - compressed_size / raw_estimate) * 100 if raw_estimate > 0 else 0
        print(f"Saved: {args.output} ({compressed_size / 1e6:.2f} MB, ~{ratio:.0f}% compression)", file=sys.stderr)


if __name__ == "__main__":
    main()
