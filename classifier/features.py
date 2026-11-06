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
    "WASHING MACHINE", "IRON", "LIGHTS", "TV", "OTHER",
]

# Nº de puntos de la forma normalizada (independiente de la frecuencia de muestreo)
SHAPE_POINTS = 8

FEATURE_NAMES = (
    ["log_abs_delta", "sign", "rng_rel", "std_rel", "overshoot", "undershoot", "monotonic"]
    + [f"shape_{i}" for i in range(SHAPE_POINTS)]
)

_EPS = 1e-6


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
