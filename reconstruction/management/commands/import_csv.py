"""
Importa un CSV exportado con \\copy desde nilm_measurement (producción)
a la base de datos local, asociado a un Device real (creándolo si no existe).

Uso:
    python manage.py import_csv medidas_domoboi07.csv --device-id domoboi-07 --model "DOMOBOI 1.0" --device-type DOMOBOI --location-desc "Carrer Poble Nou, 22 Gandesa" --location-address "Carrer Poble Nou, 22 Gandesa"
"""
import csv
import json

from django.core.management.base import BaseCommand, CommandError

from nilm.models import Device, Location, Measurement


def _parse_json(raw):
    if raw is None or raw == "":
        return None
    return json.loads(raw)


class Command(BaseCommand):
    help = "Importa un CSV de nilm_measurement (exportado con \\copy) a la base local."

    def add_arguments(self, parser):
        parser.add_argument("csv_path")
        parser.add_argument("--device-id", required=True)
        parser.add_argument("--model", default="")
        parser.add_argument("--device-type", default="DOMOBOI")
        parser.add_argument("--location-desc", default="")
        parser.add_argument("--location-address", default="")
        parser.add_argument("--batch-size", type=int, default=1000)

    def handle(self, *args, **options):
        location = None
        if options["location_desc"]:
            location, _ = Location.objects.get_or_create(
                description=options["location_desc"],
                defaults={"address": options["location_address"]},
            )

        device, created = Device.objects.get_or_create(
            device_id=options["device_id"],
            defaults={
                "model": options["model"],
                "device_type": options["device_type"],
                "location": location,
            },
        )
        self.stdout.write(f"Device {'creado' if created else 'ya existía'}: {device.device_id}")

        batch = []
        total = 0
        skipped = 0
        with open(options["csv_path"], newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
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
                except Exception as e:
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
            f"Importación terminada: {total} filas procesadas, {skipped} filas con error ignoradas."
        ))