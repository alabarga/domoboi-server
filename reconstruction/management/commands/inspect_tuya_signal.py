"""
Diagnóstico (solo lectura) de cómo están guardadas las Measurement de los
dispositivos TUYA, para decidir cómo reconstruir su señal.

Para cada dispositivo TUYA con datos en la ventana indicada informa de:
  - qué métricas trae cada fila (power_w, current_a, energy_added_kwh...),
  - cuántas filas NO traen power_w (y qué `value` tienen),
  - la separación típica entre muestras de potencia,
  - cómo se comporta energy_added_kwh (¿contador acumulado o incremento?).

Uso:
    python manage.py inspect_tuya_signal
    python manage.py inspect_tuya_signal --days 3
    python manage.py inspect_tuya_signal --device bf2d4eb75c9e769371jftv
"""
from collections import Counter
from datetime import timedelta
from statistics import median

from django.core.management.base import BaseCommand
from django.utils import timezone

from nilm.models import Device, Measurement


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class Command(BaseCommand):
    help = "Diagnostica la forma de las Measurement de los dispositivos TUYA."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7)
        parser.add_argument("--device", help="device_id Tuya concreto (por defecto, todos).")

    def handle(self, *args, **opts):
        since = timezone.now() - timedelta(days=opts["days"])
        devices = Device.objects.filter(device_type="TUYA").order_by("device_id")
        if opts["device"]:
            devices = devices.filter(device_id=opts["device"])

        for dev in devices:
            rows = list(
                Measurement.objects.filter(device=dev, start_time__gte=since)
                .order_by("start_time")
                .values_list("start_time", "value", "telemetry")
            )
            if not rows:
                continue

            self.stdout.write(self.style.MIGRATE_HEADING(
                f"\n=== {dev.device_id}  ({len(rows)} filas, últimos {opts['days']} días) ==="
            ))

            kinds = Counter()
            no_power_values = Counter()
            power_ts = []
            energy = []          # (ts, energy_added_kwh)
            energy_total = []    # (ts, energy_kwh)
            samples = {}

            for ts, value, tel in rows:
                tel = tel if isinstance(tel, dict) else {}
                has_p = tel.get("power_w") is not None
                has_i = tel.get("current_a") is not None
                has_v = tel.get("voltage_v") is not None
                has_e = tel.get("energy_added_kwh") is not None
                has_t = tel.get("energy_kwh") is not None

                if has_p:
                    kind = "con power_w"
                    power_ts.append(ts)
                elif has_i or has_v:
                    kind = "SIN power_w (trae corriente/voltaje)"
                elif has_e or has_t:
                    kind = "SOLO energía (sin potencia/corriente)"
                else:
                    kind = "vacía / otra"

                kinds[kind] += 1
                if not has_p:
                    no_power_values[_num(value)] += 1
                if has_e:
                    energy.append((ts, _num(tel["energy_added_kwh"])))
                if has_t:
                    energy_total.append((ts, _num(tel["energy_kwh"])))
                samples.setdefault(kind, (ts, value, {k: v for k, v in tel.items() if k != "raw"},
                                          sorted((tel.get("raw") or {}).keys())))

            self.stdout.write("Tipos de fila:")
            for kind, n in kinds.most_common():
                self.stdout.write(f"  {n:6d}  {kind}  ({100 * n / len(rows):.1f}%)")

            if no_power_values:
                top = ", ".join(f"{k}: {v}" for k, v in no_power_values.most_common(5))
                self.stdout.write(f"`value` en filas sin power_w (valor: nº): {top}")

            if len(power_ts) > 1:
                gaps = [(b - a).total_seconds() for a, b in zip(power_ts, power_ts[1:])]
                self.stdout.write(
                    f"Separación entre muestras de potencia: mediana {median(gaps):.0f}s, "
                    f"mín {min(gaps):.0f}s, máx {max(gaps):.0f}s"
                )

            for label, series in (("energy_added_kwh (add_ele)", energy),
                                  ("energy_kwh (total_forward_energy)", energy_total)):
                vals = [v for _, v in series if v is not None]
                if not vals:
                    continue
                nondec = sum(1 for a, b in zip(vals, vals[1:]) if b >= a)
                pairs = max(len(vals) - 1, 1)
                self.stdout.write(
                    f"{label}: {len(vals)} lecturas, mín {min(vals)}, máx {max(vals)}, "
                    f"distintos {len(set(vals))}, no-decrecientes {100 * nondec / pairs:.0f}%"
                )
                self.stdout.write("  primeras: " + ", ".join(f"{v:g}" for v in vals[:12]))
                if len(series) > 1:
                    gaps = [(b[0] - a[0]).total_seconds() for a, b in zip(series, series[1:])]
                    self.stdout.write(f"  separación entre lecturas: mediana {median(gaps):.0f}s")

            self.stdout.write("Ejemplo por tipo (ts, value, telemetry sin 'raw', códigos en raw):")
            for kind, (ts, value, tel, raw_keys) in samples.items():
                self.stdout.write(f"  [{kind}] {ts} value={value} tel={tel} raw={raw_keys}")

        self.stdout.write(self.style.SUCCESS("\nListo (no se ha escrito nada)."))
