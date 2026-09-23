# Tuya collector deployment

The collector is the `tuya_poll` management command, run from **cron** — the
host has no root, and a periodic `--once`/`--history` invocation is self-healing
in a way a supervised daemon is not: a failed run is simply replaced by the next.

## Which mode, and why history wins

Two ways to collect, and they are not equivalent:

| | live (`--once`) | history (`--history`) |
|---|---|---|
| what you get | 1 reading per device per run | **every datapoint change Tuya recorded** |
| timestamps | collector time (when we asked) | **real device event time** |
| a day's data | 1 row/run/device | ~650 instants/device (one per ~2 min) |
| API calls/device/day | 1 per run | ~12–24 total |
| determinism | one request, one row | rate limited; see below |

During the training phase — where routines matter and latency does not — a
**once-daily history backfill is strictly better than minute-by-minute live
polling**: ~250× fewer API calls, ~6× fewer rows, real device timestamps, and
change-driven sampling that captures transitions instead of re-reading a stable
value 1,440 times.

## Crontab entry (training phase)

```cron
# Domoboi — pull yesterday's Tuya datapoint history, once a day at 03:17.
# 48h lookback so a single missed run self-heals; re-ingesting is free because
# of the (device, timestamp) unique constraint.
17 3 * * * cd /path/to/domoboi_django && /usr/bin/flock -n /tmp/tuya_poll.lock .venv/bin/python manage.py tuya_poll --history --since "$(date -d '2 days ago' +\%F)" >> logs/tuya_poll.log 2>&1
```

Note `\%` — cron treats an unescaped `%` as a newline. On a BSD/macOS host use
`date -v-2d +\%F`.

Replace `/path/to/domoboi_django` and `.venv` with the real paths. The `cd`
matters: `settings.py` calls `load_dotenv()`, which searches upward from the
working directory, and cron inherits none of your shell environment.

Optionally add a live poll so the admin's `is_active` column stays meaningful —
it uses a 60-minute window, so with daily-only collection every device reads
inactive:

```cron
*/15 * * * * cd /path/to/domoboi_django && .venv/bin/python manage.py tuya_poll --once >> logs/tuya_poll.log 2>&1
```

That costs 1 call per device per run (~1,050/day for 11 devices).

## Rate limiting — the thing that will bite you

The datapoint-log endpoint is rate limited and **degrades instead of failing**:
hammered, it returns partial results, then empty ones, with no error. Measured —
four back-to-back backfills of the same range returned 522, 130, 0 and 0
instants; after a 120 s pause the same query returned the full 650.

Two consequences:

- `tuya_poll` paces its own history requests (`HISTORY_REQUEST_PACING_S`). Don't
  remove that.
- **Don't re-run a backfill repeatedly to "check" it.** An empty result usually
  means throttling, not missing data. Wait a couple of minutes.

Each request also caps at 100 rows, and the v1.0 fallback endpoint advertises
`has_next` while serving an empty second page — so pagination cannot get past
it. `--window-hours` (default 2) slices the range to stay under the cap, and the
command warns when a window comes back at exactly 100. Lower it to 1 or 0.5 if
you see that warning and want the missing rows; overlapping re-runs are free.

## Required environment

`.env` in the project root (or the host's env-var mechanism) must define:

| variable | notes |
|---|---|
| `TUYA_ACCESS_ID` | Tuya cloud project Access ID |
| `TUYA_ACCESS_SECRET` | keep this file mode 600 |
| `TUYA_REGION` | `eu` (default), `us`, `cn`, … |
| `TUYA_POLL_INTERVAL` | only used by continuous mode; irrelevant under cron |

No API URL or edge token is needed — the command writes through the ORM, so
there is no HTTP hop and no token to authenticate.

## Datapoint-scale cache

`tuya_poll` writes `.tuya_spec_cache.json` in the project root: a small map of
per-device datapoint scales. Without it every cron run re-fetches each device's
specification, **doubling API consumption** (2 calls per device per minute
instead of 1) for metadata that effectively never changes.

It expires after 7 days. Force a refresh after a firmware update with
`--refresh-specs`, or just delete the file. It is regenerable and gitignored;
losing it costs one extra round of calls, never correctness.

## Log rotation

The crontab line appends forever. Add a weekly rotation, e.g.:

```cron
0 4 * * 0 cd /path/to/domoboi_django && mv logs/tuya_poll.log logs/tuya_poll.log.1 2>/dev/null
```

or configure `logrotate` if the host provides it.

## Checking it works

```bash
tail -f logs/tuya_poll.log            # should show one line per minute
```

The authoritative check is the admin: `/admin/nilm/device/` shows `is_active`
for every Tuya device that reported within the last 60 minutes. Do **not**
judge by the location detail pages — `get_placeholder_events()` fabricates demo
events when a date has none, so those look populated even when ingestion is
completely broken.

## Initial backfill

Run once by hand before enabling the cron job:

```bash
.venv/bin/python manage.py tuya_poll --history --since 2026-09-15
```

Retention is roughly 7 days, so that is the practical limit — the backfill is a
one-time opportunity. Expect ~15 s per device-day and roughly 650 instants per
device per day. Re-running any range is free: the `(device, timestamp)` unique
constraint turns it into a no-op.

If a device returns nothing, check it is actually reporting (`tuya-energy read
<id>`) before assuming data loss — the endpoint returns empty when throttled.

## Other modes

```bash
manage.py tuya_poll                 # continuous loop (for the systemd unit)
manage.py tuya_poll --once          # one cycle — what cron runs
manage.py tuya_poll --dry-run       # fetch and report, write nothing
manage.py tuya_poll --device <id>   # restrict to one meter
manage.py sync_tuya_devices --dry-run   # compare cloud meters against the DB
```

`domoboi-tuya.service` in this directory is a systemd unit for a future move to
a root-capable host; it is not used in the current deployment.
