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
from nilm.models import Device, Event

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


def _day(s):
    try:
        return timezone.make_aware(datetime.combine(date.fromisoformat(s), time.min))
    except ValueError:
        raise CommandError(f'Fecha inválida: {s} (usa YYYY-MM-DD)')


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


class Command(BaseCommand):
    help = 'Detecta y clasifica (FreeJev) los usos de electrodomésticos de los medidores Tuya.'

    def add_arguments(self, parser):
        parser.add_argument('--location', type=int, action='append', help='Solo esta ubicación (repetible)')
        parser.add_argument('--since', help='YYYY-MM-DD (por defecto: ahora − 8 h)')
        parser.add_argument('--until', help='YYYY-MM-DD, exclusivo (por defecto: ahora)')
        parser.add_argument('--dry-run', action='store_true', help='Muestra la petición; no llama a FreeJev ni escribe')
        parser.add_argument('--limit', type=int, help='Máximo de sesiones a enviar en total')

    def handle(self, *args, **o):
        now = timezone.now()
        accept_from = _day(o['since']) if o['since'] else now - DEFAULT_LOOKBACK
        accept_to = min(_day(o['until']), now) if o['until'] else now
        if accept_from >= accept_to:
            raise CommandError('--since debe ser anterior a --until')
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
        done = set(Event.objects.filter(source='AUTO', device=dev, start_time__in=[s['start'] for s in sessions])
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

        to_create = []
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
                Event.objects.bulk_create(to_create, ignore_conflicts=True)
                raise CommandError(f'{e} (guardados {len(to_create)} eventos antes del error)')
            if b == 0 and o['limit']:
                self.stdout.write('Respuesta FreeJev: ' + json.dumps(resp, ensure_ascii=False)[:2000])
            usage = resp.get('usage') or {}
            for k in ('credits_used', 'creditsUsed', 'credits'):
                if isinstance(usage.get(k), (int, float)):
                    stats['creditos'] += usage[k]
            answers = cls.parse_answers(resp, body)
            for (qid, desc, _), (s, _) in zip(qitems, batch):
                label, conf = answers.get(qid, (None, None))
                if label is None:
                    stats['sin_respuesta'] += 1
                    continue
                if label in cls.EXTRA:
                    stats[label] += 1
                    continue
                stats[label] += 1
                to_create.append(Event(
                    location=dev.location, device=dev, source='AUTO',
                    start_time=s['start'], end_time=s['end'], type=label,
                    class_name=event_class(label, s), description=summary(s),
                    confidence=conf, features=desc,
                ))
        if to_create:
            # bulk_create no lanza post_save: nunca hay push (además no se generan ALERT)
            Event.objects.bulk_create(to_create, ignore_conflicts=True)
        stats['guardadas'] = len(to_create)
        return stats, budget
