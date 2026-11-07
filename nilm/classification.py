"""
Clasificación de sesiones de uso (nilm.detection.group_cycles) con FreeJev.

FreeJev (https://freejev.org/docs) responde preguntas `choice` sobre un
`state` JSON. Por cada sesión se envía un descriptor agregado (nunca
Measurements sueltas) y una pregunta con los electrodomésticos candidatos:
los declarados en la ubicación (Appliance) o, si no hay ninguno, todos;
filtrados por tipo de carga (FP) y por la potencia que el medidor puede ver.

Ver docs/clasificacion_eventos.md.
"""
import hashlib
import json
import time

import requests
from django.utils import timezone

# Firmas por tipo de Event.APPLIANCE_TYPES. pf: clases de FP compatibles
# (None = cualquiera). Rangos de ΔP en W; candidates() aplica ±50 %.
SIGNATURES = {
    'FRIDGE': dict(min_w=50, max_w=250, pf={'motor/electrónica'},
                   desc='Nevera o congelador: 50–250 W, ciclos de 5–40 min repetidos todo el día y la noche.'),
    'COOKTOP': dict(min_w=1000, max_w=2500, pf={'resistivo'},
                    desc='Vitrocerámica o inducción: 1–2,5 kW que se encienden y apagan cada 1–3 min durante 15–90 min, a la hora de comer o cenar.'),
    'HEATER': dict(min_w=500, max_w=2500, pf={'resistivo'},
                   desc='Estufa o radiador eléctrico: 0,5–2,5 kW, ciclos de termostato durante horas, por la mañana o la tarde-noche, más en invierno.'),
    'OVEN': dict(min_w=1500, max_w=3500, pf={'resistivo'},
                 desc='Horno: 1,5–3,5 kW, 20–120 min con ciclos de termostato, normalmente para comer o cenar.'),
    'KETTLE': dict(min_w=1500, max_w=3000, pf={'resistivo'},
                   desc='Hervidor o cafetera: 1,5–3 kW durante 1–5 min, sobre todo en el desayuno.'),
    'WATER_HEATER': dict(min_w=1200, max_w=2500, pf={'resistivo'},
                         desc='Termo eléctrico: 1,2–2,5 kW durante 30–180 min, a menudo de madrugada o tras la ducha.'),
    'WASHING MACHINE': dict(min_w=200, max_w=2200, pf=None,
                            desc='Lavadora: calentamiento de 1,8–2,2 kW y motor de 200–500 W, ciclo de 45–150 min, normalmente por la mañana.'),
    'MICROWAVE': dict(min_w=800, max_w=1500, pf=None,
                      desc='Microondas: 0,8–1,5 kW durante 1–10 min, a la hora de las comidas.'),
    'IRON': dict(min_w=1000, max_w=2400, pf={'resistivo'},
                 desc='Plancha: 1–2,4 kW con ciclos cortos de termostato durante 10–60 min.'),
    'TV': dict(min_w=40, max_w=250, pf={'motor/electrónica'},
               desc='Televisión: 40–250 W estables durante 0,5–6 h, sobre todo por la tarde y la noche.'),
    'LIGHTS': dict(min_w=10, max_w=300, pf=None,
                   desc='Luces: 10–300 W, encendidas al anochecer o en momentos puntuales de noche.'),
}
NAMES = {
    'FRIDGE': 'Nevera o congelador', 'COOKTOP': 'Vitrocerámica o inducción', 'HEATER': 'Estufa o radiador',
    'OVEN': 'Horno', 'KETTLE': 'Hervidor o cafetera', 'WATER_HEATER': 'Termo eléctrico',
    'WASHING MACHINE': 'Lavadora', 'MICROWAVE': 'Microondas', 'IRON': 'Plancha', 'TV': 'Televisión',
    'LIGHTS': 'Luces',
}
EXTRA = {
    'OTHER': 'Otro aparato, varios a la vez, o ninguno de los anteriores encaja.',
    'NOISE': 'No es un uso real: ruido o transitorio del medidor.',
}

BATCH_SIZE = 16  # máximo de preguntas por petición en FreeJev
WEEKDAYS = ['lun', 'mar', 'mié', 'jue', 'vie', 'sáb', 'dom']

ROUTINE = (
    'Persona mayor que vive sola, zona rural, horario español. Rutina esperada: '
    '01–06 h casi sin actividad; 07–09 h levantarse y desayunar; 09–13 h tareas '
    '(lavadora, plancha) o salir; 13–15 h comida principal; 15–17 h siesta o TV; '
    '17–21 h TV y luces al anochecer, estufa en invierno; 20–22 h cena ligera; '
    '22–24 h acostarse.'
)


GUIDE = (
    'Cada evento es un uso de un solo aparato: delta_w = potencia que añade, dur_min = duración '
    'de la sesión, ciclos = encendidos repetidos (termostato), on_min = duración típica de cada '
    'ciclo, duty = fracción del tiempo encendido, fp_tipo = tipo de carga (si fp_aprox es true, '
    'solo orientativo). Compara con state.firmas, la hora local (inicio) y la rutina esperada.'
)


def candidates(session, declared, meter_max_w):
    """
    Tipos plausibles para una sesión. declared: tipos de Appliance de la
    ubicación (vacío = todos). Devuelve la lista sin OTHER/NOISE.
    """
    pool = [t for t in SIGNATURES if not declared or t in declared]
    delta = session['delta_w']
    trust_pf = session.get('pf_class') in ('resistivo', 'motor/electrónica') and not session.get('pf_approx')
    out = []
    for t in pool:
        sig = SIGNATURES[t]
        if meter_max_w and sig['min_w'] > meter_max_w:
            continue  # el medidor nunca ha visto tanta potencia (circuito parcial)
        if not (sig['min_w'] * 0.5 <= delta <= sig['max_w'] * 1.5):
            continue
        if trust_pf and sig['pf'] and session['pf_class'] not in sig['pf']:
            continue
        out.append(t)
    return out


def describe(session, qid):
    """Descriptor compacto que recibe FreeJev (sin datos personales)."""
    lt = timezone.localtime(session['start'])
    return {
        'id': qid,
        'inicio': f"{WEEKDAYS[lt.weekday()]} {lt:%H:%M}",
        'dur_min': session['span_min'],
        'delta_w': session['delta_w'],
        'ciclos': session['n_cycles'],
        'on_min': session['on_min'],
        'duty': session['duty'],
        'energia_wh': session['energy_wh'],
        'fp': session['pf'],
        'fp_tipo': session['pf_class'] or 'desconocido',
        'fp_aprox': session['pf_approx'],
    }


def build_request(context, items):
    """
    context: dict con medidor/base/aparatos (sin nombre ni dirección).
    items: [(qid, descriptor, [tipos candidatos])], como mucho BATCH_SIZE.
    """
    assert len(items) <= BATCH_SIZE
    # Las firmas van una sola vez en state (se cobra por token de entrada);
    # cada criterio lleva solo el nombre.
    used = sorted({t for _, _, cands in items for t in cands})
    state = dict(context, rutina_esperada=ROUTINE, como_decidir=GUIDE,
                 firmas={t: SIGNATURES[t]['desc'] for t in used}, eventos=[d for _, d, _ in items])
    questions = {}
    for qid, _, cands in items:
        criteria = {t: f'{NAMES[t]} (firma en state.firmas.{t})' for t in cands}
        criteria.update(EXTRA)
        questions[qid] = {
            'type': 'choice',
            'instructions': f'¿Qué electrodoméstico produjo el evento {qid} de state.eventos? (ver state.como_decidir)',
            'criteria': criteria,
        }
    return {'state': state, 'questions': questions}


def _label_conf(answer, allowed):
    """(etiqueta, confianza) de una respuesta choice; el formato exacto no está documentado."""
    if isinstance(answer, str):
        return (answer, None) if answer in allowed else (None, None)
    if not isinstance(answer, dict):
        return None, None
    probs = next((answer[k] for k in ('probabilities', 'scores', 'distribution', 'criteria')
                  if isinstance(answer.get(k), dict)), None)
    if probs is None and answer and all(isinstance(v, (int, float)) for v in answer.values()):
        probs = answer  # {etiqueta: probabilidad}
    label = next((answer[k] for k in ('answer', 'choice', 'label', 'value', 'result')
                  if isinstance(answer.get(k), str)), None)
    if label is None and probs:
        label = max(probs, key=probs.get)
    # Formato real (nov-2026): {"type": "choice", "choice": "FRIDGE", "confidence": 0.42,
    # "probabilities": {"FRIDGE": 0.54, "OTHER": 0.41, ...}}. Se prefiere la confianza de la API.
    conf = next((float(answer[k]) for k in ('confidence', 'probability', 'p')
                 if isinstance(answer.get(k), (int, float))), None)
    if conf is None and probs and label in probs:
        conf = float(probs[label])
    return (label, conf) if label in allowed else (None, None)


def parse_answers(resp, body):
    """{qid: (etiqueta, confianza)} con solo etiquetas que estaban entre los criterios."""
    answers = resp.get('answers') or {}
    if isinstance(answers, list):  # [{id/question: qid, ...}]
        answers = {a.get('id') or a.get('question'): a for a in answers if isinstance(a, dict)}
    out = {}
    for qid, q in body['questions'].items():
        out[qid] = _label_conf(answers.get(qid), set(q['criteria']))
    return out


class FreeJevError(Exception):
    pass


def call_freejev(body, api_key, base_url, http=requests, max_retries=3):
    """POST /api/v1/decisions con reintento en 429/503 (Retry-After). Devuelve el JSON."""
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True)
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        'Idempotency-Key': hashlib.sha256(raw.encode()).hexdigest()[:40],
    }
    for attempt in range(max_retries + 1):
        r = http.post(f'{base_url.rstrip("/")}/api/v1/decisions', data=raw.encode(), headers=headers, timeout=120)
        if r.status_code in (409, 429, 502, 503, 504) and attempt < max_retries:  # 409: otra llamada en curso
            time.sleep(min(float(r.headers.get('Retry-After') or 10), 120))
            continue
        if r.status_code >= 400:
            raise FreeJevError(f'FreeJev {r.status_code}: {r.text[:300]}')
        return r.json()
    raise FreeJevError('FreeJev: demasiados reintentos')
