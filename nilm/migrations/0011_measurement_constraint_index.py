"""Add the (device, timestamp) unique constraint and its supporting index.

Why the constraint
------------------
Ingestion had no natural key, so re-running a backfill silently duplicated every
row. With this in place `bulk_create(..., ignore_conflicts=True)` makes repeated
history imports free no-ops, which is what lets the backfill be retried safely.

Note this only works because history readings are *coalesced by timestamp* before
insert. Tuya returns report logs in long format — one row per datapoint code, with
several codes commonly sharing an identical timestamp — so inserting them raw
would collide on two out of every three rows.

Why the index
-------------
`Measurement.Meta.ordering = ['-timestamp']` forced a sort on every query, and
`Device.is_active` / `Device.last_active` both filter or sort by device and
timestamp, with no index on either. The composite `(device, -timestamp)` serves
both exactly. At ~61k rows growing by ~11.5k/day this stops being optional.

Deduplication
-------------
Production contains 64 duplicate `(device, timestamp)` groups (65 surplus rows),
so `AddConstraint` would fail outright without a cleanup step first. We keep the
lowest `id` in each group — the earliest row actually written — and delete the
rest. The surviving row is retained in full; no data is merged or altered.

This runs before `AddConstraint` in the same migration, so the two are applied
atomically: either the table ends up deduplicated *and* constrained, or the
transaction rolls back and nothing changes.
"""

from django.db import migrations, models


# Rows with a NULL device or timestamp are excluded on both sides of the DELETE.
# A unique constraint treats NULLs as distinct, so such rows never collide and
# must not be removed — whereas GROUP BY would lump them all into one group and
# delete every one but the first.
DEDUPE_SQL = """
    DELETE FROM nilm_measurement
    WHERE device_id IS NOT NULL
      AND timestamp IS NOT NULL
      AND id NOT IN (
        SELECT MIN(id)
        FROM nilm_measurement
        WHERE device_id IS NOT NULL
          AND timestamp IS NOT NULL
        GROUP BY device_id, timestamp
    );
"""


def dedupe(apps, schema_editor):
    """Remove surplus rows sharing a (device, timestamp) pair, keeping the oldest."""
    Measurement = apps.get_model("nilm", "Measurement")
    before = Measurement.objects.count()
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(DEDUPE_SQL)
    removed = before - Measurement.objects.count()
    if removed:
        print(f"\n    deduplicated {removed} surplus measurement row(s)")


def noop_reverse(apps, schema_editor):
    """Deleted duplicates cannot be reconstructed; reversing is a no-op."""


class Migration(migrations.Migration):

    dependencies = [
        ('nilm', '0010_shift_measurement_instants'),
    ]

    operations = [
        migrations.RunPython(dedupe, noop_reverse),
        migrations.AddIndex(
            model_name='measurement',
            index=models.Index(fields=['device', '-timestamp'], name='meas_device_ts_idx'),
        ),
        migrations.AddConstraint(
            model_name='measurement',
            constraint=models.UniqueConstraint(fields=('device', 'timestamp'), name='uniq_measurement_device_timestamp'),
        ),
    ]
