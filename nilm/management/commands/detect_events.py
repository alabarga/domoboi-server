"""
Detecta y clasifica eventos de las ubicaciones con medidor Tuya y los guarda
como Event (source=AUTO). Pensado para cron cada hora, tras
`tuya_poll --history --since <hoy>` (hace falta la resolución del histórico
para emparejar ON/OFF).

Uso:
    python manage.py detect_events                         # última hora(s), todas las Tuya
    python manage.py detect_events --location 13 --dry-run # muestra la petición, no llama ni escribe
    python manage.py detect_events --location 13 --since 2026-11-01 --until 2026-11-02
    python manage.py detect_events --location 13 --since 2026-11-01 --until 2026-11-02 --limit 1

Ventana: se aceptan sesiones cerradas que empiezan en [desde, hasta) y
terminan antes de ahora − 30 min. Sin --since/--until, desde = ahora − 8 h.
La restricción única (device, start_time) para AUTO hace que repetir la
ejecución no duplique. Ver docs/clasificacion_eventos.md.
"""
import json
from collections import Counter
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from nilm import classification as cls
from nilm import detection as det
from nilm.models import Appliance, ClassifiedSession, Device, Event

MIN_DELTA_W = 40
# Margen antes de la ventana: un ON puede quedar abierto hasta 4 h (pair_steps).
MARGIN = timedelta(hours=4)
# Una sesión que terminó hace menos de esto puede seguir en curso.
SETTLE = timedelta(minutes=30)
DEFAULT_LOOKBACK = timedelta(hours=8)
# ponytail: una sesión de más de ~7,5 h no llega cerrada a la ventana horaria
# y se pierde; reprocesar con --since/--until si hiciera falta.
METER_WINDOW = timedelta(days=30)
GENERAL_METER_W = 2000  # por debajo de esto el medidor solo ve un circuito
BACKGROUND_MAX_W = 150
BACKGROUND_MAX_MIN = 15
# Dos eventos del mismo tipo separados por menos de esto son un solo uso
# (p. ej. dos fuegos de la vitro a potencias distintas = dos sesiones).
MERGE_GAP = timedelta(minutes=15)
# Por debajo de esta confianza de FreeJev no se guarda el evento (cuenta como OTHER):
# "microondas de 12 min a las 05:00" salía con 0,19–0,27.
MIN_CONFIDENCE = 0.35
# Regla manual de luz conocida (--light-w): las luces LED/fluorescentes de una
# habitación (25–40 W) quedan por debajo de MIN_DELTA_W, el ruido en reposo es σ ≈ 4–6 W.
# La potencia de la luz de cada ubicación vive en Appliance(type='LIGHTS').power_w.
LIGHT_MIN_DELTA_W = 20
LIGHT_TOL = 0.25
LIGHT_DUR_MIN = (2, 240)


def _day(s):
    try:
        return timezone.make_aware(datetime.combine(date.fromisoformat(s), time.min))
    except ValueError:
        raise CommandError(f'Fecha inválida: {s} (usa YYYY-MM-DD)')


def is_background(session):
    """Un solo ciclo de < BACKGROUND_MAX_W que dura ≤ BACKGROUND_MAX_MIN."""
    return (session['n_cycles'] == 1 and session['delta_w'] < BACKGROUND_MAX_W
            and session['span_min'] <= BACKGROUND_MAX_MIN)


def event_class(label, session):
    """UNEXPECTED si se cocina de madrugada o durante más de 2 h; si no, NORMAL."""
    if label in ('OVEN', 'COOKTOP'):
        hour = timezone.localtime(session['start']).hour
        if hour < 6 or session['span_min'] > 120:
            return 'UNEXPECTED'
    return 'NORMAL'


def summary(session):
    d = session['delta_w']
    parts = [f'{d / 1000:.1f} kW' if d >= 1000 else f'{d} W']
    if session['n_cycles'] > 1:
        parts.append(f"{session['n_cycles']} ciclos")
    parts.append(f"{session['span_min']:.0f} min")
    if session['pf_class']:
        parts.append(session['pf_class'])
    return ' · '.join(parts)


def absorb(a, b):
    """Une el evento b en a (mismo tipo, contiguos o solapados)."""
    a.end_time = max(a.end_time, b.end_time)
    fa, fb = dict(a.features or {}), b.features or {}
    n = fa.get('sesiones', 1) + fb.get('sesiones', 1)
    d = max(fa.get('delta_w_max', fa.get('delta_w', 0)), fb.get('delta_w_max', fb.get('delta_w', 0)))
    fa.update(sesiones=n, delta_w_max=d)
    a.features = fa
    confs = [c for c in (a.confidence, b.confidence) if c is not None]
    a.confidence = min(confs) if confs else None
    dur = (a.end_time - a.start_time).total_seconds() / 60
    a.description = f"{n} sesiones · {dur:.0f} min · hasta {f'{d / 1000:.1f} kW' if d >= 1000 else f'{d} W'}"
    unexpected = 'UNEXPECTED' in (a.class_name, b.class_name)  # p. ej. luz de madrugada
    a.class_name = 'UNEXPECTED' if unexpected else event_class(a.type, {'start': a.start_time, 'span_min': dur})


MERGED_FIELDS = ['end_time', 'features', 'confidence', 'description', 'class_name']


def merge_events(dev, events):
    """
    Une los eventos nuevos del mismo tipo separados por < MERGE_GAP, entre sí
    o con uno ya guardado (otra ejecución horaria). Devuelve (a_crear, n_unidos).
    """
    out, merged = [], 0
    for ev in sorted(events, key=lambda e: e.start_time):
        # ya cubierto por un evento guardado (recarga de un rango ya procesado)
        if Event.objects.filter(source='AUTO', device=dev, type=ev.type, start_time__lte=ev.start_time,
                                end_time__gte=ev.end_time).exists():
            continue
        prev = next((e for e in reversed(out)
                     if e.type == ev.type and ev.start_time - e.end_time <= MERGE_GAP), None)
        if prev is None:
            prev = (Event.objects.filter(source='AUTO', device=dev, type=ev.type, start_time__lt=ev.start_time,
                                         end_time__gte=ev.start_time - MERGE_GAP)
                    .order_by('-end_time').first())
            if prev is not None:
                absorb(prev, ev)
                prev.save(update_fields=MERGED_FIELDS)
                merged += 1
                continue
            out.append(ev)
        else:
            absorb(prev, ev)
            merged += 1
    return out, merged


def light_events(dev, watts, since, until, now):
    """Usos de la luz conocida (~watts) cerrados en [since, until), como Event LIGHTS sin guardar."""
    load_to = min(now, until + MARGIN)
    series, _ = det.remove_spikes(det.load_series(dev, since - MARGIN, load_to), LIGHT_MIN_DELTA_W)
    pairs, _, _ = det.pair_steps(series, LIGHT_MIN_DELTA_W, va=det.load_va(dev, since - MARGIN, load_to))
    events = []
    for p in pairs:
        if not (since <= p['start'] < until and p['end'] <= now - SETTLE):
            continue
        if abs(p['delta_w'] - watts) > watts * LIGHT_TOL:
            continue
        if not (LIGHT_DUR_MIN[0] <= p['duration_min'] <= LIGHT_DUR_MIN[1]):
            continue
        night = 1 <= timezone.localtime(p['start']).hour < 5 and p['duration_min'] > 30  # despierta de madrugada
        events.append(Event(
            location=dev.location, device=dev, source='AUTO', type='LIGHTS',
            start_time=p['start'], end_time=p['end'], class_name='UNEXPECTED' if night else 'NORMAL',
            description=f"Luz · {p['delta_w']} W · {p['duration_min']:.0f} min", confidence=None,
            features={'regla': f'luz {watts:.0f} W', 'delta_w': p['delta_w'],
                      'dur_min': p['duration_min'], 'fp': p['pf']},
        ))
    return events


class Command(BaseCommand):
    help = 'Detecta y clasifica (FreeJev) los usos de electrodomésticos de los medidores Tuya.'

    def add_arguments(self, parser):
        parser.add_argument('--location', type=int, action='append', help='Solo esta ubicación (repetible)')
        parser.add_argument('--since', help='YYYY-MM-DD (por defecto: ahora − 8 h)')
        parser.add_argument('--until', help='YYYY-MM-DD, exclusivo (por defecto: ahora)')
        parser.add_argument('--dry-run', action='store_true', help='Muestra la petición; no llama a FreeJev ni escribe')
        parser.add_argument('--limit', type=int, help='Máximo de sesiones a enviar en total')
        parser.add_argument('--light-w', type=float,
                            help='Luz conocida de --location: guarda W en su Appliance LIGHTS (la ejecución horaria '
                                 'aplicará la regla desde entonces) y recarga el rango: usos de ~W (±25 %%, 2–240 min, '
                                 'saltos ≥ 20 W) como LIGHTS sin FreeJev')
        parser.add_argument('--fix-existing', action='store_true',
                            help='Revisa los eventos AUTO ya guardados (duración implausible, contiguos '
                                 'del mismo tipo) sin llamar a FreeJev; con --dry-run solo lista')

    def handle(self, *args, **o):
        self.verbosity = o['verbosity']
        now = timezone.now()
        accept_from = _day(o['since']) if o['since'] else now - DEFAULT_LOOKBACK
        accept_to = min(_day(o['until']), now) if o['until'] else now
        if accept_from >= accept_to:
            raise CommandError('--since debe ser anterior a --until')
        if o['fix_existing']:
            return self.fix_existing(accept_from, accept_to, o)
        if o['light_w']:
            return self.add_lights(accept_from, accept_to, now, o)
        api_key = getattr(settings, 'FREEJEV_API_KEY', '')
        if not api_key and not o['dry_run']:
            raise CommandError('Falta FREEJEV_API_KEY (local_settings.py o .env)')

        devices = Device.objects.filter(device_type='TUYA', location__isnull=False).select_related('location')
        if o['location']:
            devices = devices.filter(location_id__in=o['location'])
        budget = o['limit']
        total = Counter()
        for dev in devices.order_by('location_id'):
            stats, budget = self.process(dev, now, accept_from, accept_to, api_key, o, budget)
            total.update(stats)
            self.stdout.write(f"loc {dev.location_id} {dev.device_id}: " +
                              ', '.join(f'{k}={v}' for k, v in sorted(stats.items())))
            if budget is not None and budget <= 0:
                break
        self.stdout.write(self.style.SUCCESS('TOTAL: ' + ', '.join(f'{k}={v}' for k, v in sorted(total.items()))))

    def process(self, dev, now, accept_from, accept_to, api_key, o, budget):
        stats = Counter()
        light = dev.location.appliances.filter(type='LIGHTS', power_w__isnull=False).first()
        if light:  # regla de luz conocida (Appliance.power_w), sin FreeJev
            lights = light_events(dev, light.power_w, accept_from, accept_to, now)
            if o['dry_run']:
                stats['luces'] = len(lights)
            else:
                stats['luces'], _ = self.save(dev, lights, [])
        load_to = min(now, accept_to + MARGIN)
        series, _ = det.remove_spikes(det.load_series(dev, accept_from - MARGIN, load_to), MIN_DELTA_W)
        if len(series) < 3:
            stats['sin_datos'] = 1
            return stats, budget
        va = det.load_va(dev, accept_from - MARGIN, load_to)
        pairs, _, _ = det.pair_steps(series, MIN_DELTA_W, va=va)
        sessions = [s for s in det.group_cycles(pairs)
                    if accept_from <= s['start'] < accept_to and s['end'] <= now - SETTLE]
        stats['sesiones'] = len(sessions)
        sessions = [s for s in sessions if not (s['n_cycles'] < 3 and s['max_on_min'] < 1)]
        stats['transitorios'] = stats['sesiones'] - len(sessions)
        n = len(sessions)
        # ponytail: ciclo suelto pequeño y corto = carga de fondo (compresor de
        # nevera, standby); no dice nada de la rutina y costaría créditos.
        sessions = [s for s in sessions if not is_background(s)]
        stats['fondo'] = n - len(sessions)
        starts = [s['start'] for s in sessions]
        done = set(Event.objects.filter(source='AUTO', device=dev, start_time__in=starts)
                   .values_list('start_time', flat=True))
        done |= set(ClassifiedSession.objects.filter(device=dev, start_time__in=starts)
                    .values_list('start_time', flat=True))
        sessions = [s for s in sessions if s['start'] not in done]
        stats['ya_guardadas'] = len(done)
        if not sessions:
            return stats, budget

        # Contexto del medidor (30 días): ¿ve la casa entera o un circuito?
        recent = det.load_series(dev, now - METER_WINDOW, now)
        meter_max = det.pct([p for _, p in recent], 0.999) or 0
        daily = det.daily_baseline(recent)
        base = sorted(daily.values())[len(daily) // 2] if daily else None
        declared = set(dev.location.appliances.values_list('type', flat=True))
        context = {
            'vivienda': 'persona mayor que vive sola',
            'medidor': {
                'alcance': 'general' if meter_max >= GENERAL_METER_W else 'circuito parcial',
                'potencia_max_observada_w': round(meter_max),
                'base_nocturna_w': round(base) if base is not None else None,
            },
            'aparatos_declarados': sorted(declared) or 'desconocidos: cualquiera compatible',
        }

        items, local_only = [], 0
        for s in sessions:
            cands = cls.candidates(s, declared, meter_max)
            if not cands:
                local_only += 1
                continue
            items.append((s, cands))
        stats['sin_candidatos'] = local_only
        if budget is not None:
            items = items[:budget]
            budget -= len(items)

        to_create, seen = [], []
        for b in range(0, len(items), cls.BATCH_SIZE):
            batch = items[b:b + cls.BATCH_SIZE]
            qitems = [(f'E{i + 1}', cls.describe(s, f'E{i + 1}'), c) for i, (s, c) in enumerate(batch)]
            body = cls.build_request(context, qitems)
            stats['enviadas'] += len(batch)
            if o['dry_run']:
                self.stdout.write(json.dumps(body, ensure_ascii=False, indent=1))
                continue
            try:
                resp = cls.call_freejev(body, api_key, settings.FREEJEV_BASE_URL)
            except cls.FreeJevError as e:
                self.save(dev, to_create, seen)
                if e.fatal:
                    raise CommandError(f'{e} (guardados {len(to_create)} eventos antes del error)')
                self.stderr.write(self.style.WARNING(f'  loc {dev.location_id}: {e}; se sigue con la siguiente'))
                stats['error_api'] += 1
                break
            if b == 0 and o['limit']:
                self.stdout.write('Respuesta FreeJev: ' + json.dumps(resp, ensure_ascii=False)[:2000])
            usage = resp.get('usage') or {}
            for k in ('credits_used', 'creditsUsed', 'credits'):
                if isinstance(usage.get(k), (int, float)):
                    stats['creditos'] += usage[k]
            answers = cls.parse_answers(resp, body)
            for (qid, desc, _), (s, _c) in zip(qitems, batch):
                label, conf = answers.get(qid, (None, None))
                if self.verbosity >= 2:
                    self.stdout.write(f"  {timezone.localtime(s['start']):%a %m-%d %H:%M} {summary(s):<40} "
                                      f"→ {label} ({conf})  candidatos: {','.join(_c)}")
                if label is None:
                    stats['sin_respuesta'] += 1
                    continue
                seen.append(ClassifiedSession(device=dev, start_time=s['start'], label=label, confidence=conf))
                if label in cls.EXTRA:
                    stats[label] += 1
                    continue
                if conf is not None and conf < MIN_CONFIDENCE:
                    stats['baja_confianza'] += 1
                    continue
                stats[label] += 1
                to_create.append(Event(
                    location=dev.location, device=dev, source='AUTO',
                    start_time=s['start'], end_time=s['end'], type=label,
                    class_name=event_class(label, s), description=summary(s),
                    confidence=conf, features=desc,
                ))
        stats['guardadas'], stats['unidas'] = self.save(dev, to_create, seen)
        return stats, budget

    @staticmethod
    def save(dev, events, seen):
        events, merged = merge_events(dev, events)
        # bulk_create no lanza post_save: nunca hay push (además no se generan ALERT)
        Event.objects.bulk_create(events, ignore_conflicts=True)
        ClassifiedSession.objects.bulk_create(seen, ignore_conflicts=True)
        return len(events), merged

    def fix_existing(self, since, until, o):
        """Quita eventos AUTO incompatibles con su firma y une los contiguos del mismo tipo."""
        qs = Event.objects.filter(source='AUTO', start_time__gte=since, start_time__lt=until)
        if o['location']:
            qs = qs.filter(location_id__in=o['location'])
        dry = o['dry_run']
        removed, merged, kept = Counter(), Counter(), 0
        by_device = {}
        for ev in qs.order_by('device_id', 'start_time'):
            f = ev.features or {}
            if ev.confidence is not None and ev.confidence < MIN_CONFIDENCE and 'sesiones' not in f:
                removed['baja_confianza'] += 1
                self.stdout.write(f"  quitar  loc {ev.location_id} {timezone.localtime(ev.start_time):%a %m-%d %H:%M} "
                                  f"{ev.type:<16} confianza {ev.confidence:.2f}")
                if not dry:
                    ev.delete()  # su ClassifiedSession se queda: no se vuelve a pagar
                continue
            if 'sesiones' not in f and f.get('delta_w') is not None:
                fp = f.get('fp_tipo')
                sess = {'delta_w': f['delta_w'], 'span_min': f.get('dur_min'), 'pf_approx': f.get('fp_aprox'),
                        'pf_class': None if fp in (None, 'desconocido') else fp}
                if ev.type not in cls.candidates(sess, set(), 0):
                    removed[ev.type] += 1
                    self.stdout.write(f"  quitar  loc {ev.location_id} {timezone.localtime(ev.start_time):%a %m-%d %H:%M} "
                                      f"{ev.type:<16} {ev.description}")
                    if not dry:
                        # sin su registro, la próxima ejecución con --since la reclasifica
                        ClassifiedSession.objects.filter(device_id=ev.device_id, start_time=ev.start_time).delete()
                        ev.delete()
                    continue
            prev = next((e for e in reversed(by_device.setdefault(ev.device_id, []))
                         if e.type == ev.type and ev.start_time - e.end_time <= MERGE_GAP), None)
            if prev is None:
                by_device[ev.device_id].append(ev)
                kept += 1
                continue
            merged[ev.type] += 1
            self.stdout.write(f"  unir    loc {ev.location_id} {timezone.localtime(ev.start_time):%a %m-%d %H:%M} "
                              f"{ev.type:<16} con el de las {timezone.localtime(prev.start_time):%H:%M}")
            absorb(prev, ev)
            if not dry:
                prev.save(update_fields=MERGED_FIELDS)
                ev.delete()
        verb = 'se quitarían' if dry else 'quitados'
        self.stdout.write(self.style.SUCCESS(
            f"{verb}: {sum(removed.values())} ({dict(removed)}); unidos: {sum(merged.values())} ({dict(merged)}); "
            f"quedan {kept} eventos"))

    def add_lights(self, since, until, now, o):
        """--light-w: recarga las luces de --location y guarda la potencia en su Appliance LIGHTS."""
        if not o['location']:
            raise CommandError('--light-w requiere --location')
        watts, dry, total = o['light_w'], o['dry_run'], 0
        devices = Device.objects.filter(device_type='TUYA', location_id__in=o['location']).select_related('location')
        for dev in devices:
            events = light_events(dev, watts, since, until, now)
            for ev in events:
                self.stdout.write(f"  loc {dev.location_id} {timezone.localtime(ev.start_time):%a %m-%d %H:%M}-"
                                  f"{timezone.localtime(ev.end_time):%H:%M} {ev.class_name:<10} {ev.description}")
            if dry:
                total += len(events)
                continue
            # La ejecución horaria aplicará la regla a partir de ahora
            Appliance.objects.update_or_create(location=dev.location, type='LIGHTS',
                                               defaults={'power_w': round(watts)})
            created, merged = self.save(dev, events, [])
            total += created
            self.stdout.write(f"loc {dev.location_id}: guardadas={created}, unidas={merged}, luz={round(watts)} W")
        self.stdout.write(self.style.SUCCESS(f"{'se guardarían' if dry else 'guardadas'}: {total} luces"))
