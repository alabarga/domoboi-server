"""
Análisis (solo lectura) de la serie Tuya de una ubicación, como paso previo
a definir la estrategia de clasificación de eventos.

No escribe nada en la BD. Opcionalmente exporta los eventos candidatos a CSV.

Uso:
    python manage.py analyze_location 40
    python manage.py analyze_location 40 --since 2026-10-01 --until 2026-10-08
    python manage.py analyze_location 40 --min-delta 30 --csv /tmp/loc40.csv
    python manage.py analyze_location 40 --day 2026-10-15     # muestra el día completo
"""
import csv
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from statistics import median, pstdev

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max, Min
from django.utils import timezone

from nilm import detection as det
from nilm.models import Location, Measurement

POWER_BANDS = [40, 100, 200, 400, 800, 1500, 2500, 10 ** 6]
DURATION_BANDS = [5, 15, 45, 120, 10 ** 6]  # minutos
WEEKDAYS = ['L', 'M', 'X', 'J', 'V', 'S', 'D']


def _band(v, bands):
    for i, b in enumerate(bands):
        if v < b:
            return i
    return len(bands) - 1


def _label(bands, i, unit):
    lo = 0 if i == 0 else bands[i - 1]
    hi = bands[i]
    return f'≥{lo}{unit}' if hi >= 10 ** 6 else f'{lo}-{hi}{unit}'


def _local(ts):
    return timezone.localtime(ts)


def _parse_day(s):
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise CommandError(f'Fecha inválida: {s} (usa YYYY-MM-DD)')


def _calibration(qs):
    """Distribuciones de V, I, S=V·I y FP=P/S en las filas con los tres datapoints."""
    vs, is_, ss, pfs, samples = [], [], [], [], []
    rows = (qs.filter(telemetry__has_key='power_w').filter(telemetry__has_key='current_a')
            .filter(telemetry__has_key='voltage_v').values_list('telemetry', flat=True))
    for tel in rows.iterator():
        try:
            p, i, v = float(tel['power_w']), float(tel['current_a']), float(tel['voltage_v'])
        except (TypeError, ValueError):
            continue
        vs.append(v); is_.append(i); ss.append(v * i)
        if p > 30 and v * i > 0:
            pfs.append(p / (v * i))
            if len(samples) < 5 and p > 100:
                samples.append(tel)
    q = lambda xs: (det.pct(xs, .1), det.pct(xs, .5), det.pct(xs, .99)) if xs else (0, 0, 0)
    return {'n': len(vs), 'v': (det.pct(vs, .01), det.pct(vs, .5), det.pct(vs, .99)) if vs else (0, 0, 0),
            'i': q(is_), 'i_max': max(is_) if is_ else 0, 's': (det.pct(ss, .5), det.pct(ss, .99), max(ss) if ss else 0),
            'pf': q(pfs), 'n_pf': len(pfs), 'samples': samples}


def _calibration_lines(c):
    return [
        f"Filas: {c['n']}",
        f"V: p1={c['v'][0]:.1f} p50={c['v'][1]:.1f} p99={c['v'][2]:.1f}",
        f"I (A): p10={c['i'][0]:.3f} p50={c['i'][1]:.3f} p99={c['i'][2]:.3f} máx={c['i_max']:.3f}",
        f"S=V·I (VA): p50={c['s'][0]:.0f} p99={c['s'][1]:.0f} máx={c['s'][2]:.0f}",
        f"FP=P/S (P>30 W, {c['n_pf']} filas): p10={c['pf'][0]:.2f} p50={c['pf'][1]:.2f} p99={c['pf'][2]:.2f}",
    ]


class Command(BaseCommand):
    help = 'Analiza (solo lectura) la serie de potencia Tuya de una ubicación.'

    def add_arguments(self, parser):
        parser.add_argument('location_id', type=int, nargs='?')
        parser.add_argument('--overview', action='store_true', help='Una línea por cada dispositivo TUYA de la BD')
        parser.add_argument('--since', help='YYYY-MM-DD (por defecto: primer dato)')
        parser.add_argument('--until', help='YYYY-MM-DD, exclusivo (por defecto: hoy+1)')
        parser.add_argument('--day', help='YYYY-MM-DD: además lista las muestras y eventos de ese día')
        parser.add_argument('--min-delta', type=float, default=40, help='W sobre la base para considerar carga (40)')
        parser.add_argument('--merge-gap', type=int, default=240, help='s: une tramos separados por menos (240)')
        parser.add_argument('--csv', help='Ruta para exportar los eventos candidatos')

    def out(self, s=''):
        self.stdout.write(s)

    def h(self, s):
        self.out(''); self.out(self.style.MIGRATE_HEADING(f'── {s} ' + '─' * max(0, 60 - len(s))))

    def handle(self, *args, **o):
        if o['overview']:
            return self.overview()
        if o['location_id'] is None:
            raise CommandError('Indica location_id o --overview')
        try:
            loc = Location.objects.get(pk=o['location_id'])
        except Location.DoesNotExist:
            raise CommandError(f"No existe la ubicación {o['location_id']}")
        devices = list(loc.devices.all())
        self.out(f'Ubicación {loc.pk}: {loc.description} · electrodomésticos declarados: '
                 f"{', '.join(loc.appliances.values_list('type', flat=True)) or '—'}")
        if not devices:
            raise CommandError('La ubicación no tiene dispositivos')

        tz = timezone.get_current_timezone()
        for dev in devices:
            self.out(f'\n=== Dispositivo {dev.device_id} ({dev.device_type}, {dev.model}) ===')
            qs = Measurement.objects.filter(device=dev)
            span = qs.aggregate(first=Min('timestamp'), last=Max('timestamp'))
            if not span['first']:
                self.out('Sin medidas.'); continue
            since = (timezone.make_aware(datetime.combine(_parse_day(o['since']), time.min), tz)
                     if o['since'] else span['first'])
            until = (timezone.make_aware(datetime.combine(_parse_day(o['until']), time.min), tz)
                     if o['until'] else span['last'] + timedelta(seconds=1))
            self.analyze(dev, qs, since, until, o)

    def overview(self):
        from nilm.models import Device
        now = timezone.now()
        self.out(f'{"loc":>4} {"dispositivo":<24} {"días":>4} {"P50":>5} {"P99":>5} {"Pmáx":>6} '
                 f'{"base":>5} {"kWh/d":>6} {"FP50":>5} {"Imáx":>6} {"Smáx":>6}  ubicación')
        for dev in Device.objects.filter(device_type='TUYA').select_related('location').order_by('location_id'):
            qs = Measurement.objects.filter(device=dev)
            span = qs.aggregate(first=Min('timestamp'), last=Max('timestamp'))
            if not span['first']:
                self.out(f'{dev.location_id or "-":>4} {dev.device_id:<24} sin medidas'); continue
            series = det.load_series(dev, span['first'], now)
            powers = [p for _, p in series]
            if not powers:
                self.out(f'{dev.location_id or "-":>4} {dev.device_id:<24} sin power_w'); continue
            days = {_local(t).date() for t, _ in series}
            daily = det.daily_baseline(series)
            energy = det.load_energy(dev, span['first'], now)
            e_days = {_local(t).date() for t, _ in energy}
            kwh_day = sum(k for _, k in energy) / len(e_days) if e_days else 0
            cal = _calibration(qs)
            self.out(f'{dev.location_id or "-":>4} {dev.device_id:<24} {len(days):>4} {det.pct(powers, .5):>5.0f} '
                     f'{det.pct(powers, .99):>5.0f} {max(powers):>6.0f} '
                     f'{(median(daily.values()) if daily else 0):>5.0f} {kwh_day:>6.2f} '
                     f'{(cal["pf"][1] if cal["n"] else 0):>5.2f} {(cal["i_max"] if cal["n"] else 0):>6.2f} '
                     f'{(cal["s"][2] if cal["n"] else 0):>6.0f}  {dev.location.description if dev.location else "—"}')

    def analyze(self, dev, qs, since, until, o):
        min_d = o['min_delta']
        qs = qs.filter(timestamp__gte=since, timestamp__lt=until)
        self.out(f'Rango: {_local(since):%Y-%m-%d %H:%M} → {_local(until):%Y-%m-%d %H:%M}')
        n_days_total = max(1, (until - since).days)

        # ── 1. Cobertura ─────────────────────────────────────────────
        self.h('1. Cobertura')
        kinds = Counter()
        for tel in qs.values_list('telemetry', flat=True).iterator():
            if not isinstance(tel, dict):
                kinds['sin telemetry'] += 1
            elif 'power_w' in tel:
                kinds['power_w'] += 1
            elif 'energy_added_kwh' in tel:
                kinds['energy_added_kwh'] += 1
            elif 'energy_kwh' in tel:
                kinds['energy_kwh'] += 1
            else:
                kinds['otros (' + ','.join(sorted(k for k in tel if k != 'raw'))[:40] + ')'] += 1
        self.out('Filas por tipo: ' + ', '.join(f'{k}={v}' for k, v in kinds.most_common()))

        raw = det.load_series(dev, since, until)
        energy = det.load_energy(dev, since, until)
        if len(raw) < 3:
            self.out('Muy pocas muestras de potencia.'); return
        dts = [(b[0] - a[0]).total_seconds() for a, b in zip(raw, raw[1:])]
        per_day = Counter(_local(t).date() for t, _ in raw)
        days = sorted(per_day)
        self.out(f'Muestras power_w: {len(raw)} en {len(days)} días '
                 f'(mín {min(per_day.values())}, mediana {median(per_day.values()):.0f}, máx {max(per_day.values())} por día)')
        self.out(f'Intervalo entre muestras (s): p10={det.pct(dts, .1):.0f} p50={det.pct(dts, .5):.0f} '
                 f'p90={det.pct(dts, .9):.0f} p99={det.pct(dts, .99):.0f} máx={max(dts):.0f}')
        gaps = [(a[0], d) for a, d in zip(raw, dts) if d > 900]
        self.out(f'Huecos > 15 min: {len(gaps)} (suman {sum(d for _, d in gaps) / 3600:.1f} h); '
                 f'> 1 h: {sum(1 for _, d in gaps if d > 3600)}')
        for t, d in sorted(gaps, key=lambda x: -x[1])[:5]:
            self.out(f'   {_local(t):%Y-%m-%d %H:%M}  {d / 3600:.1f} h')
        dup = sum(1 for d in dts if d <= 0)
        self.out(f'Marcas de tiempo duplicadas/desordenadas: {dup}')
        thin = [d for d in days if per_day[d] < 100]
        if thin:
            self.out(f'Días con < 100 muestras: {len(thin)} ({", ".join(str(d) for d in thin[:10])}{"…" if len(thin) > 10 else ""})')

        # ── 2. Ruido ─────────────────────────────────────────────────
        self.h('2. Ruido y calidad')
        powers = [p for _, p in raw]
        dps = [b - a for a, b in zip(powers, powers[1:])]
        small = [d for d in dps if abs(d) < min_d]
        self.out(f'Potencia (W): p1={det.pct(powers, .01):.0f} p10={det.pct(powers, .1):.0f} p50={det.pct(powers, .5):.0f} '
                 f'p90={det.pct(powers, .9):.0f} p99={det.pct(powers, .99):.0f} máx={max(powers):.0f}')
        absd = [abs(d) for d in dps]
        self.out(f'|ΔP| consecutivo (W): p50={det.pct(absd, .5):.0f} p75={det.pct(absd, .75):.0f} '
                 f'p90={det.pct(absd, .9):.0f} p99={det.pct(absd, .99):.0f}')
        self.out(f'Jitter en reposo (|ΔP|<{min_d:.0f} W): {len(small)} saltos, σ={pstdev(small):.1f} W' if small else 'Sin saltos pequeños')
        for thr in (5, 10, 20, 40, 80):
            self.out(f'   saltos ≥ {thr:>3} W: {sum(1 for d in absd if d >= thr):>6} ({100 * sum(1 for d in absd if d >= thr) / len(absd):.1f}%)')
        clean, n_spikes = det.remove_spikes(raw, min_d)
        self.out(f'Picos aislados (1 muestra, ≥{min_d:.0f} W, <3 min) eliminados: {n_spikes}')
        bad = Counter()
        for tel in qs.filter(telemetry__has_key='power_w').values_list('telemetry', flat=True).iterator():
            try:
                p = float(tel.get('power_w'))
            except (TypeError, ValueError):
                bad['power_w no numérico'] += 1; continue
            a, v = tel.get('current_a'), tel.get('voltage_v')
            if p < 0:
                bad['P < 0'] += 1
            if p == 0 and a not in (None, 0) and float(a) > 0.05:
                bad['P = 0 con I > 0.05 A'] += 1
            if v is not None and not (200 <= float(v) <= 250):
                bad['V fuera de 200-250'] += 1
            if a is not None and v is not None and p > 50:
                pf = p / (float(a) * float(v) or 1)
                if pf > 1.1:
                    bad['P > V·I·1.1'] += 1
        self.out('Valores sospechosos: ' + (', '.join(f'{k}={v}' for k, v in bad.items()) or 'ninguno'))

        # ── 2b. Calibración ──────────────────────────────────────────
        self.h('2b. Calibración (filas con P, I y V)')
        cal = _calibration(qs)
        if not cal['n']:
            self.out('Sin filas con power_w + current_a + voltage_v')
        else:
            for line in _calibration_lines(cal):
                self.out(line)
            self.out('Muestra de valores crudos (raw) → escalados:')
            for tel in cal['samples']:
                raw = tel.get('raw') or {}
                keys = [k for k in raw if k.startswith(('cur_', 'add_ele')) or k.endswith('_coe')]
                self.out(f"   P={tel.get('power_w')} I={tel.get('current_a')} V={tel.get('voltage_v')}  raw: "
                         + ', '.join(f'{k}={raw[k]}' for k in sorted(keys)))

        # ── 3. Carga base ────────────────────────────────────────────
        self.h('3. Carga base (always-on, p10 02:00-05:00)')
        daily = det.daily_baseline(clean)
        if daily:
            vals = list(daily.values())
            glob = median(vals)
            self.out(f'Base diaria (W): mediana={glob:.0f} mín={min(vals):.0f} máx={max(vals):.0f} σ={pstdev(vals):.1f} ({len(vals)} noches)')
            self.out('   ' + '  '.join(f'{d:%m-%d}:{w:.0f}' for d, w in sorted(daily.items())[-14:]))
        else:
            glob = det.pct(powers, .05)
            self.out(f'Sin noches suficientes; base = p5 global = {glob:.0f} W')
        base = det.baseline_fn(daily, glob)

        # ── 4. Saltos ────────────────────────────────────────────────
        self.h(f'4. Saltos ≥ {min_d:.0f} W (tras quitar picos)')
        st = det.steps(clean, min_d)
        on = [d for _, d in st if d > 0]
        off = [-d for _, d in st if d < 0]
        self.out(f'ON: {len(on)}  OFF: {len(off)}  (por día: {len(st) / max(1, len(days)):.0f})')
        self.out(f'{"banda":>12} {"ON":>6} {"OFF":>6}')
        for i in range(len(POWER_BANDS)):
            self.out(f'{_label(POWER_BANDS, i, "W"):>12} {sum(1 for d in on if _band(d, POWER_BANDS) == i):>6} '
                     f'{sum(1 for d in off if _band(d, POWER_BANDS) == i):>6}')
        # Picos finos: histograma de ON en cubos de 25 W hasta 3 kW
        hist = Counter(int(d // 25) * 25 for d in on if d < 3000)
        top = sorted(hist.items(), key=lambda x: -x[1])[:12]
        self.out('ON más frecuentes (cubos 25 W): ' + ', '.join(f'{k}-{k + 25}W×{v}' for k, v in sorted(top)))

        # ── 5. Eventos candidatos ────────────────────────────────────
        self.h('5. Eventos candidatos (tramos sobre la base)')
        segs = det.segment(clean, base, min_d, merge_gap_s=o['merge_gap'])
        self.out(f'Tramos: {len(segs)} ({len(segs) / max(1, len(days)):.1f}/día); '
                 f'1 sola muestra: {sum(1 for s in segs if s["n_samples"] == 1)}; '
                 f'unidos por hueco < {o["merge_gap"]} s: {sum(1 for s in segs if s["merged"])}')
        if segs:
            durs = [s['duration_min'] for s in segs]
            self.out(f'Duración (min): p10={det.pct(durs, .1)} p50={det.pct(durs, .5)} p90={det.pct(durs, .9)} máx={max(durs)}')
            tot_e = sum(s['energy_wh'] for s in segs) / 1000
            self.out(f'Energía en eventos: {tot_e:.1f} kWh ({tot_e / max(1, len(days)):.2f} kWh/día sobre la base)')

            self.out('\nClusters meseta × duración (n · energía media Wh · horas locales más comunes):')
            cl = defaultdict(list)
            for s in segs:
                cl[(_band(s['plateau_w'], POWER_BANDS), _band(s['duration_min'], DURATION_BANDS))].append(s)
            for (pb, db), ss in sorted(cl.items(), key=lambda x: -len(x[1])):
                if len(ss) < 2:
                    continue
                hours = Counter(_local(s['start']).hour for s in ss).most_common(4)
                self.out(f'  {_label(POWER_BANDS, pb, "W"):>11} × {_label(DURATION_BANDS, db, "min"):>10}: '
                         f'n={len(ss):>4} ({len(ss) / max(1, len(days)):.1f}/día) '
                         f'meseta~{median(s["plateau_w"] for s in ss):.0f}W pico~{median(s["peak_w"] for s in ss):.0f}W '
                         f'{median(s["energy_wh"] for s in ss):.0f}Wh '
                         f'escalones~{median(s["n_steps"] for s in ss):.0f} '
                         f'h:{",".join(f"{h}({c})" for h, c in hours)}')

            self.out('\nRecurrencia (intervalo entre inicios dentro del mismo cluster):')
            for (pb, db), ss in sorted(cl.items(), key=lambda x: -len(x[1]))[:6]:
                if len(ss) < 10:
                    continue
                gaps_min = [(b['start'] - a['start']).total_seconds() / 60 for a, b in zip(ss, ss[1:])]
                gaps_min = [g for g in gaps_min if g < 240]
                if len(gaps_min) < 5:
                    continue
                m = median(gaps_min)
                cv = pstdev(gaps_min) / m if m else 0
                self.out(f'  {_label(POWER_BANDS, pb, "W")} × {_label(DURATION_BANDS, db, "min")}: '
                         f'periodo mediano {m:.0f} min, CV={cv:.2f} '
                         f'({"periódico" if cv < 0.5 else "irregular"}, {len(gaps_min)} intervalos < 4 h)')

            self.out('\nMayores eventos por energía:')
            for s in sorted(segs, key=lambda s: -s['energy_wh'])[:10]:
                self.out(f'  {_local(s["start"]):%a %Y-%m-%d %H:%M} {s["duration_min"]:>6} min '
                         f'meseta {s["plateau_w"]:>5} W pico {s["peak_w"]:>5} W {s["energy_wh"]:>6} Wh '
                         f'muestras {s["n_samples"]:>3} escalones {s["n_steps"]:>2}')

        # ── 6. Contraste con la energía reportada ────────────────────
        self.h('6. Contraste ∫power_w vs energy_added_kwh')
        ratios, rep_tot, int_tot = [], 0.0, 0.0
        for (t0, _), (t1, kwh) in zip(energy, energy[1:]):
            if not (60 <= (t1 - t0).total_seconds() <= 7200):
                continue
            ik = det.integrate_kwh(clean, t0, t1)
            rep_tot += kwh; int_tot += ik
            if ik > 0.005:
                ratios.append(kwh / ik)
        if ratios:
            self.out(f'{len(ratios)} intervalos: reportado/integrado p10={det.pct(ratios, .1):.2f} '
                     f'p50={det.pct(ratios, .5):.2f} p90={det.pct(ratios, .9):.2f}; '
                     f'total {rep_tot:.2f} kWh reportados vs {int_tot:.2f} kWh integrados')
        else:
            self.out(f'Sin intervalos comparables ({len(energy)} lecturas de energía)')

        # Carga oculta: energía reportada que power_w no ve, convertida a W medios
        self.h('6b. Carga oculta (energía reportada − ∫power_w, en W medios)')
        hidden = []
        for (t0, _), (t1, kwh) in zip(energy, energy[1:]):
            sec = (t1 - t0).total_seconds()
            if not (60 <= sec <= 7200):
                continue
            ik = det.integrate_kwh(clean, t0, t1)
            hidden.append((t0, t1, sec, kwh * 3.6e6 / sec, (kwh - ik) * 3.6e6 / sec))
        if hidden:
            hw = [h[4] for h in hidden]
            self.out(f'W ocultos por intervalo: p10={det.pct(hw, .1):.0f} p50={det.pct(hw, .5):.0f} '
                     f'p90={det.pct(hw, .9):.0f} p99={det.pct(hw, .99):.0f} máx={max(hw):.0f}')
            secs = [h[2] for h in hidden]
            self.out(f'Duración de intervalo add_ele (s): p10={det.pct(secs, .1):.0f} p50={det.pct(secs, .5):.0f} p90={det.pct(secs, .9):.0f}')
            for thr in (100, 300, 1000):
                self.out(f'   intervalos con ≥ {thr:>4} W ocultos: {sum(1 for x in hw if x >= thr)}')
            short = [h for h in hidden if h[2] < 900]
            self.out(f'Intervalos < 15 min (0,1 kWh alcanzados rápido → carga alta): {len(short)}, '
                     f'W medios p50={det.pct([h[3] for h in short], .5) or 0:.0f}')
            self.out('Energía oculta por hora local (Wh/día):')
            by_h = defaultdict(float)
            for t0, t1, sec, w, hwv in hidden:
                by_h[_local(t0).hour] += max(0, hwv) * sec / 3600
            pk = max(by_h.values()) or 1
            for hr in range(24):
                self.out(f'  {hr:02d}h {by_h[hr] / n_days_total:>6.0f} Wh ' + '█' * int(30 * by_h[hr] / pk))
            # Bloques: intervalos consecutivos con ≥ 300 W ocultos
            blocks, cur = [], None
            for t0, t1, sec, w, hwv in hidden:
                if hwv >= 300:
                    if cur and t0 <= cur[1]:
                        cur[1] = t1; cur[2] += hwv * sec / 3600; cur[3] = max(cur[3], w)
                    else:
                        cur = [t0, t1, hwv * sec / 3600, w]; blocks.append(cur)
                else:
                    cur = None
            self.out(f'Bloques de carga oculta ≥ 300 W: {len(blocks)} ({len(blocks) / n_days_total:.1f}/día)')
            bd = [(b[1] - b[0]).total_seconds() / 60 for b in blocks]
            if bd:
                self.out(f'   duración (min) p10={det.pct(bd, .1):.0f} p50={det.pct(bd, .5):.0f} p90={det.pct(bd, .9):.0f}; '
                         f'energía (Wh) p50={det.pct([b[2] for b in blocks], .5):.0f}')
                for b in sorted(blocks, key=lambda b: -b[2])[:10]:
                    self.out(f'   {_local(b[0]):%a %Y-%m-%d %H:%M}-{_local(b[1]):%H:%M} {b[2]:>5.0f} Wh ocultos, W medio máx {b[3]:.0f}')

        # ── 7. Rutina ────────────────────────────────────────────────
        self.h('7. Rutina: Wh/día sobre la base, por hora local')
        by_hour = defaultdict(float)
        by_wd_hour = defaultdict(float)
        wd_days = Counter(d.weekday() for d in days)
        for s in segs:
            lt = _local(s['start'])
            by_hour[lt.hour] += s['energy_wh']
            by_wd_hour[(lt.weekday(), lt.hour)] += s['energy_wh']
        n_days = max(1, len(days))
        peak = max(by_hour.values()) if by_hour else 1
        for hr in range(24):
            v = by_hour[hr] / n_days
            self.out(f'  {hr:02d}h {v:>6.0f} Wh ' + '█' * int(30 * by_hour[hr] / peak if peak else 0))
        self.out('\nEventos/día por día de la semana × franja (0-6, 6-9, 9-13, 13-16, 16-20, 20-24):')
        slots = [(0, 6), (6, 9), (9, 13), (13, 16), (16, 20), (20, 24)]
        cnt = Counter((_local(s['start']).weekday(), next(i for i, (a, b) in enumerate(slots) if a <= _local(s['start']).hour < b)) for s in segs)
        for wd in range(7):
            nd = wd_days[wd] or 1
            self.out(f'  {WEEKDAYS[wd]} ({wd_days[wd]:>2} días): ' + ' '.join(f'{cnt[(wd, i)] / nd:>5.1f}' for i in range(len(slots))))

        # ── Día concreto ─────────────────────────────────────────────
        if o['day']:
            d = _parse_day(o['day'])
            self.h(f'Día {d}')
            for t, p in raw:
                if _local(t).date() == d:
                    self.out(f'  {_local(t):%H:%M:%S} {p:>7.1f} W  (base {base(t):.0f})')
            for t0e, t1e in zip(energy, energy[1:]):
                if _local(t1e[0]).date() == d:
                    sec = (t1e[0] - t0e[0]).total_seconds()
                    ik = det.integrate_kwh(clean, t0e[0], t1e[0])
                    self.out(f'  ENERGÍA {_local(t0e[0]):%H:%M}-{_local(t1e[0]):%H:%M} {t1e[1]:.3f} kWh '
                             f'= {t1e[1] * 3.6e6 / sec if sec else 0:>6.0f} W medios (∫power_w {ik * 3.6e6 / sec if sec else 0:.0f} W)')
            for s in segs:
                if _local(s['start']).date() == d:
                    self.out(f'  EVENTO {_local(s["start"]):%H:%M}-{_local(s["end"]):%H:%M} {s}')

        if o['csv']:
            with open(o['csv'], 'w', newline='') as f:
                w = csv.writer(f)
                cols = ['start', 'end', 'duration_min', 'plateau_w', 'peak_w', 'energy_wh', 'n_samples',
                        'n_steps', 'merged', 'on_delta_w', 'off_delta_w']
                w.writerow(['device'] + cols + ['hour', 'weekday', 'base_w'])
                for s in segs:
                    lt = _local(s['start'])
                    w.writerow([dev.device_id] + [_local(s[c]).isoformat() if c in ('start', 'end') else s[c] for c in cols]
                               + [lt.hour, lt.weekday(), round(base(s['start']))])
            self.out(f'\nCSV: {o["csv"]} ({len(segs)} eventos)')
