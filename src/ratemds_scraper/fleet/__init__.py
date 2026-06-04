"""
Optional distributed-crawl helpers.

RateMDs rate-limits per IP, so the single real way to go faster is to crawl from
more IPs. This subpackage shards a crawl across several machines (each owning a
disjoint set of states), then merges their per-host CSVs into one deduplicated
dataset. The shell scripts under ``fleet/scripts`` drive a fleet over plain SSH;
:func:`ratemds_scraper.fleet.combine.merge` does the host-agnostic merge.
"""
