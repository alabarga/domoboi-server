# Prototipo de clasificador de eventos (TFM · DOMOBOI)

Clasifica un **evento** (salto de potencia ΔP + forma de la ventana de ~3 s) en un tipo de
aparato: `FRIDGE, OVEN, KETTLE, MICROWAVE, WASHING MACHINE, IRON, LIGHTS, TV, COOKTOP, HEATER, WATER_HEATER, OTHER`
(las mismas clases que el desplegable de etiquetas de la app `reconstruction`).

Es un prototipo para **montar y validar el pipeline mientras no hay etiquetas propias**.
Los números de abajo son de TraceBase (aparatos alemanes sueltos, ~1 muestra/s) y no
predicen el rendimiento sobre datos de DOMOBOI.

## Piezas

| Archivo | Qué hace |
|---|---|
| `features.py` | `event_features(readings, delta_p)`: características que **valen igual** para datos públicos y de DOMOBOI (sin nivel absoluto en W, porque en una acometida incluye todas las demás cargas). Dos grupos: **escalón** (ΔP y forma de la ventana; `rng_rel` y `std_rel` dependen de cuántas muestras previas y posteriores tiene la ventana, por eso la ventana de DOMOBOI se lleva a 1 Hz con `domoboi_window`) y **sesión** (ver abajo). |
| `build_tracebase_events.py` | Detecta escalones en las trazas de TraceBase, corta la ventana y calcula las características. |
| `train_baseline.py` | RandomForest y HistGradientBoosting, validados **por dispositivo físico** (el aparato de prueba no se ha visto al entrenar). |

## Características de sesión

Con solo el escalón, aparatos de potencia parecida (hervidor, microondas, cafetera, ~1 kW) se confunden:
en la prueba entre datasets, la forma de la ventana mejoraba la F1 macro solo +0,04/+0,08 sobre usar
únicamente ΔP. Lo que los separa es el comportamiento en el tiempo, que `features.py` calcula
emparejando cada ON con su OFF (misma lógica que `nilm.detection.pair_steps` de producción) y
agrupando ciclos repetidos en sesiones (`session_features`):

| Característica | Qué mide |
|---|---|
| `paired` | 1 si el escalón tiene pareja ON/OFF |
| `log_on_s` | duración del ciclo (log10 de 1 + s) |
| `log_span_s` | duración de toda la sesión |
| `log_cycles` | nº de ciclos (termostato, repeticiones) |
| `duty` | fracción de la sesión encendido |
| `log_energy_wh` | energía de la sesión |

En ECO se calculan sobre todos los escalones del agregado (como en producción); en TraceBase, sobre la
traza del aparato; en DOMOBOI, con todas las medidas del dispositivo alrededor del evento etiquetado.
`train_baseline.py`, `eval_cross_dataset.py` y `check_on_domoboi.py` las usan si todos los CSV las
traen; con `--no-session` se desactivan para comparar. **Hay que regenerar los CSV** (los antiguos
siguen funcionando, pero solo con las 15 características de escalón).

## Uso

```powershell
pip install scikit-learn pandas numpy joblib
# TraceBase (~3 GB) FUERA del repo del proyecto:
git clone --depth 1 https://github.com/areinhardt/tracebase.git C:\datasets\tracebase

python classifier/build_tracebase_events.py --tracebase C:\datasets\tracebase --out classifier/out/tracebase_events.csv
python classifier/train_baseline.py --events classifier/out/tracebase_events.csv
```
La extracción tarda ~4 min con 2 núcleos; el entrenamiento, menos de 1 min.

## Resultados de referencia (TraceBase, validación por dispositivo)

HistGradientBoosting: **F1 macro 0,47** (RandomForest 0,44). Por clase: lavadora 0,69 ·
nevera 0,59 · tetera 0,63 · microondas 0,58 · TV 0,46 · OTHER 0,29 · luces 0,06.

- **Casi todo lo discrimina ΔP** (importancia ~0,5). La forma de la ventana aporta algo, pero con ~4 muestras por evento a 1 Hz hay poca.
- **TV y OTHER se confunden mucho** (saltos pequeños parecidos) y **LIGHTS no se aprende** (84 eventos).
- **IRON y OVEN se excluyen de la validación**: en TraceBase solo hay **un** dispositivo de cada uno, y la validación por dispositivo exige varios.
- Con umbrales de |ΔP| mayores (`--min-abs-delta 50/100`) el F1 macro *baja* (0,41/0,38), sobre todo por quedarse sin eventos de TV y luces.

## Limitaciones a tener presentes

1. **Brecha de dominio**: ~1 Hz y aparatos aislados (TraceBase) frente a ~10 Hz y acometida con varias cargas (DOMOBOI). Esto es una línea base del pipeline, no una estimación del rendimiento real.
2. `OVEN` se alimenta de *Cookingstove* (lo más cercano que hay) y `OTHER` agrupa todo lo demás de TraceBase.
3. Para medir de verdad hacen falta **tus eventos etiquetados**.

## Siguientes pasos

1. Etiquetar eventos en la app `reconstruction` (decenas por clase como mínimo; de `OTHER` también).
2. Exportar los eventos etiquetados con `event_features(Measurement.readings, Measurement.value)` y entrenar/validar con ellos (por dispositivo o por día, nunca mezclando eventos próximos).
3. Usar TraceBase/SynD solo como datos extra de entrenamiento, y comprobar que **mejoran** la validación sobre datos propios.
4. Después, probar una CNN 1D sobre las muestras crudas.
