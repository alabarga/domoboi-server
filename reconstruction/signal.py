"""
Reconstrucción de la señal de potencia de un dispositivo DOMOBOI (edge NILM)
o TUYA (enchufe inteligente en la nube) a partir de sus Measurement.

Los dos tipos de dispositivo transmiten datos de naturaleza muy distinta
(ver nilm/models.py y scripts/tuya_daemon.py), así que se reconstruyen de
forma distinta:

  - DOMOBOI: el edge NO transmite la serie continua. Solo graba y envía una
    ventana de ~3s (30 muestras a 10Hz) alrededor de cada transición ON/OFF
    detectada (nilm_processor.py). `value` es un ΔP (delta, con signo) y
    `features.avg` es el nivel post-evento. Reconstrucción:
      · Entre eventos: potencia constante = último nivel post-evento
        conocido. No se interpola ni se inventa nada en los huecos.
      · Durante cada evento: se dibuja la forma real capturada (los
        `readings` crudos), repartida a lo largo de start_time..end_time.

  - TUYA: cada Measurement es una muestra puntual (start_time == end_time,
    sin `features`) y NO todas son de potencia. Los datapoints llegan por
    separado (ver tuya_poll.coalesce_history), así que hay tres tipos de fila:
      · con `telemetry.power_w` (+ current_a, voltage_v): potencia absoluta
        instantánea en W. Es la serie real; se unen las muestras con una
        línea recta, sin escalones ni reconstrucción que inventar.
      · solo `telemetry.energy_added_kwh` (datapoint add_ele): energía
        consumida desde el reporte anterior (incremento, ~cada 30 min), NO
        una potencia ni un contador acumulado. Tiene value=0, así que
        tratarla como potencia dibujaría caídas falsas a 0 W. Se muestra
        aparte, como barras en kWh.
      · con corriente/voltaje pero sin power_w (muy pocas): su `value` son
        amperios; no se dibujan como vatios.
    Tratar `value` como delta (como en DOMOBOI) daría una curva errónea.
"""
import calendar
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from nilm.models import Measurement


def _local_dt(naive_dt):
    """
    domoboi-server usa USE_TZ=True y TIME_ZONE='Europe/Madrid': la BD guarda
    instantes UTC y los días se cuentan en hora de Madrid. La rama naive
    (USE_TZ=False) se conserva solo por compatibilidad.
    """
    if settings.USE_TZ:
        return timezone.make_aware(naive_dt, timezone.get_current_timezone())
    return naive_dt


def _local_date(ts):
    """Fecha local (Madrid) de un DateTimeField aware; naive solo por compatibilidad."""
    if settings.USE_TZ:
        return timezone.localtime(ts).date()
    return ts.date()


def _day_bounds(day):
    start = _local_dt(datetime.combine(day, time.min))
    end = start + timedelta(days=1)
    return start, end


def _tuya_power(m):
    """
    Potencia absoluta (W) de una muestra Tuya, o None si la fila no es una
    muestra de potencia.

    - Con `telemetry.power_w`: ese valor.
    - Con telemetry pero sin power_w (solo energía, o solo corriente/voltaje):
      None. Su `value` NO son vatios (0 en las filas de energía, amperios en
      las de corriente), así que usarlo dibujaría ceros y picos falsos.
    - Sin telemetry (filas antiguas del daemon): `value` / `readings[0]`.
    """
    tel = m.telemetry if isinstance(m.telemetry, dict) else None
    if tel:
        if tel.get('power_w') is not None:
            try:
                return float(tel['power_w'])
            except (TypeError, ValueError):
                return None
        return None
    for candidate in (m.value, (m.readings or [None])[0]):
        if candidate is not None:
            try:
                return float(candidate)
            except (TypeError, ValueError):
                continue
    return None


def _tuya_power_only(queryset):
    """Restringe un queryset de Measurement TUYA a las muestras de potencia."""
    return queryset.filter(telemetry__has_key='power_w')


def build_daily_reconstruction(device, day):
    """
    Devuelve (points, events):
      - points: lista de {"t": iso8601, "p": watts} lista para Chart.js.
      - events: lista de Measurement del día, ordenados por start_time.
    """
    if device.device_type == 'TUYA':
        return _build_tuya_series(device, day)
    return _build_domoboi_reconstruction(device, day)


def _build_tuya_series(device, day):
    """
    Los Tuya no generan 'eventos' que reconstruir: cada muestra con
    `power_w` ES ya una lectura real de potencia absoluta en ese instante.
    Basta con unir las muestras consecutivas con una línea recta.

    Las filas que no son de potencia (solo energía, o solo corriente/voltaje)
    se dejan fuera tanto de la curva como de la lista de muestras; la
    energía se devuelve aparte con build_tuya_energy().
    """
    day_start, day_end = _day_bounds(day)

    candidates = Measurement.objects.filter(
        device=device, start_time__gte=day_start, start_time__lt=day_end
    ).order_by('start_time')

    samples, points = [], []
    for m in candidates:
        power = _tuya_power(m)
        if power is None or not m.start_time:
            continue
        samples.append(m)
        points.append({"t": m.start_time.isoformat(), "p": power})
    return points, samples


# Intervalos entre lecturas de energía que se dibujan como potencia media:
# por debajo de MIN el cociente kWh/tiempo es ruido (lecturas casi
# simultáneas); por encima de MAX hubo reportes perdidos y una media plana
# afirmaría más de lo que sabemos.
ENERGY_MIN_INTERVAL_S = 60
ENERGY_MAX_INTERVAL_S = 2 * 3600


def build_tuya_energy(device, day):
    """
    Energía de un dispositivo Tuya en el día.

    Devuelve (points, total_kwh, n_readings) donde cada punto es
    {"t0", "t1", "kwh", "w"}: la energía consumida entre dos lecturas
    consecutivas y la potencia media equivalente (kWh / tiempo, en W).

    Validado contra la integral de power_w (validate_tuya_energy): cada
    lectura de `energy_added_kwh` (add_ele) es la energía consumida desde la
    lectura anterior — el medidor reporta a los 30 min o al acumular 0,1 kWh,
    lo que ocurra antes — así que kWh / intervalo es la potencia media real.

    Los medidores con contador acumulado (`energy_kwh`, total_forward_energy)
    se tratan igual usando la diferencia con la lectura anterior (se descarta
    la primera y cualquier bajada, p. ej. un reinicio del contador).
    """
    day_start, day_end = _day_bounds(day)

    # Última lectura de energía anterior al día: abre el primer intervalo.
    prev_added_ts = (
        Measurement.objects.filter(
            device=device,
            start_time__lt=day_start,
            start_time__gte=day_start - timedelta(seconds=ENERGY_MAX_INTERVAL_S),
            telemetry__has_key='energy_added_kwh',
        )
        .order_by('-start_time')
        .values_list('start_time', flat=True)
        .first()
    )

    rows = (
        Measurement.objects.filter(
            device=device, start_time__gte=day_start, start_time__lt=day_end
        )
        .order_by('start_time')
        .values_list('start_time', 'telemetry')
    )

    points = []
    total_kwh = 0.0
    n_readings = 0
    prev_total, prev_total_ts = None, None

    for ts, tel in rows:
        if not isinstance(tel, dict) or ts is None:
            continue

        kwh, interval_start = None, None
        added = tel.get('energy_added_kwh')
        if added is not None:
            try:
                kwh = float(added)
            except (TypeError, ValueError):
                continue
            interval_start, prev_added_ts = prev_added_ts, ts
        elif tel.get('energy_kwh') is not None:
            try:
                total = float(tel['energy_kwh'])
            except (TypeError, ValueError):
                continue
            if prev_total is not None and total >= prev_total:
                kwh = round(total - prev_total, 6)
                interval_start = prev_total_ts
            prev_total, prev_total_ts = total, ts
        if kwh is None:
            continue

        n_readings += 1
        total_kwh += kwh

        if interval_start is None:
            continue
        seconds = (ts - interval_start).total_seconds()
        if not (ENERGY_MIN_INTERVAL_S <= seconds <= ENERGY_MAX_INTERVAL_S):
            continue
        points.append({
            "t0": max(interval_start, day_start).isoformat(),
            "t1": ts.isoformat(),
            "kwh": round(kwh, 4),
            "w": round(kwh * 3.6e6 / seconds, 1),
            "min": round(seconds / 60, 1),
        })

    return points, round(total_kwh, 3), n_readings


def _build_domoboi_reconstruction(device, day):
    day_start, day_end = _day_bounds(day)

    prev = (
        Measurement.objects.filter(device=device, start_time__lt=day_start)
        .order_by('-start_time')
        .first()
    )
    baseline = 0.0
    if prev:
        if prev.features and prev.features.get('avg') is not None:
            baseline = float(prev.features['avg'])
        elif prev.value is not None:
            baseline = float(prev.value)

    events = list(
        Measurement.objects.filter(
            device=device, start_time__gte=day_start, start_time__lt=day_end
        ).order_by('start_time')
    )

    points = [{"t": day_start.isoformat(), "p": baseline}]
    cursor = day_start

    for m in events:
        if not m.start_time or not m.end_time or m.end_time <= m.start_time:
            continue

        # tramo plano hasta el inicio de este evento
        if m.start_time > cursor:
            points.append({"t": cursor.isoformat(), "p": baseline})
            points.append({"t": m.start_time.isoformat(), "p": baseline})

        # forma real capturada durante la transición
        readings = m.readings or []
        n = len(readings)
        if n > 1:
            span = (m.end_time - m.start_time).total_seconds()
            step = span / (n - 1)
            for i, val in enumerate(readings):
                t = m.start_time + timedelta(seconds=step * i)
                try:
                    points.append({"t": t.isoformat(), "p": float(val)})
                except (TypeError, ValueError):
                    continue
        elif n == 1:
            try:
                points.append({"t": m.start_time.isoformat(), "p": float(readings[0])})
            except (TypeError, ValueError):
                pass
        else:
            # sin readings crudos (payload antiguo/incompleto): al menos
            # marcamos el salto con el nivel base conocido
            points.append({"t": m.start_time.isoformat(), "p": baseline})

        # nuevo nivel base tras el evento
        if m.features and m.features.get('avg') is not None:
            baseline = float(m.features['avg'])
        elif m.value is not None:
            baseline = baseline + float(m.value)

        cursor = m.end_time

    points.append({"t": cursor.isoformat(), "p": baseline})
    points.append({"t": day_end.isoformat(), "p": baseline})

    return points, events


def build_month_calendar(device, year, month):
    """
    Matriz de semanas (lunes-domingo) del mes dado, con el nº de eventos
    capturados por día para ese dispositivo (mapa de calor).

    Devuelve (weeks, total) donde cada celda de `weeks` es None (fuera de
    mes) o un dict {"date", "count", "level"} con level en [0, 4] para la
    intensidad del color.
    """
    start = _local_dt(datetime(year, month, 1))
    if month == 12:
        end = _local_dt(datetime(year + 1, 1, 1))
    else:
        end = _local_dt(datetime(year, month + 1, 1))

    counts = {}
    month_rows = Measurement.objects.filter(
        device=device, start_time__gte=start, start_time__lt=end
    )
    if device.device_type == 'TUYA':
        # solo cuentan las muestras de potencia, no las lecturas de energía
        month_rows = _tuya_power_only(month_rows)
    timestamps = month_rows.values_list('start_time', flat=True)

    for ts in timestamps:
        local_date = _local_date(ts)
        counts[local_date] = counts.get(local_date, 0) + 1

    max_count = max(counts.values()) if counts else 0
    cal = calendar.Calendar(firstweekday=0)  # lunes primero
    weeks = []
    for week in cal.monthdayscalendar(year, month):
        row = []
        for day_num in week:
            if day_num == 0:
                row.append(None)
                continue
            d = date(year, month, day_num)
            n = counts.get(d, 0)
            if max_count == 0 or n == 0:
                level = 0
            else:
                ratio = n / max_count
                level = 1 if ratio <= 0.25 else 2 if ratio <= 0.5 else 3 if ratio <= 0.75 else 4
            row.append({"date": d, "count": n, "level": level})
        weeks.append(row)

    return weeks, sum(counts.values())


def device_event_averages(devices):
    """
    Media de eventos capturados por día, por dispositivo, y media global.

    "Media por día" = total de eventos del dispositivo ÷ nº de días
    distintos en los que capturó al menos uno (no se cuentan días sin
    datos, para no diluir el ritmo real de captura de un domoboi que
    lleva poco tiempo instalado).

    "Media global" = promedio de esas medias diarias entre todos los
    dispositivos que tienen al menos un día con datos (cada domoboi
    pesa igual, independientemente de su volumen total).

    Devuelve (per_device, global_avg) donde per_device es
    {device_id: {"total": int, "active_days": int, "avg_per_day": float}}
    para los dispositivos SIN datos, avg_per_day es None.
    """
    rows = (
        Measurement.objects
        .filter(device__in=devices, start_time__isnull=False)
        # En Tuya solo cuentan las muestras de potencia; las lecturas de
        # energía (add_ele) llegan aparte y no son eventos.
        .exclude(Q(device__device_type='TUYA') & ~Q(telemetry__has_key='power_w'))
        .annotate(day=TruncDate('start_time'))
        .values('device_id', 'day')
        .annotate(n=Count('id'))
    )

    per_device = {d.id: {'total': 0, 'active_days': 0, 'avg_per_day': None} for d in devices}
    for row in rows:
        stats = per_device.setdefault(row['device_id'], {'total': 0, 'active_days': 0, 'avg_per_day': None})
        stats['total'] += row['n']
        stats['active_days'] += 1

    for stats in per_device.values():
        if stats['active_days']:
            stats['avg_per_day'] = stats['total'] / stats['active_days']

    daily_averages = [s['avg_per_day'] for s in per_device.values() if s['avg_per_day'] is not None]
    global_avg = sum(daily_averages) / len(daily_averages) if daily_averages else None

    return per_device, global_avg
