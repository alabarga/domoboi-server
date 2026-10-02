"""
Limpia duplicados de Measurement dejados por import_csv/import_all_csv
anteriores a que escribieran `timestamp` (ver esos comandos para el porqué).

Dos pasos:
  1. Borra duplicados exactos: misma (device, start_time, end_time, value),
     conservando la fila con el id más bajo (la primera importada).
  2. Rellena `timestamp` = start_time donde siga NULL, para que la
     restricción de unicidad (device, timestamp) vuelva a funcionar y para
     que Device.is_active / Device.last_active (que leen `timestamp`)
     dejen de estar rotos para estos dispositivos.

Uso:
    python manage.py dedupe_measurements --dry-run   # solo informa
    python manage.py dedupe_measurements             # aplica los cambios
"""
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import F

from nilm.models import Measurement

CHUNK_SIZE = 5000


class Command(BaseCommand):
    help = "Elimina Measurement duplicados y rellena timestamp donde falte."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Solo informa de lo que haría, no escribe nada.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no se escribe nada"))

        # 1. Localizar duplicados exactos (misma device+start_time+end_time+value).
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY device_id, start_time, end_time, value
                        ORDER BY id
                    ) AS rn
                    FROM nilm_measurement
                ) ranked
                WHERE rn > 1
                """
            )
            dup_ids = [row[0] for row in cursor.fetchall()]

        self.stdout.write(f"{len(dup_ids)} fila(s) duplicada(s) encontradas.")

        if dup_ids and not dry_run:
            deleted = 0
            for i in range(0, len(dup_ids), CHUNK_SIZE):
                chunk = dup_ids[i:i + CHUNK_SIZE]
                n, _ = Measurement.objects.filter(pk__in=chunk).delete()
                deleted += n
            self.stdout.write(self.style.SUCCESS(f"{deleted} fila(s) duplicada(s) eliminadas."))

        # 2. Rellenar timestamp donde falte (solo lo deja NULL el bulk_create
        #    de import_csv/import_all_csv; el resto del proyecto ya lo rellena).
        missing_ts = Measurement.objects.filter(timestamp__isnull=True, start_time__isnull=False)
        count_missing = missing_ts.count()
        self.stdout.write(f"{count_missing} fila(s) con timestamp vacío (se rellenará con start_time).")

        if count_missing and not dry_run:
            updated = missing_ts.update(timestamp=F("start_time"))
            self.stdout.write(self.style.SUCCESS(f"timestamp rellenado en {updated} fila(s)."))

        self.stdout.write(self.style.SUCCESS("Listo."))
