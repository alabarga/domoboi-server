"""
Importa a la base local las etiquetas de eventos (EventLabel) exportadas de producción
con scripts/sync_from_production.ps1 (etiquetas_export.csv).

Los ids de las medidas no coinciden entre producción y local, así que cada etiqueta se
asocia a su medida por (device_id, start_time). Se puede relanzar: actualiza, no duplica.

Uso:
    python manage.py import_labels_csv etiquetas_export.csv
"""
import csv

from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_datetime

from nilm.models import Measurement
from reconstruction.models import EventLabel


class Command(BaseCommand):
    help = "Importa etiquetas de eventos exportadas de producción."

    def add_arguments(self, parser):
        parser.add_argument("labels_csv")

    def handle(self, *args, **options):
        created = updated = missing = invalid = 0
        valid_types = {c[0] for c in EventLabel.APPLIANCE_CHOICES}
        with open(options["labels_csv"], newline="", encoding="cp1252") as f:
            for row in csv.DictReader(f):
                start = parse_datetime(row["start_time"].replace(" ", "T", 1)) if row.get("start_time") else None
                if start is None or row["appliance_type"] not in valid_types:
                    invalid += 1
                    continue
                m = Measurement.objects.filter(
                    device__device_id=row["device_id"], timestamp=start
                ).first()
                if m is None:
                    missing += 1  # la medida no está en la base local (¿sync con -Since?)
                    continue
                _, was_created = EventLabel.objects.update_or_create(
                    measurement=m,
                    defaults={"appliance_type": row["appliance_type"], "notes": row.get("notes", "") or ""},
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        self.stdout.write(self.style.SUCCESS(
            f"Etiquetas: {created} nuevas, {updated} actualizadas, "
            f"{missing} sin medida local, {invalid} no válidas."
        ))