"""Make the datetime columns timezone-aware for ``USE_TZ = True``.

Background
----------
The project previously ran with ``USE_TZ = False`` and ``TIME_ZONE='Europe/Madrid'``.
Under that setting Django sets the PostgreSQL **session** time zone to
``Europe/Madrid`` (``DatabaseWrapper.timezone_name`` returns ``settings.TIME_ZONE``
when ``USE_TZ`` is false), and DRF converts incoming tz-aware datetimes to naive
UTC before saving. The combination silently shifted API-ingested rows by one UTC
offset — see the note at the bottom.

Schema state
------------
On the current production database these columns are **already**
``timestamp with time zone``, so no conversion is required there and this
migration is a no-op. That is not guaranteed for every deployment: a database
built from migrations while ``USE_TZ`` was false would have
``timestamp without time zone`` instead.

This migration therefore *detects* the column type per column and converts only
the naive ones, which makes it safe to run against either shape and idempotent
on re-run.

Source zone when a conversion is needed
---------------------------------------
Which zone a naive value should be interpreted as depends on who wrote it:

===================  ==========================  =========================
table                written by                  naive values are in
===================  ==========================  =========================
``nilm_measurement`` the ingestion API (DRF       **UTC**
                     ``make_naive(value, utc)``)
``nilm_event``       ``timezone.now()``           **Europe/Madrid**
``nilm_comment``     ``auto_now_add``             **Europe/Madrid**
===================  ==========================  =========================

``AT TIME ZONE`` resolves DST from each row's own value, so no fixed offset is
hardcoded and the CET/CEST boundary is handled per row.

SQLite (the ``development`` alias) stores datetimes as text and has no column
types to alter, so the whole operation is skipped there.

.. note::

   This migration fixes the **schema** only. It deliberately does not touch the
   absolute instants already stored in ``nilm_measurement``, which are suspected
   to sit one UTC offset behind the true measurement times because Postgres
   interpreted DRF's naive-UTC values using the ``Europe/Madrid`` session zone.
   Correcting that is a separate, reviewable data migration — it changes the
   meaning of 61k existing rows and should not ride along with a schema change.
"""

from django.db import migrations


# table -> (columns, zone that naive values should be interpreted as)
CONVERSIONS = [
    ("nilm_measurement", ["start_time", "end_time", "timestamp"], "UTC"),
    ("nilm_event", ["start_time", "end_time"], "Europe/Madrid"),
    ("nilm_comment", ["timestamp"], "Europe/Madrid"),
]

NAIVE_TYPE = "timestamp without time zone"
AWARE_TYPE = "timestamp with time zone"


def _column_type(cursor, table, column):
    cursor.execute(
        """
        SELECT data_type FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
        """,
        [table, column],
    )
    row = cursor.fetchone()
    return row[0] if row else None


def _convert(schema_editor, to_aware):
    """Convert columns between naive and aware, skipping those already correct."""
    if schema_editor.connection.vendor != "postgresql":
        return  # SQLite: no column types to alter

    want_from = NAIVE_TYPE if to_aware else AWARE_TYPE
    target = "timestamptz" if to_aware else "timestamp"

    with schema_editor.connection.cursor() as cursor:
        for table, columns, zone in CONVERSIONS:
            pending = [
                col for col in columns
                if _column_type(cursor, table, col) == want_from
            ]
            if not pending:
                continue  # already in the desired shape
            clauses = ", ".join(
                f"ALTER COLUMN {col} TYPE {target} USING {col} AT TIME ZONE '{zone}'"
                for col in pending
            )
            cursor.execute(f"ALTER TABLE {table} {clauses};")


def apply_tz(apps, schema_editor):
    _convert(schema_editor, to_aware=True)


def revert_tz(apps, schema_editor):
    _convert(schema_editor, to_aware=False)


class Migration(migrations.Migration):

    dependencies = [
        ("nilm", "0008_alter_device_location"),
    ]

    operations = [
        migrations.RunPython(apply_tz, revert_tz),
    ]
