"""Correct the absolute instants of API-ingested measurements.

The defect
----------
While the project ran with ``USE_TZ = False`` and ``TIME_ZONE = 'Europe/Madrid'``,
every measurement written through the ingestion API was stored one UTC offset
behind the instant it actually represents:

1. ``domoboi-edge`` sends a tz-aware UTC instant ``T``
   (``client.py`` builds it with ``datetime.now(timezone.utc)``), e.g.
   ``2026-08-31T16:18:49+00:00``.
2. DRF's ``DateTimeField`` under ``USE_TZ = False`` applies
   ``timezone.make_naive(value, utc)``, producing the naive value ``16:18:49``
   — correct digits, but the UTC marker is gone.
3. Django writes that naive value to a ``timestamptz`` column with the
   PostgreSQL **session** time zone set to ``Europe/Madrid``
   (``DatabaseWrapper.timezone_name`` returns ``settings.TIME_ZONE`` whenever
   ``USE_TZ`` is false).
4. PostgreSQL therefore reads ``16:18:49`` as *Madrid local* and stores the
   absolute instant ``14:18:49Z`` — two hours (CEST) earlier than ``T``.

Reading the row back under the old settings rendered it as ``16:18`` naive
Madrid again, so the round trip looked self-consistent and the drift went
unnoticed. It became visible only because ``Device.is_active`` compares against
``timezone.now()``, which used a different convention, and so never fired.

The correction
--------------
Re-interpret each stored instant the way it was originally meant::

    (timestamp AT TIME ZONE 'Europe/Madrid')   -- render back to the naive digits
                          AT TIME ZONE 'UTC'   -- read those digits as UTC

``AT TIME ZONE`` resolves the offset from each row's own value, so CET rows shift
by 1 h and CEST rows by 2 h without any hardcoded constant.

Scope
-----
``nilm_measurement`` **only**. ``nilm_event`` and ``nilm_comment`` were written by
Django itself via ``timezone.now()`` / ``auto_now_add``, which produced naive
*Madrid* values that PostgreSQL then interpreted as Madrid — already correct.
Shifting them would introduce the very error this migration removes.

Ordering
--------
Must run before any post-``USE_TZ`` data is ingested. Everything present when
this was written (61,645 rows, ``2026-07-16`` → ``2026-08-31``) predates the
cutover, and `tuya_poll` has never run. Applied out of order it would also shift
correctly-stored new rows.

Reversibility
-------------
``reverse_sql`` is the exact inverse, so the shift can be undone if the premise
turns out to be wrong. Note this is safe *only* until new rows are written; after
that, reversing would drag correct rows backwards too, and a dump restore is the
right recovery.
"""

from django.db import migrations


SHIFT_FORWARD = """
    UPDATE nilm_measurement SET
        start_time = (start_time AT TIME ZONE 'Europe/Madrid') AT TIME ZONE 'UTC',
        end_time   = (end_time   AT TIME ZONE 'Europe/Madrid') AT TIME ZONE 'UTC',
        timestamp  = (timestamp  AT TIME ZONE 'Europe/Madrid') AT TIME ZONE 'UTC';
"""

SHIFT_BACK = """
    UPDATE nilm_measurement SET
        start_time = (start_time AT TIME ZONE 'UTC') AT TIME ZONE 'Europe/Madrid',
        end_time   = (end_time   AT TIME ZONE 'UTC') AT TIME ZONE 'Europe/Madrid',
        timestamp  = (timestamp  AT TIME ZONE 'UTC') AT TIME ZONE 'Europe/Madrid';
"""


def shift(apps, schema_editor, sql):
    # SQLite (the `development` alias) holds datetimes as text and has no
    # AT TIME ZONE operator; it also carries no measurement rows.
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(sql)


def apply_shift(apps, schema_editor):
    shift(apps, schema_editor, SHIFT_FORWARD)


def revert_shift(apps, schema_editor):
    shift(apps, schema_editor, SHIFT_BACK)


class Migration(migrations.Migration):

    dependencies = [
        ("nilm", "0009_use_tz"),
    ]

    operations = [
        migrations.RunPython(apply_shift, revert_shift),
    ]
