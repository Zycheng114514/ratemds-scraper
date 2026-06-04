# Distributed crawling

RateMDs rate-limits per IP. On one IP, ~4 workers is the sweet spot; adding more
threads just trips Cloudflare 429s and self-throttles. So the only real way to
go faster is **more IPs** — i.e. more machines, each crawling a disjoint slice
of states from its own IP with its own `cf_clearance`.

The `fleet` tooling does exactly that and then merges the results losslessly.
The scripts live inside the package at `ratemds_scraper/fleet/scripts/` (copy
them out, or run them in place).

## 1. Describe your fleet

Copy `fleet.conf.example` to `fleet.conf` and list one worker per line —
`<ssh-host> <comma-separated state slugs>`:

```
worker-a   ca,ga,nc,tn,in,al,la,nv,ar,hi,nh,ak,vt
worker-b   tx,oh,mi,ma,mo,sc,ky,ia,ms,wv,me,nd,wy
worker-c   fl,il,nj,az,md,mn,or,ut,ks,id,mt,sd,dc/washington
worker-d   ny,pa,va,wa,wi,co,ok,ct,nm,ne,ri,de
```

`<ssh-host>` is anything your SSH understands (a `~/.ssh/config` alias, or
`user@ip`). Disjoint state sets avoid wasted work; overlap is still safe because
the merge dedupes by id.

## 2. Deploy, start, watch

```bash
./fleet.sh deploy     # scp worker scripts + pip-install the package on each host
./fleet.sh start      # each host starts its run loop on its assigned states
./fleet.sh status     # progress on every worker
./fleet.sh tail       # follow the first worker's log
./fleet.sh stop       # stop all (resumable)
```

Each worker runs `runloop.sh`, which re-invokes the scraper until its queue is
drained (so transient give-ups get retried automatically) and survives restarts
via the ledger. A worker is also usable standalone:

```bash
RATEMDS_ROOT=~/ratemds-run ./worker.sh start "ca,nv"
./worker.sh status
```

## 3. Merge

```bash
./combine.sh                    # pull every worker's CSVs -> output/combined/ and merge
LOCAL_OUT=./output ./combine.sh # also fold in a worker running on the control box
```

`combine.sh` rsyncs each host's `doctors.csv` / `reviews.csv` / `anomalies.log`
into `output/combined/shards/<host>/`, then
`python -m ratemds_scraper.fleet.combine` merges them, deduping doctors by
`doctor_id` and reviews by `(doctor_id, review_type, review_id)`. Run it anytime
to snapshot progress — it never stops the workers.

## Rebalancing

Because each worker is resumable and the merge dedupes, you can move states
between workers freely: stop a worker, edit its line in `fleet.conf`, and
restart. A worker that finishes early can be handed another's leftover states —
copy the busy worker's `output/doctors_queue.csv` + `state/done.jsonl` to the
idle one first and it will skip everything already scraped, only doing the
remainder.
