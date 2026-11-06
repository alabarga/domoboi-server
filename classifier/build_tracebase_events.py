"""
Extrae eventos tipo DOMOBOI de TraceBase (trazas de aparatos individuales,
~1 muestra/s, Alemania 230 V) y los guarda en CSV.

Uso:
    git clone --depth 1 https://github.com/areinhardt/tracebase.git  (fuera del repo del proyecto)
    python classifier/build_tracebase_events.py --tracebase RUTA/tracebase --out classifier/out/tracebase_events.csv
"""
import argparse
import glob
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from features import FEATURE_NAMES, event_features  # noqa: E402

# carpeta de TraceBase -> clase de la app (EventLabel.APPLIANCE_CHOICES)
FOLDER_TO_CLASS = {
    "Refrigerator": "FRIDGE", "Freezer": "FRIDGE",
    "Cookingstove": "OVEN",           # el más cercano a un horno/placa en TraceBase
    "WaterKettle": "KETTLE",
    "MicrowaveOven": "MICROWAVE",
    "Washingmachine": "WASHING MACHINE",
    "Iron": "IRON",
    "Lamp": "LIGHTS",
    "TV-LCD": "TV", "TV-CRT": "TV",
}
# todo lo demás de TraceBase se agrupa como OTHER (se limita su peso, ver --max-other)

PRE, POST, WIN_BEFORE, WIN_AFTER = 3, 3, 1, 2   # s: media previa/posterior, ventana de características


def load_trace(path):
    df = pd.read_csv(path, sep=";", header=None, names=["ts", "p1", "p8"],
                     usecols=[0, 1], on_bad_lines="skip", engine="c")
    df["ts"] = pd.to_datetime(df["ts"], format="%d/%m/%Y %H:%M:%S", errors="coerce")
    df = df.dropna(subset=["ts", "p1"])
    if df.empty:
        return None
    # rejilla de 1 s: media si hay duplicados, hueco corto rellenado, hueco largo = NaN
    s = df.groupby(df["ts"].dt.floor("s"))["p1"].mean()
    grid = s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="s"))
    return grid.ffill(limit=3)


def detect(p, thr):
    """Índices t de los escalones con |media(p[t:t+POST]) - media(p[t-PRE:t])| > thr."""
    n = p.size
    c = np.concatenate([[0.0], np.cumsum(np.nan_to_num(p))])
    ok = np.concatenate([[0], np.cumsum(~np.isnan(p))])
    idx = np.arange(PRE, n - POST)
    pre = (c[idx] - c[idx - PRE]) / PRE
    post = (c[idx + POST] - c[idx]) / POST
    valid = ((ok[idx] - ok[idx - PRE]) == PRE) & ((ok[idx + POST] - ok[idx]) == POST)
    d = np.where(valid, post - pre, 0.0)
    cand = np.where(np.abs(d) > thr)[0]
    events = []
    for k in cand:
        lo, hi = max(0, k - 4), min(d.size, k + 5)
        if np.abs(d[k]) >= np.abs(d[lo:hi]).max():        # supresión de no-máximos
            events.append((int(idx[k]), float(d[k])))
    return events


def process(args):
    path, appliance, cls, device, thr = args
    grid = load_trace(path)
    if grid is None:
        return []
    p = grid.to_numpy(dtype=float)
    rows = []
    for t, d in detect(p, thr):
        w = p[t - WIN_BEFORE: t + WIN_AFTER + 1]
        f = event_features(w, d)
        if f is None:
            continue
        rows.append([cls, appliance, device, os.path.basename(path), str(grid.index[t]), d] + list(f))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tracebase", required=True)
    ap.add_argument("--out", default="classifier/out/tracebase_events.csv")
    ap.add_argument("--min-delta", type=float, default=20.0, help="umbral de |ΔP| en W")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--max-files-per-folder", type=int, default=0, help="0 = todos (para pruebas rápidas)")
    a = ap.parse_args()

    jobs = []
    for folder in sorted(glob.glob(os.path.join(a.tracebase, "complete", "*"))):
        name = os.path.basename(folder)
        cls = FOLDER_TO_CLASS.get(name, "OTHER")
        files = sorted(glob.glob(os.path.join(folder, "*.csv")))
        if a.max_files_per_folder:
            files = files[: a.max_files_per_folder]
        for f in files:
            device = os.path.basename(f).split("_")[1] if "_" in os.path.basename(f) else "x"
            jobs.append((f, name, cls, f"{name}:{device}", a.min_delta))
    print(f"{len(jobs)} ficheros", flush=True)

    rows = []
    with ProcessPoolExecutor(a.workers) as ex:
        for i, r in enumerate(ex.map(process, jobs, chunksize=4), 1):
            rows.extend(r)
            if i % 200 == 0:
                print(f"  {i}/{len(jobs)} ficheros, {len(rows)} eventos", flush=True)

    cols = ["cls", "appliance", "device", "file", "t", "delta_p"] + FEATURE_NAMES
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    out = pd.DataFrame(rows, columns=cols)
    out.to_csv(a.out, index=False)
    print(f"{len(out)} eventos -> {a.out}")
    print(out["cls"].value_counts().to_string())


if __name__ == "__main__":
    main()
