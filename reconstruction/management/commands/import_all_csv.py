"""
Importa TODOS los dispositivos y medidas exportados de producción
(dispositivos_export.csv + medidas_todos.csv) a la base de datos local.

Uso:
    python manage.py import_all_csv dispositivos_export.csv medidas_todos.csv
"""
import csv
import json

from django.core.management.base import BaseCommand

from nilm.models import Device, Location, Measurement


def _parse_json(raw):
    if raw is None or raw == "":
        return None
    return json.loads(raw)


class Command(BaseCommand):
    help = "Importa todos los dispositivos y medidas exportados de producción."

    def add_arguments(self, parser):
        parser.add_argument("devices_csv")
        parser.add_argument("measurements_csv")
        parser.add_argument("--batch-size", type=int, default=1000)

    def handle(self, *args, **options):
        devices = {}
        created_devices = 0
        with open(options["devices_csv"], newline="", encoding="cp1252") as f:
            for row in csv.DictReader(f):
                location = None
                if row.get("location_desc"):
                    location, _ = Location.objects.get_or_create(
                        description=row["location_desc"],
                        defaults={"address": row.get("location_address", "")},
                    )
                device, created = Device.objects.get_or_create(
                    device_id=row["device_id"],
                    defaults={
                        "model": row.get("model", ""),
                        "device_type": row.get("device_type", "DOMOBOI"),
                        "location": location,
                    },
                )
                devices[row["device_id"]] = device
                if created:
                    created_devices += 1

        self.stdout.write(f"Dispositivos: {created_devices} creados, {len(devices) - created_devices} ya existían.")

        batch = []
        total = 0
        skipped = 0
        with open(options["measurements_csv"], newline="", encoding="cp1252") as f:
            for row in csv.DictReader(f):
                device = devices.get(row["device_id"])
                if device is None:
                    skipped += 1
                    continue
                try:
                    batch.append(Measurement(
                        device=device,
                        start_time=row["start_time"] or None,
                        end_time=row["end_time"] or None,
                        # bulk_create() se salta Measurement.save(), que es
                        # donde normalmente se copia start_time -> timestamp.
                        # Sin esto, timestamp queda NULL y la restricción de
                        # unicidad (device, timestamp) nunca detecta
                        # duplicados al reimportar (NULL != NULL en SQL).
                        timestamp=row["start_time"] or None,
                        value=row["value"],
                        readings=_parse_json(row["readings"]) or [],
                        features=_parse_json(row["features"]),
                        telemetry=_parse_json(row["telemetry"]),
                    ))
                except Exception:
                    skipped += 1
                    continue

                if len(batch) >= options["batch_size"]:
                    Measurement.objects.bulk_create(batch, ignore_conflicts=True)
                    total += len(batch)
                    self.stdout.write(f"  ... {total} importadas")
                    batch = []

        if batch:
            Measurement.objects.bulk_create(batch, ignore_conflicts=True)
            total += len(batch)

        self.stdout.write(self.style.SUCCESS(
            f"Importación terminada: {total} medidas procesadas, {skipped} filas ignoradas."
        ))