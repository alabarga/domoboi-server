"""
Exporta los eventos DOMOBOI etiquetados en la app `reconstruction` (EventLabel) a un CSV
con el mismo formato que build_tracebase_events.py / build_eco_events.py, para poder
usarlos con train_baseline.py y eval_cross_dataset.py.

Cada fila es un evento: clase, ΔP (Measurement.value), características de la ventana
(Measurement.readings) y datos para validar sin mezclar eventos próximos (device, day).

Uso (desde la raíz del repo, con el entorno virtual activo):
    python classifier/build_domoboi_events.py --out classifier/out/domoboi_events.csv
    python classifier/build_domoboi_events.py --device-id domoboi-21 --since 2026-10-28
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "domoboi.settings")

import django  # noqa: E402

django.setup()

import pandas as pd  # noqa: E402
from django.utils import timezone  # noqa: E402

from features import CLASSES, FEATURE_NAMES, event_features  # noqa: E402
from reconstruction.models import EventLabel  # noqa: E402

import numpy as np

def con_base(readings, delta_p, k=5):
    """Antepone el nivel previo al salto (nivel final - ΔP) a la ventana.
    El enchufe promedia por intervalo y la ventana suele empezar a mitad
    de la transición; así la forma queda comparable con los datasets públicos."""
    w = np.asarray(readings, dtype=float)
    if w.size < 3:
        return readings
    base = float(np.median(w[-k:])) - float(delta_p)
    return np.concatenate([[base], w])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="classifier/out/domoboi_events.csv")
    ap.add_argument("--device-id", default=None, help="solo este dispositivo (Device.device_id)")
    ap.add_argument("--since", default=None, help="solo eventos desde esta fecha (AAAA-MM-DD)")
    a = ap.parse_args()

    qs = EventLabel.objects.select_related("measurement__device").order_by("measurement__start_time")
    if a.device_id:
        qs = qs.filter(measurement__device__device_id=a.device_id)
    if a.since:
        qs = qs.filter(measurement__start_time__date__gte=a.since)

    rows, skipped = [], {"sin readings/ΔP": 0}
    for lab in qs:
        m = lab.measurement
        f = event_features(con_base(m.readings, float(m.value)), float(m.value))
        if f is None:
            skipped["sin readings/ΔP"] += 1
            continue
        t = timezone.localtime(m.start_time or m.timestamp)
        dev = m.device.device_id if m.device else "?"
        rows.append([
            lab.appliance_type, lab.appliance_type, f"domoboi:{dev}:{lab.appliance_type}",
            f"measurement:{m.pk}", t.isoformat(), float(m.value), *f, "domoboi", t.date().isoformat(),
        ])

    cols = ["cls", "appliance", "device", "file", "t", "delta_p"] + FEATURE_NAMES + ["house", "day"]
    out = pd.DataFrame(rows, columns=cols)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    out.to_csv(a.out, index=False)

    print(f"{len(out)} eventos etiquetados exportados a {a.out}  (omitidos: {skipped})")
    if out.empty:
        return
    print("\nPor clase:\n" + out["cls"].value_counts().to_string())
    print("\nPor día y clase:\n" + out.groupby(["day", "cls"]).size().to_string())
    fuera = sorted(set(out["cls"]) - set(CLASSES))
    if fuera:
        print(f"\nAviso: {fuera} no están en features.CLASSES, así que train_baseline y "
              "eval_cross_dataset las ignorarán hasta que se añadan allí.")


if __name__ == "__main__":
    main()
