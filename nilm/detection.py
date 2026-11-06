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


def load_va(device, since, until):
    """{ts: VA} (S = V·I) de las filas que traen power_w, current_a y voltage_v."""
    rows = (Measurement.objects
            .filter(device=device, timestamp__gte=since, timestamp__lt=until,
                    telemetry__has_key='power_w')
            .filter(telemetry__has_key='current_a')
            .filter(telemetry__has_key='voltage_v')
            .values_list('timestamp', 'telemetry'))
    out = {}
    for ts, tel in rows:
        try:
            out[ts] = float(tel['voltage_v']) * float(tel['current_a'])
        except (TypeError, ValueError):
            continue
    return out


# Por debajo de este aumento de potencia el cociente ΔP/ΔS es ruido.
PF_MIN_DELTA_W = 100


def event_pf(series, va, prev_i, idxs):
    """
    Factor de potencia del aparato que entra (no de toda la casa):
    mediana de (P − P_antes) / (S − S_antes) en las muestras del evento, con
    "antes" = la muestra previa al inicio. Restar S es una aproximación
    (las potencias aparentes no se suman en rigor), suficiente para separar
    resistivo (≈ 1) de motor/electrónica (0,5–0,9). None si faltan V·I o el
    aumento es < PF_MIN_DELTA_W.
    """
    if not va or prev_i is None or series[prev_i][0] not in va:
        return None
    p0, s0 = series[prev_i][1], va[series[prev_i][0]]
    ratios = []
    for i in idxs:
        ts, p = series[i]
        if ts in va and p - p0 >= PF_MIN_DELTA_W and va[ts] - s0 > 0:
            r = (p - p0) / (va[ts] - s0)
            if 0 < r <= 1.3:  # fuera de esto, V/I y P son de instantes distintos
                ratios.append(r)
    if not ratios:
        return None
    ratios.sort()
    return round(ratios[len(ratios) // 2], 2)


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


def segment(series, base_w, min_delta_w, merge_gap_s=240, min_duration_s=0, va=None):
    """
    Tramos con P - base ≥ min_delta_w. Dos tramos separados por menos de
    merge_gap_s se unen (un aparato que baja un momento sigue siendo el
    mismo uso). Cada tramo incluye rasgos para clasificar:
      start, end, duration_min, peak_w, plateau_w (mediana ponderada por
      tiempo, sobre la base), energy_wh (sobre la base), n_samples,
      n_steps (saltos internos ≥ min_delta_w), on_delta_w, off_delta_w,
      pf (factor de potencia del aparato, ver event_pf; None sin va).
    base_w: número o función ts -> W (base por día).
    va: {ts: VA} de load_va(), opcional.
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
                cur = {'start': ts, 'idx': [], 'on_delta_w': p - prev_p, 'prev_i': i - 1 if i else None}
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
            'pf': event_pf(series, va, s['prev_i'], idxs),
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



def _step_pf(series, va, i):
    """FP del salto entre las muestras i-1 e i: ΔP / ΔS (None si falta V·I o ΔP < PF_MIN_DELTA_W)."""
    if not va or i < 1:
        return None
    (t0, p0), (t1, p1) = series[i - 1], series[i]
    if t0 not in va or t1 not in va or abs(p1 - p0) < PF_MIN_DELTA_W:
        return None
    ds = va[t1] - va[t0]
    if ds == 0 or (ds > 0) != (p1 > p0):
        return None
    r = (p1 - p0) / ds
    return round(r, 2) if 0 < r <= 1.3 else None


def pair_steps(series, min_delta_w, tol=0.3, max_on_s=4 * 3600, va=None):
    """
    Descompone la serie en usos de un solo aparato: empareja cada salto de
    subida (ON) con el primer salto de bajada (OFF) posterior de magnitud
    parecida (|ΔON − ΔOFF| ≤ tol · máx). Entre varios ON abiertos que encajan
    se elige el más reciente.
    Un ON sin pareja tras max_on_s se descarta.

    Devuelve (pares, n_on_sin_pareja, n_off_sin_pareja). Cada par:
      start, end, duration_min, delta_w (media de |ON| y |OFF|), on_w, off_w,
      energy_wh (delta · duración), pf (del salto ON, o del OFF si falta).
    """
    # ponytail: un OFF que apaga dos aparatos a la vez (misma muestra) no se
    # reparte entre dos ON; quedan sin pareja. Resolver como subset-sum si pesa.
    open_on, pairs, lost_on, lost_off = [], [], 0, 0
    for i in range(1, len(series)):
        (t0, p0), (t1, p1) = series[i - 1], series[i]
        if (t1 - t0).total_seconds() > MAX_HOLD_S:
            lost_on += len(open_on); open_on = []  # hueco: no sabemos qué pasó
            continue
        d = p1 - p0
        if abs(d) < min_delta_w:
            continue
        expired = [s for s in open_on if (t1 - s['ts']).total_seconds() > max_on_s]
        lost_on += len(expired)
        open_on = [s for s in open_on if s not in expired]
        if d > 0:
            open_on.append({'ts': t1, 'w': d, 'i': i})
            continue
        off = -d
        # El ON compatible más reciente: con un termostato (estufa, horno) hay
        # varios ON iguales abiertos y elegir uno antiguo crea pares falsos largos.
        k = next((k for k in range(len(open_on) - 1, -1, -1)
                  if abs(open_on[k]['w'] - off) / max(open_on[k]['w'], off) <= tol), None)
        if k is None:
            lost_off += 1
            continue
        on = open_on.pop(k)
        dur = (t1 - on['ts']).total_seconds()
        delta = (on['w'] + off) / 2
        pf = _step_pf(series, va, on['i'])
        pairs.append({
            'start': on['ts'], 'end': t1,
            'duration_min': round(dur / 60, 1),
            'delta_w': round(delta), 'on_w': round(on['w']), 'off_w': round(off),
            'energy_wh': round(delta * dur / 3600, 1),
            'pf': pf if pf is not None else _step_pf(series, va, i),
        })
    lost_on += len(open_on)
    pairs.sort(key=lambda p: p['start'])
    return pairs, lost_on, lost_off


def group_cycles(pairs, tol=0.2, max_gap_s=1800):
    """
    Agrupa en sesiones los pares de la misma magnitud (±tol) que se repiten con
    menos de max_gap_s entre el fin de uno y el inicio del siguiente: el
    termostato de una estufa u horno, o el compresor de la nevera.
    Devuelve sesiones con start, end, n_cycles, delta_w (mediana), on_min
    (mediana por ciclo), duty (fracción encendida), energy_wh, pf (mediana).
    """
    sessions = []
    for p in pairs:
        target = None
        for s in reversed(sessions):
            if (p['start'] - s['end']).total_seconds() > max_gap_s:
                continue
            if abs(p['delta_w'] - s['ref_w']) <= tol * max(p['delta_w'], s['ref_w']):
                target = s
                break
        if target is None:
            sessions.append({'ref_w': p['delta_w'], 'pairs': [p], 'end': p['end']})
        else:
            target['pairs'].append(p)
            target['end'] = max(target['end'], p['end'])
    out = []
    for s in sessions:
        ps = s['pairs']
        span = (s['end'] - ps[0]['start']).total_seconds()
        # Tiempo encendido = unión de intervalos (dos pares pueden solaparse)
        on_s, cur_end = 0.0, None
        for p in ps:
            a = p['start'] if cur_end is None else max(p['start'], cur_end)
            if p['end'] > a:
                on_s += (p['end'] - a).total_seconds()
            cur_end = p['end'] if cur_end is None else max(cur_end, p['end'])
        pfs = sorted(p['pf'] for p in ps if p['pf'] is not None)
        out.append({
            'start': ps[0]['start'], 'end': s['end'], 'n_cycles': len(ps),
            'delta_w': sorted(p['delta_w'] for p in ps)[len(ps) // 2],
            'on_min': sorted(p['duration_min'] for p in ps)[len(ps) // 2],
            'span_min': round(span / 60, 1),
            'duty': round(on_s / span, 2) if span else 1.0,
            'energy_wh': round(sum(p['energy_wh'] for p in ps), 1),
            'pf': pfs[len(pfs) // 2] if pfs else None,
        })
    return out
