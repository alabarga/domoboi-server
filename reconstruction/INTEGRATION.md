# Integración — app `reconstruction`

App Django nueva, aislada del código existente: no toca ni modifica `nilm/`
(ni sus modelos, vistas ni migraciones). Solo lee `Device` y `Measurement`,
y añade su propia tabla `EventLabel` para el etiquetado manual.

## 1. Copiar la carpeta

Copia la carpeta `reconstruction/` a la raíz del proyecto, al mismo nivel
que `nilm/`:

```
domoboi-server/
├── domoboi/
├── nilm/
├── reconstruction/   ← nueva
├── manage.py
...
```

## 2. Registrar la app (`domoboi/settings.py`)

```python
INSTALLED_APPS = [
    ...
    'nilm',
    'reconstruction',   # ← añadir esta línea
]
```

## 3. Registrar las URLs (`domoboi/urls.py`)

```python
urlpatterns = [
    path('admin/', admin.site.urls),
    path('nilm/', include('nilm.urls')),
    path('reconstruction/', include('reconstruction.urls')),  # ← añadir
    ...
]
```

## 4. Migrar

```bash
python manage.py migrate
```

Crea únicamente la tabla `reconstruction_eventlabel` (FK 1-a-1 a
`nilm.Measurement`). La migración depende de `nilm.0008` (la última
migración de `nilm` en el repo en el momento de escribir esto) — si tu
copia tiene migraciones más recientes de `nilm`, Django lo detecta solo al
migrar; no hace falta tocar nada.

## 5. Acceder

```
https://tu-servidor/reconstruction/
```

Restringido a usuarios `is_staff` o `is_superuser` (mismo patrón que las
vistas de `nilm`). Si quieres dar acceso a un cuidador/usuario normal,
cambia `StaffRequiredMixin.test_func` en `reconstruction/views.py`.

## Qué hace cada pantalla

- **`/reconstruction/`** — lista de dispositivos registrados (o escribe un
  `device_id` directamente). Funciona igual para dispositivos DOMOBOI y TUYA.
- **`/reconstruction/<device_id>/`** — para el dispositivo elegido:
  - **Calendario** (mapa de calor mensual) — nº de muestras/eventos por
    día. Clic en un día lo selecciona.
  - **Señal reconstruida** del día seleccionado (gráfico Chart.js).
  - **Lista de eventos/muestras** de ese día, con un desplegable para
    **etiquetar manualmente** el electrodoméstico de cada uno (se guarda
    al vuelo vía HTMX, sin recargar la página).

## DOMOBOI vs. TUYA — se tratan distinto (importante)

La app detecta el tipo por `Device.device_type` y cambia tanto la
reconstrucción como la lista:

| | DOMOBOI (edge NILM) | TUYA (enchufe cloud) |
|---|---|---|
| Qué envía cada `Measurement` | ΔP de una transición + ventana real de ~3s (`nilm_processor.py`) | Potencia absoluta instantánea, muestreada por polling (`tuya_daemon.py`, cada ~60s) |
| `value` | Delta (con signo) | Potencia absoluta en ese instante |
| `features` | `{avg, min, max, std}` del post-evento | Vacío (`None`) |
| Reconstrucción | Escalones: nivel constante entre eventos + forma real durante cada transición | Se unen las muestras reales consecutivas con una línea recta — ya es la serie real, no hay "hueco" que rellenar |
| Lista de eventos | ΔP, dirección ON/OFF, features | Potencia (W), Δ vs. muestra anterior, y voltage/current/energy si el `telemetry` los trae |

**Importante para tu TFM**: si un enchufe Tuya está dedicado a un único
electrodoméstico (p. ej. "Enchufe Tuya - Nevera"), sus medidas ya son
*ground truth* real por aparato — sin necesidad de etiquetar a mano. Es la
vía más rápida para conseguir una primera muestra fiable con la que
comprobar si los eventos DOMOBOI (agregados, en la misma vivienda) son
separables por aparato: compara la marca de tiempo de un salto de ΔP en
DOMOBOI con los cambios de potencia del Tuya correspondiente en la misma
franja horaria.

## Cómo se reconstruye la señal (`reconstruction/signal.py`)

El edge (`nilm_processor.py`) no transmite la serie continua: solo envía
una ventana de ~3s (30 muestras a 10Hz) alrededor de cada transición
ON/OFF. La reconstrucción, por tanto:

- **Entre eventos**: dibuja un valor constante = último nivel post-evento
  conocido (`Measurement.features['avg']`). No se interpola ni se inventa
  nada en los huecos — es fiel a lo realmente medido.
- **Durante cada evento**: usa la forma real capturada (`readings` crudos),
  repartida a lo largo de `start_time`..`end_time`.
- El nivel de partida del día se hereda del último evento del día anterior
  (si existe), para no arrancar siempre en 0W.

## Etiquetado manual → dataset para tu clasificador

Cada etiqueta se guarda en `EventLabel` (uno por `Measurement`, sin tocar
el modelo original). Para exportar el dataset etiquetado a CSV/Parquet
para entrenar tu clasificador:

```python
from reconstruction.models import EventLabel

for el in EventLabel.objects.select_related('measurement').all():
    m = el.measurement
    row = {
        "device_id": m.device.device_id,
        "start_time": m.start_time,
        "end_time": m.end_time,
        "delta_p": m.value,
        "readings": m.readings,
        "avg": m.features.get("avg") if m.features else None,
        "min": m.features.get("min") if m.features else None,
        "max": m.features.get("max") if m.features else None,
        "std": m.features.get("std") if m.features else None,
        "appliance_type": el.appliance_type,
    }
```

Puedo prepararte este export (management command o script) en cuanto
tengas suficientes eventos etiquetados.

## Nota sobre `USE_TZ`

El proyecto usa `USE_TZ = True` con `TIME_ZONE = 'Europe/Madrid'`: la BD
guarda instantes UTC y las plantillas, el calendario y la reconstrucción
los muestran y agrupan por día en hora de Madrid. `signal.py` y `views.py`
conservan una rama para `USE_TZ = False` (datetimes naive) solo por
compatibilidad.

Los DOMOBOI y los Tuya están alineados en el tiempo (comprobado contra
producción el 2026-10-05). Los datos de la API que lleguen sin zona horaria se
interpretan como hora de Madrid, así que el edge debe enviar ISO-8601 con
zona (`+02:00` o `Z`) o hora local de Madrid.

## Probado

Se ha probado end-to-end con datos sintéticos (5 eventos ON/OFF a lo
largo de un día) contra una base SQLite: render de las 3 vistas, guardado
y borrado de etiquetas vía POST, navegación HTMX entre meses/días, y
cálculo del calendario y la reconstrucción. No se ha probado contra
Postgres ni con datos reales del piloto.
