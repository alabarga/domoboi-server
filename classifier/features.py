"""
Características de un evento NILM, comunes a los datos públicos y a los de DOMOBOI.

Un evento es una ventana corta de potencia (~3 s) alrededor de un escalón, más
su ΔP firmado. Para que el MISMO clasificador sirva con datos de distinta
frecuencia (TraceBase ~1 Hz, SynD 5 Hz, DOMOBOI ~10 Hz) las características:

  * dependen solo de la forma de la ventana y de ΔP, no del nº de muestras;
  * NO usan el nivel absoluto (avg/min/max en vatios): en una acometida
    completa el nivel incluye todas las demás cargas encendidas y no
    identifica al aparato. Lo que sí lo identifica es el salto (ΔP) y cómo
    se desarrolla (arranque, pico inicial, rizado).

Equivalencias con la tabla nilm_measurement:
    delta_p  <-  Measurement.value      (ΔP firmado, W)
    readings <-  Measurement.readings   (muestras de la ventana, W)
"""
import numpy as np

CLASSES = [
    "FRIDGE", "OVEN", "KETTLE", "MICROWAVE",
    "WASHING MACHINE", "IRON", "LIGHTS", "TV",
    "COOKTOP", "HEATER", "WATER_HEATER",   # alineadas con Event.APPLIANCE_TYPES y detect_events
    "COFFEE_MAKER", "DISHWASHER", "DRYER", # con datos públicos (TraceBase y ECO); no están aún en la app
    "OTHER",
]

# Nº de puntos de la forma normalizada (independiente de la frecuencia de muestreo)
SHAPE_POINTS = 8

FEATURE_NAMES = (
    ["log_abs_delta", "sign", "rng_rel", "std_rel", "overshoot", "undershoot", "monotonic"]
    + [f"shape_{i}" for i in range(SHAPE_POINTS)]
)

# Características de SESIÓN: qué hace el aparato en el tiempo, no solo cómo es el escalón.
# Con solo ΔP, hervidor, microondas y cafetera (todos ~1 kW) se confunden; lo que los separa
# es cuánto dura el uso, si se repite (termostato) y cuánta energía mueve.
SESSION_FEATURE_NAMES = ["paired", "log_on_s", "log_span_s", "log_cycles", "duty", "log_energy_wh"]
ALL_FEATURE_NAMES = FEATURE_NAMES + SESSION_FEATURE_NAMES

PAIR_TOL = 0.3          # |ΔON - |ΔOFF|| <= tol * máx: mismo aparato
GROUP_TOL = 0.2         # pares de la misma magnitud (±tol) forman una sesión
GROUP_GAP_S = 1800      # entre ciclos de una sesión, como mucho 30 min
MAX_ON_S = 4 * 3600     # un ON sin OFF tras 4 h se descarta (igual que nilm.detection.pair_steps)

_EPS = 1e-6


def pair_steps(steps, tol=PAIR_TOL, max_on_s=MAX_ON_S):
    """
    steps: [(t_segundos, ΔP)] ordenados por t. Empareja cada subida (ON) con la primera
    bajada (OFF) posterior de magnitud parecida; entre varios ON abiertos que encajan elige
    el más reciente. Misma lógica que nilm.detection.pair_steps (producción).
    Devuelve [(i_on, i_off)] con índices de steps.
    """
    open_on, pairs = [], []
    for i, (t, d) in enumerate(steps):
        open_on = [j for j in open_on if t - steps[j][0] <= max_on_s]
        if d > 0:
            open_on.append(i)
            continue
        for j in reversed(open_on):
            on = steps[j][1]
            if abs(on - abs(d)) <= tol * max(on, abs(d)):
                pairs.append((j, i))
                open_on.remove(j)
                break
    return pairs


def session_features(steps, tol=PAIR_TOL, group_tol=GROUP_TOL, max_gap_s=GROUP_GAP_S):
    """
    Una fila de SESSION_FEATURE_NAMES por cada paso de `steps` ([(t_s, ΔP)] ordenados):
      paired      1 si el escalón tiene pareja ON/OFF, 0 si no (ON sin OFF, p. ej. al final del día)
      log_on_s    log10(1 + segundos entre el ON y su OFF)
      log_span_s  log10(1 + segundos de toda la sesión: primer ON a último OFF)
      log_cycles  log10(nº de ciclos de la sesión; 0 si es uno)
      duty        fracción del tiempo de la sesión con el aparato encendido
      log_energy_wh  log10(1 + Wh de la sesión)
    La sesión agrupa los pares de la misma magnitud (±group_tol) separados por menos de
    max_gap_s: el termostato de un horno o estufa, el compresor de una nevera, un hervidor
    encendido varias veces seguidas.
    """
    out = np.zeros((len(steps), len(SESSION_FEATURE_NAMES)))
    pairs = pair_steps(steps, tol)
    cycles = []                       # (t_on, t_off, delta, i_on, i_off)
    for i_on, i_off in pairs:
        t0, t1 = steps[i_on][0], steps[i_off][0]
        cycles.append((t0, t1, (steps[i_on][1] + abs(steps[i_off][1])) / 2, i_on, i_off))
    cycles.sort()
    groups = []
    for c in cycles:
        best = None
        for g in groups:
            last = g[-1]
            if (abs(c[2] - last[2]) <= group_tol * max(c[2], last[2])
                    and last[1] <= c[0] <= last[1] + max_gap_s
                    and (best is None or last[1] > best[-1][1])):
                best = g
        if best is None:
            groups.append([c])
        else:
            best.append(c)
    for g in groups:
        span = g[-1][1] - g[0][0]
        on_time = sum(c[1] - c[0] for c in g)
        energy_wh = sum(c[2] * (c[1] - c[0]) for c in g) / 3600.0
        duty = on_time / span if span > 0 else 1.0
        for c in g:
            row = [1.0, np.log10(1 + c[1] - c[0]), np.log10(1 + span), np.log10(len(g)),
                   min(duty, 1.0), np.log10(1 + energy_wh)]
            out[c[3]] = row
            out[c[4]] = row
    return out


def select_features(df, use_session=True):
    """Columnas a usar: las de escalón y, si TODOS los CSV las traen completas, las de sesión."""
    if use_session and all(c in df.columns for c in SESSION_FEATURE_NAMES) \
            and df[SESSION_FEATURE_NAMES].notna().all().all():
        return ALL_FEATURE_NAMES
    return FEATURE_NAMES


def domoboi_window(readings, delta_p, dur_s=None, k=5):
    """
    Ventana de un Measurement de DOMOBOI en el formato de los datasets públicos (1 Hz):
    [nivel previo, medias de 1 s]. El nivel previo = nivel final - ΔP, porque el enchufe
    promedia por intervalo y la ventana suele empezar a mitad de la transición.
    """
    w = np.asarray(readings, dtype=float)
    if w.size < 3:
        return w
    base = float(np.median(w[-k:])) - float(delta_p)
    dur = dur_s if dur_s and dur_s > 0 else w.size / 10.0
    n = max(2, int(round(dur)))
    return np.array([base] + [c.mean() for c in np.array_split(w, min(n, w.size))])


def event_features(readings, delta_p):
    """
    readings : muestras de potencia (W) de la ventana, en orden temporal.
    delta_p  : salto de potencia del evento (W, con signo).
    Devuelve un vector con FEATURE_NAMES, o None si el evento no es usable.
    """
    w = np.asarray(readings, dtype=float)
    if w.size < 3 or not np.all(np.isfinite(w)) or delta_p is None:
        return None
    d = float(delta_p)
    ad = abs(d)
    if ad < _EPS:
        return None
    s = 1.0 if d > 0 else -1.0

    # forma normalizada: 0 = nivel de partida, 1 = salto completo (en la dirección del evento)
    nw = s * (w - w[0]) / ad
    t_old = np.linspace(0.0, 1.0, w.size)
    t_new = np.linspace(0.0, 1.0, SHAPE_POINTS)
    shape = np.interp(t_new, t_old, nw)

    steps = np.diff(w) * s
    return np.concatenate([
        [
            np.log10(ad),
            s,
            (w.max() - w.min()) / ad,          # rango relativo al salto
            w.std() / ad,                      # rizado relativo
            max(nw.max() - 1.0, 0.0),          # pico por encima del nuevo nivel (arranque)
            max(-nw.min(), 0.0),               # caída previa al salto
            float(np.mean(steps >= -0.02 * ad)),  # fracción de pasos "hacia el salto"
        ],
        shape,
    ])
