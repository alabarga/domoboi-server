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

  - TUYA: el daemon (tuya_daemon.py) hace polling periódico (cada
    TUYA_POLL_INTERVAL segundos, 60s por defecto) y cada Measurement es una
    muestra absoluta e instantánea (start_time == end_time, `value` = W en
    ese instante, sin `features`). Aquí NO hay eventos que reconstruir: ya
    es la serie real, solo hay que unir las muestras consecutivas con una
    línea recta. Tratar `value` como delta (como en DOMOBOI) daría una
    curva completamente errónea.
"""
import calendar
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone

from nilm.models import Measurement


def _local_dt(naive_dt):
    """
    domoboi-server usa USE_TZ=False (DateTimeField naive, en hora local).
    Este helper construye el datetime "de comparación" correcto tanto si
    el proyecto tiene USE_TZ=True (aware) como False (naive), para que la
    app siga funcionando si algún día se activa USE_TZ.
    """
    if settings.USE_TZ:
        return timezone.make_aware(naive_dt, timezone.get_current_timezone())
    return naive_dt


def _local_date(ts):
    """Fecha local de un DateTimeField, sea aware o naive."""
    if settings.USE_TZ:
        return timezone.localtime(ts).date()
    return ts.date()


def _day_bounds(day):
    start = _local_dt(datetime.combine(day, time.min))
    end = start + timedelta(days=1)
    return start, end


def _tuya_power(m):
    """Potencia absoluta (W) de una muestra Tuya: prefiere telemetry.power_w,
    y cae a value/readings[0] si faltara (algún backfill antiguo)."""
    if m.telemetry and m.telemetry.get('power_w') is not None:
        try:
            return float(m.telemetry['power_w'])
        except (TypeError, ValueError):
            pass
    if m.value is not None:
        try:
            return float(m.value)
        except (TypeError, ValueError):
            pass
    if m.readings:
        try:
            return float(m.readings[0])
        except (TypeError, ValueError):
            pass
    return 0.0


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
    Los Tuya no generan 'eventos' que reconstruir: cada Measurement ES ya
    una muestra real de potencia absoluta en ese instante (polling cada
    ~60s). Basta con unir las muestras consecutivas con una línea recta.
    """
    day_start, day_end = _day_bounds(day)

    samples = list(
        Measurement.objects.filter(
            device=device, start_time__gte=day_start, start_time__lt=day_end
        ).order_by('start_time')
    )

    points = [{"t": m.start_time.isoformat(), "p": _tuya_power(m)} for m in samples if m.start_time]
    return points, samples


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
    timestamps = Measurement.objects.filter(
        device=device, start_time__gte=start, start_time__lt=end
    ).values_list('start_time', flat=True)

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
