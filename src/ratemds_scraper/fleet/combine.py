#!/usr/bin/env python3
"""
Merge per-host scraper outputs into one deduplicated dataset.

Each worker writes its own ``doctors.csv`` / ``reviews.csv``. After pulling them
into ``<combined>/shards/<host>/`` (see ``fleet/scripts/combine.sh``), this
merges every shard into ``<combined>/doctors.csv`` + ``<combined>/reviews.csv``.

Dedup keys (identical to the scraper's own ``dedupe`` step, so merging is
lossless even when shards overlap):

    doctors : (doctor_id)                          -- keep last seen
    reviews : (doctor_id, review_type, review_id)  -- keep first seen

Usage:
    python -m ratemds_scraper.fleet.combine <combined_dir>
"""

from __future__ import annotations

import csv
import os
import sys
import glob

csv.field_size_limit(16 * 1024 * 1024)   # some reviews are long


def _merge(kind, key_cols, keep, shard_files, out_path):
    seen: dict = {}
    header = None
    per_shard: dict = {}
    total_in = 0
    for host, path in shard_files:
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            per_shard[host] = 0
            continue
        with open(path, encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames:
                header = header or reader.fieldnames
            n = 0
            for row in reader:
                n += 1
                total_in += 1
                k = tuple(row.get(c, "") for c in key_cols)
                if keep == "last" or k not in seen:
                    seen[k] = row
            per_shard[host] = n
    if header is None:
        print(f"  [{kind}] no rows found in any shard")
        return
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(seen.values())
    print(f"  [{kind}] {total_in} rows in -> {len(seen)} unique "
          f"(-{total_in - len(seen)} dupes)  ->  {out_path}")
    for host, n in sorted(per_shard.items()):
        print(f"        {host}: {n}")


def merge(combined: str) -> None:
    """Merge every ``<combined>/shards/<host>/`` into combined CSVs."""
    shards_dir = os.path.join(combined, "shards")
    hosts = sorted(d for d in os.listdir(shards_dir)
                   if os.path.isdir(os.path.join(shards_dir, d))) \
        if os.path.isdir(shards_dir) else []
    if not hosts:
        print(f"No shard dirs under {shards_dir}; run the pull step first.")
        sys.exit(1)

    print(f"Merging shards: {', '.join(hosts)}")
    doc_files = [(h, os.path.join(shards_dir, h, "doctors.csv")) for h in hosts]
    rev_files = [(h, os.path.join(shards_dir, h, "reviews.csv")) for h in hosts]

    _merge("doctors", ("doctor_id",), "last", doc_files,
           os.path.join(combined, "doctors.csv"))
    _merge("reviews", ("doctor_id", "review_type", "review_id"), "first", rev_files,
           os.path.join(combined, "reviews.csv"))

    anoms = glob.glob(os.path.join(shards_dir, "*", "anomalies.log"))
    total = 0
    for a in anoms:
        with open(a, encoding="utf-8") as fh:
            total += sum(1 for _ in fh)
    print(f"  [anomalies] {total} line(s) across {len(anoms)} shard log(s)")


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    combined = argv[0] if argv else "output/combined"
    merge(combined)


if __name__ == "__main__":
    main()
