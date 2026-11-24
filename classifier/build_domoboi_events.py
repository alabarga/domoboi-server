"""
Exporta los eventos DOMOBOI etiquetados en la app `reconstruction` (EventLabel) a un CSV
con el mismo formato que build_tracebase_events.py / build_eco_events.py, para poder
usarlos con train_baseline.py y eval_cross_dataset.py.

Cada fila es un evento: clase, ΔP (Measurement.value), características de la ventana
(Measurement.readings), características de sesión (duración, ciclos, duty, energía) y datos
para validar sin mezclar eventos próximos (device, day).

Las características de sesión se calculan emparejando los ON/OFF con TODAS las medidas del
dispositivo alrededor del evento (no solo las etiquetadas): un hervidor etiquetado como
+1000 W necesita encontrar su −1000 W aunque ese no esté etiquetado.

Uso (desde la raíz del repo, con el entorno virtual activo):
    python classifier/build_domoboi_events.py --out classifier/out/domoboi_events.csv
    python classifier/build_domoboi_events.py --device-id domoboi-21 --since 2026-10-28
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "domoboi.settings")

import django  # noqa: E402

django.setup()

import pandas as pd  # noqa: E402
from django.utils import timezone  # noqa: E402

from features import (CLASSES, FEATURE_NAMES, MAX_ON_S, SESSION_FEATURE_NAMES,  # noqa: E402
                      domoboi_window, event_features, session_features)
from nilm.models import Measurement  # noqa: E402
from reconstruction.models import EventLabel  # noqa: E402

MIN_STEP_W = 40                       # como detect_events: saltos menores no cuentan como uso
MARGIN = timedelta(seconds=MAX_ON_S)  # un ON puede quedar abierto hasta 4 h


def session_rows(device, labels):
    """{pk de Measurement: fila de características de sesión} para las etiquetas de un dispositivo y día."""
    ts = [lab.measurement.start_time or lab.measurement.timestamp for lab in labels]
    lo, hi = min(ts) - MARGIN, max(ts) + MARGIN
    keep = {lab.measurement_id for lab in labels}
    steps, pks = [], []
    qs = (Measurement.objects.filter(device=device, start_time__gte=lo, start_time__lte=hi)
          .order_by("start_time").values_list("pk", "start_time", "value"))
    for pk, t, v in qs:
        if t is None or v is None:
            continue
        v = float(v)
        if abs(v) >= MIN_STEP_W or pk in keep:
            steps.append((t.timestamp(), v))
            pks.append(pk)
    return dict(zip(pks, session_features(steps)))


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

    by_day = defaultdict(list)
    for lab in qs:
        m = lab.measurement
        t = timezone.localtime(m.start_time or m.timestamp)
        by_day[(m.device_id, t.date())].append(lab)

    rows, skipped = [], {"sin readings/ΔP": 0}
    for (_dev_pk, day), labels in by_day.items():
        sess = session_rows(labels[0].measurement.device, labels)
        for lab in labels:
            m = lab.measurement
            dur = (m.end_time - m.start_time).total_seconds() if m.start_time and m.end_time else None
            f = event_features(domoboi_window(m.readings, float(m.value), dur), float(m.value))
            if f is None:
                skipped["sin readings/ΔP"] += 1
                continue
            t = timezone.localtime(m.start_time or m.timestamp)
            dev = m.device.device_id if m.device else "?"
            s = sess.get(m.pk, [0.0] * len(SESSION_FEATURE_NAMES))
            rows.append([
                lab.appliance_type, lab.appliance_type, f"domoboi:{dev}:{lab.appliance_type}",
                f"measurement:{m.pk}", t.isoformat(), float(m.value), *f, *s, "domoboi", day.isoformat(),
            ])

    cols = (["cls", "appliance", "device", "file", "t", "delta_p"] + FEATURE_NAMES
            + SESSION_FEATURE_NAMES + ["house", "day"])
    out = pd.DataFrame(rows, columns=cols).sort_values("t").reset_index(drop=True)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    out.to_csv(a.out, index=False)

    print(f"{len(out)} eventos etiquetados exportados a {a.out}  (omitidos: {skipped})")
    if out.empty:
        return
    print("\nPor clase:\n" + out["cls"].value_counts().to_string())
    print("\nPor día y clase:\n" + out.groupby(["day", "cls"]).size().to_string())
    print(f"\nCon pareja ON/OFF: {int(out['paired'].sum())} de {len(out)} eventos")
    fuera = sorted(set(out["cls"]) - set(CLASSES))
    if fuera:
        print(f"\nAviso: {fuera} no están en features.CLASSES, así que train_baseline y "
              "eval_cross_dataset las ignorarán hasta que se añadan allí.")


if __name__ == "__main__":
    main()
