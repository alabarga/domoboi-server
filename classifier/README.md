# Prototipo de clasificador de eventos (TFM · DOMOBOI)

Clasifica un **evento** (salto de potencia ΔP + forma de la ventana de ~3 s) en un tipo de
aparato: `FRIDGE, OVEN, KETTLE, MICROWAVE, WASHING MACHINE, IRON, LIGHTS, TV, OTHER`
(las mismas clases que el desplegable de etiquetas de la app `reconstruction`).

Es un prototipo para **montar y validar el pipeline mientras no hay etiquetas propias**.
Los números de abajo son de TraceBase (aparatos alemanes sueltos, ~1 muestra/s) y no
predicen el rendimiento sobre datos de DOMOBOI.

## Piezas

| Archivo | Qué hace |
|---|---|
| `features.py` | `event_features(readings, delta_p)`: características que **valen igual** para datos públicos y de DOMOBOI (solo forma de la ventana y ΔP; sin nivel absoluto en W, porque en una acometida incluye todas las demás cargas). Independientes de la frecuencia de muestreo. |
| `build_tracebase_events.py` | Detecta escalones en las trazas de TraceBase, corta la ventana y calcula las características. |
| `train_baseline.py` | RandomForest y HistGradientBoosting, validados **por dispositivo físico** (el aparato de prueba no se ha visto al entrenar). |

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
