"""Poll Tuya cloud energy meters and store the readings as Measurements.

Runs in-process against the ORM rather than posting to ``/nilm/api/measurements/``.
That removes the token auth, the DRF serialisation round trip and — importantly —
the naive/aware datetime conversion that silently shifted every API-ingested row
by the UTC offset. It also makes the history backfill a handful of bulk inserts
instead of ~100k serial HTTP requests.

Usage
-----
    python manage.py tuya_poll                      # continuous loop (systemd)
    python manage.py tuya_poll --once               # single poll, then exit (cron)
    python manage.py tuya_poll --history --since 2026-09-15
    python manage.py tuya_poll --history --device bf17... --dry-run

Devices
-------
Every ``Device`` row with ``device_type='TUYA'`` is polled, using its
``device_id`` as the Tuya device id — they are the same string. Register devices
in the admin (or via ``sync_tuya_devices``) first; this command never creates
them, so a typo surfaces as "not registered" rather than a stray row.

Row shape
---------
Tuya returns one reading per poll, not a sampled window, so there is no
transient buffer to store::

    readings  = [current_a]   # amperes, measured — matches the edge convention
    value     = current_a     # instantaneous level, NOT a step delta
    telemetry = {power_w, voltage_v, current_a, energy_kwh, ...}
    features  = None          # no window, so no avg/min/max/std

Note ``value`` therefore means something different than it does for DOMOBOI
rows, where it is a signed step delta. Consumers must branch on ``device_type``.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from nilm.models import Device, Measurement


# Datapoints worth fetching during a backfill. Without this the API also returns
# switches, countdowns and calibration coefficients, tripling the row count for
# data nothing reads.
DEFAULT_HISTORY_CODES = "cur_power,cur_current,cur_voltage,add_ele"

# A household feed will not carry more than this. Used as a tripwire for unit
# errors (a milliamp value leaking through as amperes reads ~1000x high) rather
# than as a physical limit — see MILLI_EXPONENT in tuya_energy/dp.py.
MAX_PLAUSIBLE_CURRENT_A = 100.0

# Tuya's datapoint-log endpoints return at most 100 rows per request, and the
# v1.0 fallback (/v1.0/devices/{id}/logs) reports has_next=true while serving an
# empty second page — so pagination cannot get past the cap. Verified: a single
# 24h window yields 100 readings, while four 6h windows over the same period
# yield 400. The backfill therefore slices the range into small windows so each
# request stays well under the limit; without this a day's history is silently
# truncated to the first 100 rows and still reported as success.
HISTORY_PAGE_CAP = 100
DEFAULT_WINDOW_HOURS = 2

# The datapoint-log endpoint is rate limited, and it degrades rather than
# failing: hammer it and successive requests return partial results, then empty
# ones, with no error. Measured — four back-to-back backfills of the same range
# returned 522, 130, 0, 0 instants; after a 120s pause the same query returned
# the full 650. Pacing requests keeps a backfill from starving itself, and is
# far cheaper than discovering the gaps later.
HISTORY_REQUEST_PACING_S = 0.5

# A throttled response is an empty response — indistinguishable from "this
# window genuinely has no data" unless you retry. Long backfills therefore fail
# silently: a 7-day run over 11 devices (~900 requests) had its later windows
# return nothing and still reported success, losing three days of data that was
# available the whole time. Any empty window is retried after a pause; if the
# retry finds data, we were being throttled and the pacing is increased for the
# rest of the run.
EMPTY_WINDOW_RETRIES = 2
EMPTY_WINDOW_BACKOFF_S = 8.0
THROTTLED_PACING_S = 2.0

# `value` is DecimalField(max_digits=10, decimal_places=2).
VALUE_QUANTUM = Decimal("0.01")

# Per-device datapoint scales are derived from the device *specification*, which
# EnergyClient caches in memory for the life of the process. Under cron every
# run is a fresh process, so that cache is always cold and each cycle spends a
# second API call per device re-fetching metadata that effectively never
# changes — doubling quota consumption. Persisting it across runs removes that.
SPEC_CACHE_PATH = Path(settings.BASE_DIR) / ".tuya_spec_cache.json"
SPEC_CACHE_MAX_AGE_S = 7 * 24 * 3600


def coalesce_history(readings):
    """Merge long-format history readings into one dense reading per instant.

    Tuya report logs arrive as one row per datapoint *event*, so `cur_power`,
    `cur_current` and `cur_voltage` typically appear as three separate readings
    sharing an identical timestamp. Stored raw they would collide on the
    (device, timestamp) unique constraint, losing two rows in three.

    Merging them also makes history the same shape as a live poll, so one schema
    serves both. Cumulative datapoints such as `add_ele` tick on their own
    schedule and land in their own bucket, which is correct.

    Returns (timestamp, metrics) pairs ordered by timestamp.
    """
    buckets: dict[datetime, dict] = {}
    for reading in readings:
        merged = buckets.setdefault(reading.timestamp, {})
        for key, value in (reading.metrics or {}).items():
            if key == "raw":
                merged.setdefault("raw", {}).update(value or {})
            elif value is not None:
                merged[key] = value
    return sorted(buckets.items())


class Command(BaseCommand):
    help = "Poll Tuya cloud energy meters into Measurement rows."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--once", action="store_true",
            help="Poll live readings once and exit (suitable for cron).",
        )
        mode.add_argument(
            "--history", action="store_true",
            help="Backfill datapoint history instead of polling live.",
        )
        parser.add_argument(
            "--since", metavar="YYYY-MM-DD",
            help="Start date for --history (default: 7 days ago, Tuya's retention limit).",
        )
        parser.add_argument(
            "--until", metavar="YYYY-MM-DD",
            help="End date for --history (exclusive; default: now). Use with --since "
                 "to backfill one day at a time, which keeps each run short enough "
                 "to avoid rate limiting.",
        )
        parser.add_argument(
            "--device", metavar="TUYA_ID", action="append",
            help="Restrict to one device; repeatable. Default: all TUYA devices.",
        )
        parser.add_argument(
            "--codes", default=DEFAULT_HISTORY_CODES,
            help=f"Comma-separated datapoint codes for --history (default: {DEFAULT_HISTORY_CODES}).",
        )
        parser.add_argument(
            "--interval", type=float, default=None,
            help="Seconds between polls in continuous mode (default: TUYA_POLL_INTERVAL).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Fetch and report, but write nothing.",
        )
        parser.add_argument(
            "--refresh-specs", action="store_true",
            help="Ignore the cached datapoint scales and re-fetch them "
                 "(use after a device firmware update).",
        )
        parser.add_argument(
            "--window-hours", type=float, default=DEFAULT_WINDOW_HOURS,
            help=f"Slice the --history range into windows of this many hours "
                 f"(default: {DEFAULT_WINDOW_HOURS}). Tuya caps every request at "
                 f"{HISTORY_PAGE_CAP} rows, so larger windows silently lose data.",
        )

    # ------------------------------------------------------------------ setup

    def _build_client(self):
        """Construct an EnergyClient from Django settings.

        Deliberately avoids TuyaConfig.from_env(), which calls
        find_dotenv(usecwd=True) — resolving against the process's working
        directory — and mutates os.environ as a side effect.
        """
        try:
            from tuya_energy import TuyaConfig, TuyaOpenAPI, EnergyClient
        except ImportError as exc:
            raise CommandError(
                "tuya_energy is not installed. Install it with:\n"
                "    pip install -e /path/to/domoboi-tuya\n"
                "or via the pinned requirement in requirements.txt."
            ) from exc

        if not settings.TUYA_ACCESS_ID or not settings.TUYA_ACCESS_SECRET:
            raise CommandError(
                "TUYA_ACCESS_ID and TUYA_ACCESS_SECRET must be set "
                "(environment or .env)."
            )

        config = TuyaConfig(
            access_id=settings.TUYA_ACCESS_ID,
            access_secret=settings.TUYA_ACCESS_SECRET,
            region=settings.TUYA_REGION,
        )
        return EnergyClient(TuyaOpenAPI(config))

    def _load_spec_cache(self, client, refresh=False):
        """Warm EnergyClient's scale-map cache from disk.

        Saves one specification request per device per run. Silently ignores a
        missing, stale or unreadable cache — the client then fetches as normal,
        so a bad cache costs quota but never correctness.
        """
        if refresh or not SPEC_CACHE_PATH.exists():
            return 0
        try:
            age = time.time() - SPEC_CACHE_PATH.stat().st_mtime
            if age > SPEC_CACHE_MAX_AGE_S:
                return 0  # expire, in case a firmware update changed the scales
            cached = json.loads(SPEC_CACHE_PATH.read_text())
        except (OSError, ValueError):
            return 0
        if not isinstance(cached, dict):
            return 0
        # Values are {dp_code: scale}; coerce defensively in case of hand-edits.
        for device_id, scales in cached.items():
            if isinstance(scales, dict):
                client._spec_cache[device_id] = {k: int(v) for k, v in scales.items()}
        return len(cached)

    def _save_spec_cache(self, client):
        try:
            SPEC_CACHE_PATH.write_text(json.dumps(client._spec_cache, indent=0))
        except OSError:
            pass  # a non-writable directory must not break collection

    def _resolve_devices(self, only):
        """Return {tuya_device_id: Device} for the registered Tuya devices."""
        queryset = Device.objects.filter(device_type="TUYA")
        if only:
            queryset = queryset.filter(device_id__in=only)
        devices = {d.device_id: d for d in queryset}

        if only:
            missing = sorted(set(only) - set(devices))
            if missing:
                raise CommandError(
                    "Not registered as TUYA devices: "
                    + ", ".join(missing)
                    + "\nRegister them in the admin or run `manage.py sync_tuya_devices`."
                )
        if not devices:
            raise CommandError(
                "No devices with device_type='TUYA' found. "
                "Run `manage.py sync_tuya_devices` first."
            )
        return devices

    # ------------------------------------------------------------- conversion

    def _build_measurement(self, device, ts, metrics):
        """Build an unsaved Measurement from one instant's metrics.

        bulk_create() bypasses Model.save(), so `timestamp` and `value` — which
        that override would normally populate — are set explicitly here. Leaving
        `timestamp` unset would make the row invisible to Device.is_active, the
        admin date_hierarchy and every dashboard count.
        """
        current = metrics.get("current_a")

        if current is not None and abs(float(current)) > MAX_PLAUSIBLE_CURRENT_A:
            raise CommandError(
                f"Implausible current for {device.device_id}: {current} A "
                f"(limit {MAX_PLAUSIBLE_CURRENT_A} A). This usually means a "
                f"milliamp value reached us unscaled — check the device's "
                f"declared unit/scale before ingesting."
            )

        readings = [float(current)] if current is not None else []
        value = (
            Decimal(str(current)).quantize(VALUE_QUANTUM)
            if current is not None else Decimal("0.00")
        )

        return Measurement(
            device=device,
            start_time=ts,
            end_time=ts,      # a point sample, not a window
            timestamp=ts,
            readings=readings,
            value=value,
            telemetry=metrics or None,
            features=None,    # no sampled window, so no summary statistics
        )

    def _store(self, measurements, dry_run):
        """Insert measurements, skipping any that already exist."""
        if not measurements:
            return 0
        if dry_run:
            return len(measurements)
        before = Measurement.objects.count()
        with transaction.atomic():
            Measurement.objects.bulk_create(
                measurements, batch_size=1000, ignore_conflicts=True,
            )
        return Measurement.objects.count() - before

    # ------------------------------------------------------------------ modes

    def _poll_once(self, client, devices, dry_run):
        errors = []
        readings = client.read_many(list(devices), errors=errors)

        rows = []
        for reading in readings:
            device = devices.get(reading.device_id)
            if device is None:
                continue
            rows.append(self._build_measurement(device, reading.timestamp, reading.metrics))

        stored = self._store(rows, dry_run)
        for device_id, err in errors:
            self.stderr.write(self.style.WARNING(f"  read failed for {device_id}: {err}"))

        verb = "would store" if dry_run else "stored"
        style = self.style.WARNING if errors else self.style.SUCCESS
        self.stdout.write(style(
            f"  {verb} {stored}/{len(devices)} reading(s)"
            + (f", {len(errors)} error(s)" if errors else "")
        ))
        return stored, len(errors)

    def _fetch_history(self, client, tuya_id, since, until, code_list, window):
        """Fetch history in small windows, working around the request cap and throttling.

        Returns (readings, stats). Two distinct hazards are handled:

        * **The 100-row cap.** Each request returns at most HISTORY_PAGE_CAP rows
          and the v1.0 fallback cannot paginate past it, so windows are kept
          small and any that come back full are counted as possibly truncated.
        * **Silent throttling.** An over-driven endpoint returns empty rather
          than erroring. Every empty window is retried after a backoff; if the
          retry finds data we were throttled, so the pacing is raised for the
          remainder of the run.
        """
        readings = []
        stats = {"capped": 0, "empty": 0, "recovered": 0, "throttled": False}
        pacing = HISTORY_REQUEST_PACING_S
        start = since
        first = True

        while start < until:
            end = min(start + window, until)

            batch = []
            for attempt in range(EMPTY_WINDOW_RETRIES + 1):
                if not first:
                    time.sleep(pacing if attempt == 0 else EMPTY_WINDOW_BACKOFF_S)
                first = False
                batch = list(client.history_readings(tuya_id, start, end, codes=code_list))
                if batch:
                    if attempt:
                        # Data appeared only after backing off — we were throttled.
                        stats["recovered"] += 1
                        stats["throttled"] = True
                        pacing = max(pacing, THROTTLED_PACING_S)
                    break
            else:
                stats["empty"] += 1  # genuinely empty, or throttled beyond recovery

            if len(batch) >= HISTORY_PAGE_CAP:
                stats["capped"] += 1
            readings.extend(batch)
            start = end

        return readings, stats

    def _backfill(self, client, devices, since, until, codes, dry_run, window_hours):
        code_list = [c.strip() for c in codes.split(",") if c.strip()] or None
        window = timedelta(hours=window_hours)
        windows = max(1, int((until - since) / window + 0.999))
        total = 0

        self.stdout.write(
            f"  range {since.date()} → {until.date()} "
            f"in {windows} window(s) of {window_hours:g}h per device"
        )

        incomplete = []

        for tuya_id, device in devices.items():
            raw, stats = self._fetch_history(client, tuya_id, since, until, code_list, window)
            coalesced = coalesce_history(raw)
            rows = [self._build_measurement(device, ts, m) for ts, m in coalesced]
            stored = self._store(rows, dry_run)
            total += stored

            verb = "would store" if dry_run else "stored"
            detail = (
                f" ({len(coalesced) - stored} already present)"
                if not dry_run and len(coalesced) != stored else ""
            )
            self.stdout.write(
                f"  {tuya_id}: {len(raw)} event(s) → {len(coalesced)} instant(s), "
                f"{verb} {stored}{detail}"
            )

            if stats["recovered"]:
                self.stdout.write(
                    f"    recovered {stats['recovered']} window(s) after backing off"
                )
            if stats["capped"]:
                incomplete.append(tuya_id)
                self.stderr.write(self.style.WARNING(
                    f"    {stats['capped']} window(s) hit the {HISTORY_PAGE_CAP}-row cap — "
                    f"possibly truncated; re-run with a smaller --window-hours "
                    f"(currently {window_hours:g})."
                ))
            if stats["empty"]:
                # Distinguishing "device was off" from "still throttled" is not
                # possible from here, so say so rather than implying completeness.
                incomplete.append(tuya_id)
                self.stderr.write(self.style.WARNING(
                    f"    {stats['empty']} window(s) returned nothing"
                    + (" despite backing off — likely still rate limited"
                       if stats["throttled"] else
                       " — the device may have been offline, or rate limiting hid the data")
                ))

        if incomplete:
            self.stderr.write(self.style.WARNING(
                f"\n  {len(set(incomplete))} device(s) may be incomplete. Re-running is "
                f"free (the unique constraint dedupes) and fills gaps — consider a "
                f"narrower --since range per run."
            ))
        return total

    # ------------------------------------------------------------------- main

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        client = self._build_client()
        devices = self._resolve_devices(options.get("device"))

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — nothing will be written"))
        self.stdout.write(f"{len(devices)} Tuya device(s): {', '.join(sorted(devices))}")

        warmed = self._load_spec_cache(client, refresh=options["refresh_specs"])
        if warmed:
            self.stdout.write(f"  reused cached datapoint scales for {warmed} device(s)")

        if options["history"]:
            def _parse(flag):
                try:
                    return datetime.fromisoformat(options[flag]).replace(
                        tzinfo=dt_timezone.utc
                    )
                except ValueError:
                    raise CommandError(f"--{flag} must be YYYY-MM-DD")

            # Tuya retains report logs for roughly 7 days on the free tier.
            since = _parse("since") if options["since"] else timezone.now() - timedelta(days=7)
            until = _parse("until") if options["until"] else timezone.now()
            if until <= since:
                raise CommandError(f"--until ({until.date()}) must be after --since ({since.date()})")

            total = self._backfill(
                client, devices, since, until, options["codes"], dry_run,
                options["window_hours"],
            )
            self._save_spec_cache(client)
            self.stdout.write(self.style.SUCCESS(f"Backfill complete — {total} measurement(s)"))
            return

        if options["once"]:
            self._poll_once(client, devices, dry_run)
            self._save_spec_cache(client)
            return

        interval = options["interval"] or settings.TUYA_POLL_INTERVAL
        self.stdout.write(f"Continuous mode — polling every {interval:g}s (Ctrl-C to stop)")
        while True:
            started = time.monotonic()
            try:
                self._poll_once(client, devices, dry_run)
            except CommandError:
                # Configuration or data-integrity problem: no point retrying.
                raise
            except Exception as exc:
                # A transient cloud/network failure must not kill the service.
                self.stderr.write(self.style.ERROR(f"  poll cycle failed: {exc}"))
            # Drift-corrected: the interval is between cycle *starts*.
            time.sleep(max(0.0, interval - (time.monotonic() - started)))
