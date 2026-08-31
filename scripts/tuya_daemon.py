#!/usr/bin/env python3
"""tuya_daemon.py — Polls Tuya cloud energy meters and posts readings to the domoboi API.

No local aggregation or event detection. Every DP reading is stored as a
separate Measurement row, preserving maximum granularity.

USAGE
    python tuya_daemon.py             # continuous loop (systemd / long-running)
    python tuya_daemon.py --once      # single poll then exit (cron-friendly)
    python tuya_daemon.py --history   # backfill all available DP log history
    python tuya_daemon.py --history --since 2026-08-01  # limit backfill start

REQUIRED ENVIRONMENT VARIABLES
    DOMOBOI_API_URL       Base URL of the domoboi server, e.g. http://suara.domoboi.es
    DOMOBOI_API_TOKEN     Value of EDGE_API_TOKEN configured in domoboi
    TUYA_DEVICE_MAP       JSON object {"tuya_device_id": "domoboi_device_id", ...}
    TUYA_ACCESS_ID        Tuya cloud project Access ID
    TUYA_ACCESS_SECRET    Tuya cloud project Access Secret

OPTIONAL ENVIRONMENT VARIABLES
    TUYA_REGION           eu (default) | we | us | us-e | cn | in | sg
    TUYA_POLL_INTERVAL    Seconds between live polls (default: 60)

PREREQUISITES
    pip install tuya_energy requests python-dotenv
    # tuya_energy lives at /path/to/tuya — install with:
    # pip install -e /path/to/tuya

    Devices must be pre-registered in the domoboi admin:
      Device.device_id = <tuya_device_id>, Device.device_type = TUYA
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

# Load .env if present (optional dependency)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("tuya_daemon")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    required = ["DOMOBOI_API_URL", "DOMOBOI_API_TOKEN", "TUYA_DEVICE_MAP",
                "TUYA_ACCESS_ID", "TUYA_ACCESS_SECRET"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        log.error("Missing required environment variables: %s", ", ".join(missing))
        sys.exit(1)

    return {
        "api_url":       os.environ["DOMOBOI_API_URL"].rstrip("/"),
        "api_token":     os.environ["DOMOBOI_API_TOKEN"],
        "device_map":    json.loads(os.environ["TUYA_DEVICE_MAP"]),
        "poll_interval": int(os.environ.get("TUYA_POLL_INTERVAL", "60")),
    }


# ---------------------------------------------------------------------------
# tuya_energy client
# ---------------------------------------------------------------------------

def build_tuya_client():
    try:
        from tuya_energy import TuyaConfig, TuyaOpenAPI, EnergyClient
    except ImportError:
        log.error(
            "tuya_energy not installed. Install with: pip install -e /path/to/tuya"
        )
        sys.exit(1)
    cfg = TuyaConfig.from_env()
    api = TuyaOpenAPI(cfg)
    return EnergyClient(api)


# ---------------------------------------------------------------------------
# Domoboi API helpers
# ---------------------------------------------------------------------------

def check_device_registration(api_url: str, api_token: str, domoboi_id: str) -> bool:
    """Return True if the device is registered in domoboi."""
    headers = {"Authorization": f"Token {api_token}"}
    try:
        resp = requests.get(
            f"{api_url}/nilm/api/device/",
            params={"device_id": domoboi_id},
            headers=headers,
            timeout=10,
        )
        data = resp.json()
        if data.get("status") == "ok":
            log.info("Device registered: %s — %s @ %s",
                     domoboi_id, data.get("model"), data.get("location"))
            return True
        log.warning("Device NOT registered in domoboi: %s (%s)",
                    domoboi_id, data.get("message", ""))
        return False
    except Exception as exc:
        log.error("Registration check failed for %s: %s", domoboi_id, exc)
        return False


def build_payload(reading, domoboi_device_id: str) -> dict:
    """Build a domoboi API measurement payload from a tuya_energy Reading.

    Works for both live readings (all metrics) and history readings (one DP).
    """
    ts = reading.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts_str = ts.isoformat()

    # Primary scalar: prefer power_w; fall back to the first numeric metric value
    metrics = reading.metrics or {}
    power = metrics.get("power_w")
    if power is None:
        # Pick the first numeric value available (e.g. voltage_v for a voltage-only row)
        for v in metrics.values():
            if isinstance(v, (int, float)):
                power = float(v)
                break
    power = float(power) if power is not None else 0.0

    return {
        "device_id":  domoboi_device_id,
        "start_time": ts_str,
        "end_time":   ts_str,
        "readings":   [power],
        "value":      power,
        "telemetry":  metrics,
    }


def post_measurement(api_url: str, api_token: str, payload: dict) -> bool:
    """POST one measurement to the domoboi API. Returns True on success."""
    headers = {
        "Authorization": f"Token {api_token}",
        "Content-Type":  "application/json",
    }
    try:
        resp = requests.post(
            f"{api_url}/nilm/api/measurements/",
            json=payload,
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except requests.HTTPError as exc:
        log.error("HTTP error posting measurement for %s: %s — %s",
                  payload.get("device_id"), exc, exc.response.text[:200])
    except Exception as exc:
        log.error("Failed to post measurement for %s: %s",
                  payload.get("device_id"), exc)
    return False


# ---------------------------------------------------------------------------
# Live polling
# ---------------------------------------------------------------------------

def poll_once(cfg: dict, client, registered_ids: dict) -> int:
    """Poll all registered Tuya devices once. Returns number of successful posts."""
    tuya_ids = list(registered_ids.keys())
    errors = []
    readings = client.read_many(tuya_ids, errors=errors)

    posted = 0
    for reading in readings:
        domoboi_id = registered_ids.get(reading.device_id)
        if not domoboi_id:
            log.warning("No domoboi mapping for Tuya device %s", reading.device_id)
            continue
        payload = build_payload(reading, domoboi_id)
        if post_measurement(cfg["api_url"], cfg["api_token"], payload):
            power = payload["value"]
            log.info("  %s (live): power=%.1f W", domoboi_id, power)
            posted += 1

    for tuya_id, err in errors:
        log.error("  Read error for %s: %s", tuya_id, err)

    return posted


# ---------------------------------------------------------------------------
# History backfill
# ---------------------------------------------------------------------------

def backfill_history(cfg: dict, client, registered_ids: dict, since: datetime) -> None:
    """Fetch all available DP log events since `since` and post each as a Measurement."""
    now = datetime.now(timezone.utc)
    for tuya_id, domoboi_id in registered_ids.items():
        log.info("Backfilling history for %s (%s) from %s to %s",
                 tuya_id, domoboi_id, since.date(), now.date())
        total = 0
        failed = 0

        for reading in client.history_readings(tuya_id, since, now):
            payload = build_payload(reading, domoboi_id)
            if post_measurement(cfg["api_url"], cfg["api_token"], payload):
                total += 1
                if total % 100 == 0:
                    log.info("  %s: %d rows posted so far (dp=%s t=%s)",
                             domoboi_id, total, reading.dp_code,
                             reading.timestamp.strftime("%Y-%m-%d %H:%M"))
            else:
                failed += 1

        log.info("Backfill complete for %s: %d posted, %d failed",
                 domoboi_id, total, failed)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Tuya cloud → domoboi API measurement bridge"
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Poll live readings once and exit (suitable for cron)"
    )
    parser.add_argument(
        "--history", action="store_true",
        help="Backfill all available DP log history then exit"
    )
    parser.add_argument(
        "--since", metavar="YYYY-MM-DD", default=None,
        help="Start date for --history backfill (default: 7 days ago)"
    )
    args = parser.parse_args()

    cfg = load_config()
    client = build_tuya_client()
    device_map: dict = cfg["device_map"]  # {tuya_id: domoboi_id}

    log.info("Starting Tuya daemon — %d device(s): %s",
             len(device_map), list(device_map.keys()))

    # Verify all devices are registered before proceeding
    registered_ids = {}
    for tuya_id, domoboi_id in device_map.items():
        if check_device_registration(cfg["api_url"], cfg["api_token"], domoboi_id):
            registered_ids[tuya_id] = domoboi_id
        else:
            log.warning("Skipping unregistered device %s → %s", tuya_id, domoboi_id)

    if not registered_ids:
        log.error("No registered devices found. Register them in the domoboi admin first.")
        sys.exit(1)

    # --- History mode ---
    if args.history:
        if args.since:
            since_dt = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
        else:
            since_dt = datetime.now(timezone.utc) - timedelta(days=7)
        backfill_history(cfg, client, registered_ids, since_dt)
        return

    # --- Single-poll mode ---
    if args.once:
        log.info("Single poll mode")
        n = poll_once(cfg, client, registered_ids)
        log.info("Posted %d measurement(s)", n)
        return

    # --- Continuous loop mode ---
    log.info("Continuous mode — poll interval: %ds", cfg["poll_interval"])
    while True:
        started = time.monotonic()
        log.info("Polling %d device(s)...", len(registered_ids))
        try:
            n = poll_once(cfg, client, registered_ids)
            log.info("Poll complete — %d measurement(s) posted", n)
        except Exception as exc:
            log.error("Unexpected error in poll cycle: %s", exc, exc_info=True)
        elapsed = time.monotonic() - started
        sleep_for = max(0.0, cfg["poll_interval"] - elapsed)
        log.debug("Sleeping %.1fs until next poll", sleep_for)
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
