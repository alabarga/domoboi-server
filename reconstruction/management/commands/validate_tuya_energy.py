"""
Valida (solo lectura) las lecturas de energía de los medidores Tuya
(`energy_added_kwh`, datapoint add_ele) contra la potencia medida.

Para cada lectura de energía se calcula cuántos kWh salen de integrar
`power_w` (cada muestra se mantiene hasta la siguiente, porque el medidor
solo reporta cuando cambia) y se compara con el valor reportado. Se prueban
dos hipótesis sobre qué intervalo cubre cada lectura:

  A) desde la lectura de energía anterior hasta esta.
  B) una ventana fija de N segundos (por defecto 1800) antes de esta.

La hipótesis que dé el cociente reportado/integrado más cercano a 1 y más
estable es la correcta. También se informa de cuántas lecturas valen
exactamente 0.1 y de cuánta energía había realmente en ellas.

Uso:
    python manage.py validate_tuya_energy
    python manage.py validate_tuya_energy --device bf361a243be04b9924uont --show 15
    python manage.py validate_tuya_energy --days 3 --window 1800
"""
from bisect import bisect_right
from datetime import timedelta
from statistics import median, quantiles

from django.core.management.base import BaseCommand
from django.utils import timezone

from nilm.models import Device, Measurement

# Una muestra de potencia no se mantiene más de esto: si el hueco es mayor,
# el intervalo se descarta (no sabemos si el aparato siguió igual).
MAX_HOLD_S = 3600
# Por debajo de esto el cociente es ruido (división por casi cero).
MIN_INTEGRATED_KWH = 0.005


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / (sxx * syy) ** 0.5


def _integrate_kwh(times, powers, start, end):
    """
    kWh entre start y end con la potencia mantenida (step-hold).
    Devuelve None si el intervalo no se puede evaluar con fiabilidad.
    """
    i = bisect_right(times, start) - 1
    if i < 0:
        return None  # no hay ninguna muestra anterior al inicio
    if (start - times[i]).total_seconds() > MAX_HOLD_S:
        return None

    joules = 0.0
    cursor = start
    j = i + 1
    current = powers[i]
    while j < len(times) and times[j] < end:
        gap = (times[j] - cursor).total_seconds()
        if gap > MAX_HOLD_S:
            return None
        joules += current * gap
        cursor = times[j]
        current = powers[j]
        j += 1
    tail = (end - cursor).total_seconds()
    if tail > MAX_HOLD_S:
        return None
    joules += current * tail
    return joules / 3.6e6


def _summarise(label, pairs, out):
    """pairs: [(reportado, integrado)]. Imprime las métricas de acuerdo."""
    usable = [(r, g) for r, g in pairs if g is not None and g >= MIN_INTEGRATED_KWH]
    if len(usable) < 5:
        out.write(f"  {label}: solo {len(usable)} intervalos evaluables (insuficiente)\n")
        return
    ratios = [r / g for r, g in usable]
    q1, q2, q3 = quantiles(ratios, n=4)
    within = sum(1 for x in ratios if 0.75 <= x <= 1.25) / len(ratios)
    corr = _pearson([r for r, _ in usable], [g for _, g in usable])
    corr_txt = f"{corr:.2f}" if corr is not None else "n/d"
    out.write(
        f"  {label}: {len(usable)} intervalos | cociente rep/integrado: "
        f"mediana {q2:.2f} (Q1 {q1:.2f}, Q3 {q3:.2f}) | "
        f"dentro de ±25 %: {100 * within:.0f} % | correlación {corr_txt}\n"
    )


class Command(BaseCommand):
    help = "Compara la energía reportada por Tuya (add_ele) con la integral de power_w."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7)
        parser.add_argument("--device", help="device_id Tuya concreto (por defecto, todos).")
        parser.add_argument("--window", type=int, default=1800,
                            help="Ventana fija en segundos para la hipótesis B.")
        parser.add_argument("--show", type=int, default=0,
                            help="Muestra las primeras N lecturas con su detalle.")

    def handle(self, *args, **opts):
        since = timezone.now() - timedelta(days=opts["days"])
        devices = Device.objects.filter(device_type="TUYA").order_by("device_id")
        if opts["device"]:
            devices = devices.filter(device_id=opts["device"])

        for dev in devices:
            rows = (
                Measurement.objects.filter(device=dev, start_time__gte=since)
                .order_by("start_time")
                .values_list("start_time", "telemetry")
            )
            times, powers, energy = [], [], []
            for ts, tel in rows:
                if not isinstance(tel, dict) or ts is None:
                    continue
                if tel.get("power_w") is not None:
                    try:
                        times.append(ts)
                        powers.append(float(tel["power_w"]))
                    except (TypeError, ValueError):
                        times.pop()
                elif tel.get("energy_added_kwh") is not None:
                    try:
                        energy.append((ts, float(tel["energy_added_kwh"])))
                    except (TypeError, ValueError):
                        pass

            if len(energy) < 6 or len(times) < 6:
                continue

            self.stdout.write(self.style.MIGRATE_HEADING(
                f"\n=== {dev.device_id}  ({len(energy)} lecturas de energía, "
                f"{len(times)} muestras de potencia) ==="
            ))

            window = timedelta(seconds=opts["window"])
            pairs_a, pairs_b, detail = [], [], []
            for k, (ts, kwh) in enumerate(energy):
                int_b = _integrate_kwh(times, powers, ts - window, ts)
                pairs_b.append((kwh, int_b))
                if k == 0:
                    continue
                prev_ts = energy[k - 1][0]
                int_a = _integrate_kwh(times, powers, prev_ts, ts)
                pairs_a.append((kwh, int_a))
                detail.append((prev_ts, ts, kwh, int_a, int_b))

            _summarise("A) desde la lectura anterior ", pairs_a, self.stdout)
            _summarise(f"B) ventana fija {opts['window']}s       ", pairs_b, self.stdout)

            gaps = [(b[0] - a[0]).total_seconds() for a, b in zip(energy, energy[1:])]
            if gaps:
                g1, g2, g3 = quantiles(gaps, n=4)
                self.stdout.write(
                    f"  separación entre lecturas: mediana {g2:.0f}s "
                    f"(Q1 {g1:.0f}s, Q3 {g3:.0f}s, mín {min(gaps):.0f}s, máx {max(gaps):.0f}s)\n"
                )

            exact = [(r, g) for r, g in pairs_a if r == 0.1]
            others = [(r, g) for r, g in pairs_a if r != 0.1]
            self.stdout.write(
                f"  lecturas exactamente 0.1: {len(exact)} de {len(pairs_a)} "
                f"({100 * len(exact) / max(len(pairs_a), 1):.0f} %)\n"
            )
            for label, group in (("== 0.1", exact), ("!= 0.1", others)):
                ints = [g for _, g in group if g is not None]
                if len(ints) >= 3:
                    self.stdout.write(
                        f"    integrado en las lecturas {label}: mediana {median(ints):.3f} kWh, "
                        f"mín {min(ints):.3f}, máx {max(ints):.3f}\n"
                    )

            if opts["show"]:
                self.stdout.write("  detalle (inicio, fin, rep kWh, integrado A, integrado B):")
                for prev_ts, ts, kwh, ia, ib in detail[:opts["show"]]:
                    fa = f"{ia:.3f}" if ia is not None else "n/d"
                    fb = f"{ib:.3f}" if ib is not None else "n/d"
                    self.stdout.write(
                        f"    {prev_ts:%m-%d %H:%M:%S} -> {ts:%H:%M:%S}  rep {kwh:.3f}  A {fa}  B {fb}"
                    )

        self.stdout.write(self.style.SUCCESS("\nListo (no se ha escrito nada)."))
