#!/usr/bin/env python3
"""
S3 scanner using ListObjectsV2 with parallel prefix fan-out.

Usage:
    python scan_census.py scan-s3 s3://bucket/prefix
    python scan_census.py scan-s3 s3://bucket/prefix --workers=16 --output=bucket.census.zst
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3

from census.scan import (
    finalize_scan,
    init_checkpoints,
    maybe_checkpoint,
    print_progress,
    save_checkpoint,
)


# =============================================================================
# PURE FUNCTIONS
# =============================================================================

def parse_s3_uri(uri: str) -> tuple[str, str]:
    """
    Pure. Parse s3://bucket/prefix into (bucket, prefix).

    Args:
        uri (str): S3 URI like 's3://my-bucket/some/prefix'.

    Returns:
        tuple[str, str]: (bucket, prefix). Prefix may be empty.

    Examples:
        >>> parse_s3_uri('s3://my-bucket/some/prefix/')
        ('my-bucket', 'some/prefix/')
        >>> parse_s3_uri('s3://my-bucket')
        ('my-bucket', '')
        >>> parse_s3_uri('s3://my-bucket/')
        ('my-bucket', '')
    """
    if not uri.startswith('s3://'):
        raise ValueError(f"Not an S3 URI: {uri}")
    path = uri[5:]  # strip 's3://'
    if '/' in path:
        bucket, prefix = path.split('/', 1)
    else:
        bucket, prefix = path, ''
    return bucket, prefix


# =============================================================================
# QUERIES - S3 listing
# =============================================================================

def discover_prefixes(client, bucket: str, prefix: str, target_count: int) -> list[str]:
    """
    Query. List top-level prefixes under a path for parallel fan-out.

    Uses '/' delimiter to find immediate subdirectories. If fewer than
    target_count prefixes exist, returns the original prefix as a single
    item (no fan-out needed).

    Args:
        client: boto3 S3 client.
        bucket (str): S3 bucket name.
        prefix (str): S3 prefix to list under.
        target_count (int): Desired number of prefixes for parallelism.

    Returns:
        list[str]: Prefixes to scan in parallel.
    """
    paginator = client.get_paginator('list_objects_v2')
    prefixes = []

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter='/'):
        for cp in page.get('CommonPrefixes', []):
            prefixes.append(cp['Prefix'])

    if len(prefixes) < target_count:
        return [prefix]

    return prefixes


def list_prefix(client, bucket: str, prefix: str) -> list:
    """
    Query. List all objects under a prefix, returning census entries.

    Args:
        client: boto3 S3 client.
        bucket (str): S3 bucket name.
        prefix (str): S3 prefix to list.

    Returns:
        list: Census entries as [0, size, key]. Inode is always 0 (no S3 equivalent).
    """
    paginator = client.get_paginator('list_objects_v2')
    entries = []

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get('Contents', []):
            entries.append([0, obj['Size'], obj['Key']])

    return entries


# =============================================================================
# COMMAND
# =============================================================================

def scan(
    root: str,
    output: str = 'census_output.census.zst',
    checkpoint_dir: str = None,
    checkpoint_interval: int = 100_000,
    workers: int = 16,
    show_progress: bool = True,
) -> str:
    """
    Command. Scan an S3 path and save as census.

    Touches: S3 (read), checkpoint folder (write), output file (write).

    Args:
        root (str): S3 URI like 's3://bucket/prefix'.
        output (str): Output file path. Default: 'census_output.census.zst'
        checkpoint_dir (str): Where to store checkpoints. Default: None (auto)
        checkpoint_interval (int): Entries between checkpoints. Default: 100000
        workers (int): Parallel listing threads. Default: 16
        show_progress (bool): Print progress to stderr. Default: True

    Returns:
        str: The output file path.
    """
    bucket, prefix = parse_s3_uri(root)
    client = boto3.client('s3')

    ckpt_dir, resume_point, checkpoint_num = init_checkpoints(
        root, checkpoint_dir, show_progress,
        workers=workers,
    )

    if show_progress:
        print(f"Scanning s3://{bucket}/{prefix} with {workers} workers...", file=sys.stderr)
        print(f"Discovering prefixes...", file=sys.stderr)

    prefixes = discover_prefixes(client, bucket, prefix, workers)

    if show_progress:
        print(f"Found {len(prefixes)} prefixes to scan", file=sys.stderr)

    files = []
    total_size = 0
    prefixes_completed = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(list_prefix, client, bucket, p): p for p in prefixes}

        for future in as_completed(futures):
            prefix_name = futures[future]
            entries = future.result()

            for entry in entries:
                # Skip if resuming
                if resume_point is not None and entry[2] <= resume_point:
                    continue
                files.append(entry)
                total_size += entry[1]

            prefixes_completed += 1

            if show_progress:
                print_progress(prefixes_completed, len(files), total_size)

            # Checkpoint based on accumulated entries
            if len(files) >= checkpoint_interval:
                save_checkpoint(ckpt_dir, checkpoint_num, files, root, show_progress)
                checkpoint_num += 1
                files = []

    # Save final chunk
    if files:
        save_checkpoint(ckpt_dir, checkpoint_num, files, root, show_progress)

    return finalize_scan(ckpt_dir, output, show_progress)
