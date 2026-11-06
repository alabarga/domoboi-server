"""
Detección de eventos sobre la serie de potencia de un medidor Tuya.

El medidor reporta `power_w` solo cuando cambia (~cada 2 min), así que la
serie se trata como escalonada (step-hold): cada muestra vale hasta la
siguiente. Un "evento" es un tramo continuo por encima de la carga base
(always-on), no una Measurement suelta.

Funciones puras sobre listas de (ts, W) para poder probarlas sin BD; solo
load_* tocan el ORM.
"""
from datetime import timedelta
from django.utils import timezone

from nilm.models import Measurement

# Más allá de esto no se mantiene una muestra: hubo un hueco de datos.
MAX_HOLD_S = 3600


def load_series(device, since, until):
    """[(ts, W)] de las filas con power_w del dispositivo, ordenadas."""
    rows = (Measurement.objects
            .filter(device=device, timestamp__gte=since, timestamp__lt=until,
                    telemetry__has_key='power_w')
            .order_by('timestamp')
            .values_list('timestamp', 'telemetry'))
    out = []
    for ts, tel in rows:
        try:
            out.append((ts, float(tel['power_w'])))
        except (TypeError, ValueError):
            continue
    return out


def load_energy(device, since, until):
    """
    [(ts, kWh)] de las lecturas add_ele (energía desde la lectura anterior).

    Solo filas de histórico (add_ele sin power_w): el sondeo en vivo
    (tuya_poll --once) guarda una instantánea de todos los datapoints y
    repite el último add_ele en cada sondeo, lo que duplicaría la energía.
    """
    rows = (Measurement.objects
            .filter(device=device, timestamp__gte=since, timestamp__lt=until,
                    telemetry__has_key='energy_added_kwh')
            .exclude(telemetry__has_key='power_w')
            .order_by('timestamp')
            .values_list('timestamp', 'telemetry'))
    out = []
    for ts, tel in rows:
        try:
            out.append((ts, float(tel['energy_added_kwh'])))
        except (TypeError, ValueError):
            continue
    return out


def remove_spikes(series, min_delta_w, max_spike_s=180):
    """
    Quita picos aislados: una sola muestra que sube (o baja) ≥ min_delta_w
    respecto a las dos vecinas, dura < max_spike_s, y las vecinas están a
    menos de min_delta_w entre sí. Devuelve (serie_limpia, n_picos).
    """
    if len(series) < 3:
        return list(series), 0
    out, n = [series[0]], 0
    for i in range(1, len(series) - 1):
        (_, p0), (t1, p1), (t2, p2) = out[-1], series[i], series[i + 1]
        up, down = p1 - p0, p1 - p2
        if (abs(up) >= min_delta_w and abs(down) >= min_delta_w and (up > 0) == (down > 0)
                and abs(p2 - p0) < min_delta_w and (t2 - t1).total_seconds() < max_spike_s):
            n += 1
            continue
        out.append(series[i])
    out.append(series[-1])
    return out, n


def steps(series, min_delta_w):
    """[(ts, ΔW)] de los saltos ≥ min_delta_w entre muestras consecutivas."""
    return [(t1, p1 - p0) for (t0, p0), (t1, p1) in zip(series, series[1:])
            if abs(p1 - p0) >= min_delta_w and (t1 - t0).total_seconds() <= MAX_HOLD_S]


def segment(series, base_w, min_delta_w, merge_gap_s=240, min_duration_s=0):
    """
    Tramos con P - base ≥ min_delta_w. Dos tramos separados por menos de
    merge_gap_s se unen (un aparato que baja un momento sigue siendo el
    mismo uso). Cada tramo incluye rasgos para clasificar:
      start, end, duration_min, peak_w, plateau_w (mediana ponderada por
      tiempo, sobre la base), energy_wh (sobre la base), n_samples,
      n_steps (saltos internos ≥ min_delta_w), on_delta_w, off_delta_w.
    base_w: número o función ts -> W (base por día).
    """
    base = base_w if callable(base_w) else (lambda ts: base_w)
    raw, cur = [], None
    for i, (ts, p) in enumerate(series):
        nxt = series[i + 1][0] if i + 1 < len(series) else None
        hold = min((nxt - ts).total_seconds(), MAX_HOLD_S) if nxt else 0
        above = p - base(ts) >= min_delta_w
        if above:
            if cur is None:
                prev_p = series[i - 1][1] if i else p
                cur = {'start': ts, 'idx': [], 'on_delta_w': p - prev_p}
            cur['idx'].append((i, hold))
            if nxt and (nxt - ts).total_seconds() > MAX_HOLD_S:  # hueco: cierra
                cur['end'] = ts + timedelta(seconds=hold)
                cur['off_delta_w'] = None
                raw.append(cur); cur = None
        elif cur is not None:
            cur['end'] = ts
            cur['off_delta_w'] = p - series[cur['idx'][-1][0]][1]
            raw.append(cur); cur = None
    if cur is not None:
        last_i, hold = cur['idx'][-1]
        cur['end'] = series[last_i][0] + timedelta(seconds=hold)
        cur['off_delta_w'] = None
        raw.append(cur)

    merged = []
    for s in raw:
        if merged and (s['start'] - merged[-1]['end']).total_seconds() < merge_gap_s:
            m = merged[-1]
            m['idx'] += s['idx']; m['end'] = s['end']; m['off_delta_w'] = s['off_delta_w']
            m['merged'] = m.get('merged', 0) + 1
        else:
            merged.append(s)

    out = []
    for s in merged:
        dur = (s['end'] - s['start']).total_seconds()
        if dur < min_duration_s:
            continue
        vals = [(series[i][1] - base(series[i][0]), h) for i, h in s['idx']]
        weighted = sorted(vals)
        total_h = sum(h for _, h in vals) or 1
        acc, plateau = 0, weighted[-1][0]
        for v, h in weighted:
            acc += h
            if acc >= total_h / 2:
                plateau = v; break
        idxs = [i for i, _ in s['idx']]
        inner = [series[j][1] - series[j - 1][1] for j in idxs[1:]]
        out.append({
            'start': s['start'], 'end': s['end'],
            'duration_min': round(dur / 60, 1),
            'peak_w': round(max(v for v, _ in vals)),
            'plateau_w': round(plateau),
            'energy_wh': round(sum(v * h for v, h in vals) / 3600, 1),
            'n_samples': len(idxs),
            'n_steps': sum(1 for d in inner if abs(d) >= min_delta_w),
            'merged': s.get('merged', 0),
            'on_delta_w': round(s['on_delta_w']),
            'off_delta_w': None if s['off_delta_w'] is None else round(s['off_delta_w']),
        })
    return out


def daily_baseline(series, hours=(2, 5), pct=0.10):
    """{fecha_local: W} = percentil pct de la potencia entre hours[0] y hours[1] (hora local)."""
    by_day = {}
    for ts, p in series:
        lt = timezone.localtime(ts) if timezone.is_aware(ts) else ts
        if hours[0] <= lt.hour < hours[1]:
            by_day.setdefault(lt.date(), []).append(p)
    return {d: sorted(v)[int(len(v) * pct)] for d, v in by_day.items() if len(v) >= 5}


def baseline_fn(daily, fallback):
    """ts -> base del día (o la mediana global si ese día no tiene noche medida)."""
    def f(ts):
        lt = timezone.localtime(ts) if timezone.is_aware(ts) else ts
        return daily.get(lt.date(), fallback)
    return f


def integrate_kwh(series, start, end):
    """kWh step-hold de la serie entre start y end (se ignoran huecos > MAX_HOLD_S)."""
    total = 0.0
    for (t0, p0), (t1, _) in zip(series, series[1:]):
        if (t1 - t0).total_seconds() > MAX_HOLD_S:
            continue
        a, b = max(t0, start), min(t1, end)
        if b > a:
            total += p0 * (b - a).total_seconds()
    return total / 3.6e6


def pct(values, q):
    """Percentil q (0..1) por rango más cercano; None si no hay datos."""
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]

