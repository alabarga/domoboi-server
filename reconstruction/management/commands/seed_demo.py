"""
Rellena la base de datos local con un dispositivo DOMOBOI y uno TUYA de
ejemplo, con eventos/muestras de hoy, para poder ver la app de
reconstrucción funcionando sin tener que esperar a los datos reales del
piloto ni rellenar formularios en el admin a mano.

Uso:
    python manage.py seed_demo
    python manage.py seed_demo --clear   # borra y vuelve a crear

NO usar contra la base de datos de producción — está pensado solo para
una base de datos local de pruebas (SQLite).
"""
import random
from datetime import datetime, timedelta

from django.conf import settings as dj_settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from nilm.models import Device, Location, Measurement


def _today():
    now = timezone.now()
    return timezone.localtime(now).date() if dj_settings.USE_TZ else now.date()


def _day_start(day):
    dt = datetime.combine(day, datetime.min.time())
    if dj_settings.USE_TZ:
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


class Command(BaseCommand):
    help = "Crea datos de ejemplo (device_id: demo-domoboi-01, demo-tuya-nevera-01) para probar la app de reconstrucción."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear", action="store_true",
            help="Borra las medidas de ejemplo de hoy antes de volver a crearlas.",
        )

    def handle(self, *args, **options):
        random.seed(7)
        today = _today()
        day_start = _day_start(today)

        loc, _ = Location.objects.get_or_create(
            description="Vivienda de ejemplo",
            defaults={"address": "Calle de prueba, 1"},
        )

        domoboi, _ = Device.objects.get_or_create(
            device_id="demo-domoboi-01",
            defaults={"model": "IPEM PiHat Lite (demo)", "location": loc, "device_type": "DOMOBOI"},
        )
        tuya, _ = Device.objects.get_or_create(
            device_id="demo-tuya-nevera-01",
            defaults={"model": "Enchufe Tuya - Nevera (demo)", "location": loc, "device_type": "TUYA"},
        )

        if options["clear"]:
            Measurement.objects.filter(
                device__in=[domoboi, tuya], start_time__gte=day_start, start_time__lt=day_start + timedelta(days=1)
            ).delete()

        self._seed_domoboi(domoboi, day_start)
        self._seed_tuya(tuya, day_start)

        self.stdout.write(self.style.SUCCESS(
            f"Datos de ejemplo listos para {today}:\n"
            f"  - {domoboi.device_id}  ({domoboi.get_device_type_display()})\n"
            f"  - {tuya.device_id}  ({tuya.get_device_type_display()})\n\n"
            f"Ábrelos en /reconstruction/{domoboi.device_id}/ y /reconstruction/{tuya.device_id}/"
        ))

    def _seed_domoboi(self, device, day_start):
        event_offsets_deltas = [
            (7 * 3600 + 15 * 60, 900.0),
            (7 * 3600 + 19 * 60, -900.0),
            (12 * 3600 + 30 * 60, 1800.0),
            (13 * 3600 + 15 * 60, -1800.0),
            (21 * 3600 + 5 * 60, 90.0),
        ]
        baseline = 110.0
        for offset, delta in event_offsets_deltas:
            start = day_start + timedelta(seconds=offset)
            end = start + timedelta(seconds=3)
            n = 30
            pre, post = baseline, baseline + delta
            readings = []
            for i in range(n):
                frac = i / (n - 1)
                val = pre + (post - pre) * min(1.0, frac * 2.2) + random.uniform(-3, 3)
                readings.append(round(val, 2))
            baseline = post
            Measurement.objects.create(
                device=device, start_time=start, end_time=end,
                readings=readings, value=delta,
                features={
                    "avg": post,
                    "min": min(readings),
                    "max": max(readings),
                    "std": 5.2,
                },
            )

    def _seed_tuya(self, device, day_start):
        for i in range(24 * 60):
            ts = day_start + timedelta(minutes=i)
            power = 130.0 if (i % 45) < 15 else 3.0
            Measurement.objects.create(
                device=device, start_time=ts, end_time=ts,
                readings=[power], value=power,
                telemetry={
                    "power_w": power,
                    "voltage_v": 230.1,
                    "current_a": round(power / 230.1, 3),
                    "energy_kwh": round(i * 0.001, 3),
                },
                features=None,
            )