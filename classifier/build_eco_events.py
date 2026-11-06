"""
Extrae eventos de ECO (consumo agregado + enchufes, 1 Hz, Suiza 230 V) y los
etiqueta automáticamente con los enchufes. Mismo formato de salida que
build_tracebase_events.py, así que train_baseline.py lo lee sin cambios.

Idea (es la misma que se usará luego con DOMOBOI + enchufes Tuya):
  1. se detectan los escalones de potencia (ΔP) en el consumo AGREGADO de la casa;
  2. para cada escalón se busca qué enchufe tuvo, en el mismo segundo (±tol),
     un escalón del mismo signo y de tamaño parecido;
  3. el escalón del agregado se etiqueta con el aparato de ese enchufe, y las
     características se calculan con la ventana del AGREGADO (la señal que
     tendrá el clasificador en producción), no con la del enchufe.
Los escalones sin pareja (cargas sin enchufe) o ambiguos se descartan: no se
etiquetan como OTHER, porque en una casa real hay cargas que no se miden.

Entrada: carpeta con, por casa NN, los ficheros descargados de ECO:
    NN_sm_csv.zip   NN_plugs_csv.zip   NN_doc.txt
(también vale si los zips están descomprimidos en carpetas con ese nombre).

Uso:
    python classifier/build_eco_events.py --eco C:\\datasets\\eco --out classifier/out/eco_events.csv
"""
import argparse
import io
import math
import os
import re
import sys
import zipfile
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from features import FEATURE_NAMES, event_features  # noqa: E402
from build_tracebase_events import WIN_AFTER, WIN_BEFORE, detect  # noqa: E402

DAY_SECONDS = 86400
DAY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.csv$")
PLUG_RE = re.compile(r"^\s*(\d+):\s*([^(]+?)\s*\(", re.M)

# Casa 1: hasta el 29/06/2012 la potencia está redondeada a 10 W (01_doc.txt)
ROUNDED_UNTIL = {"01": "2012-06-29"}

# Emparejamiento de escalones
PLUG_THR_FACTOR = 0.5      # umbral de detección en el enchufe = factor * umbral del agregado
RATIO_MIN, RATIO_MAX = 0.5, 1.5   # ΔP_enchufe / ΔP_agregado aceptado
AMBIGUITY_GAP = 0.15       # si dos enchufes puntúan casi igual (log-ratio), se descarta


def appliance_class(house, plug, name, entertainment_as_tv):
    """Clase de la app (EventLabel) para un enchufe; None = no usar el enchufe."""
    n = name.lower()
    if house == "02" and plug == "05":
        return None            # suma de los enchufes 11 (TV) y 12 (equipo de música): se contaría doble
    if "fridge" in n or "freezer" in n:
        return "FRIDGE"
    if "kettle" in n:
        return "KETTLE"
    if "microwave" in n:
        return "MICROWAVE"
    if "washing" in n:
        return "WASHING MACHINE"
    if "stove" in n:
        return "OVEN"
    if "lamp" in n:
        return "LIGHTS"
    if n == "tv":
        return "TV"
    if "entertainment" in n:
        return "TV" if entertainment_as_tv else None   # TV + equipo de música mezclados
    return "OTHER"


# ---------------------------------------------------------------- lectura de zips / carpetas

def _members(path):
    if os.path.isdir(path):
        for root, _d, files in os.walk(path):
            for f in files:
                yield os.path.relpath(os.path.join(root, f), path).replace("\\", "/")
    else:
        with zipfile.ZipFile(path) as z:
            yield from z.namelist()


def index_archive(path, plugs):
    """Consumo agregado -> {día: miembro}. Enchufes -> {enchufe: {día: miembro}}."""
    out = {}
    for m in _members(path):
        parts = m.split("/")
        if "__MACOSX" in parts or parts[-1].startswith("._"):
            continue
        mt = DAY_RE.match(parts[-1])
        if not mt:
            continue
        day = mt.group(1)
        if plugs:
            if len(parts) < 2 or not parts[-2].isdigit():
                continue
            out.setdefault(parts[-2].zfill(2), {})[day] = m
        else:
            out[day] = m
    return out


_ZIPS = {}   # un ZipFile por proceso


def read_series(path, member):
    """86.400 valores (1 Hz) de un día; huecos (-1) = NaN, huecos cortos rellenados."""
    try:
        if os.path.isdir(path):
            with open(os.path.join(path, member), "rb") as fh:
                raw = fh.read()
        else:
            z = _ZIPS.get(path)
            if z is None:
                z = _ZIPS[path] = zipfile.ZipFile(path)
            raw = z.read(member)
        s = pd.read_csv(io.BytesIO(raw), header=None, usecols=[0], dtype=np.float64,
                        engine="c").iloc[:, 0].to_numpy()
    except Exception:
        return None
    out = np.full(DAY_SECONDS, np.nan)
    n = min(len(s), DAY_SECONDS)
    out[:n] = s[:n]
    out[out < 0] = np.nan
    return pd.Series(out).ffill(limit=3).to_numpy()


def parse_doc(path):
    txt = open(path, encoding="utf-8", errors="replace").read()
    part = txt.split("PLUG DATA", 1)[-1].split("OCCUPANCY DATA", 1)[0]
    return {m.group(1).zfill(2): m.group(2).strip() for m in PLUG_RE.finditer(part)}


# ---------------------------------------------------------------- un día

def match_events(agg_events, plug_events, tol):
    """Devuelve (emparejados, nº ambiguos). Emparejado = (t, ΔP, enchufe, dt, ΔP_enchufe)."""
    matched, ambiguous = [], 0
    for t, d in agg_events:
        cands = []
        for plug, (tp, dp, _name, _cls) in plug_events.items():
            lo = np.searchsorted(tp, t - tol, side="left")
            hi = np.searchsorted(tp, t + tol, side="right")
            best = None
            for k in range(lo, hi):
                if dp[k] * d <= 0:
                    continue
                r = dp[k] / d
                if not (RATIO_MIN <= r <= RATIO_MAX):
                    continue
                score = abs(math.log(r)) + 0.05 * abs(int(tp[k]) - t)
                if best is None or score < best[0]:
                    best = (score, plug, int(tp[k]) - t, float(dp[k]))
            if best:
                cands.append(best)
        if not cands:
            continue
        cands.sort()
        if len(cands) > 1 and cands[1][0] - cands[0][0] < AMBIGUITY_GAP:
            ambiguous += 1
            continue
        _s, plug, dt, dpl = cands[0]
        matched.append((t, d, plug, dt, dpl))
    return matched, ambiguous


def process_day(task):
    house, day, sm_path, sm_member, plugs, min_delta, tol = task
    stats = {"agg": 0, "matched": 0, "ambiguous": 0, "offsets": []}
    agg = read_series(sm_path, sm_member)
    if agg is None:
        return [], stats
    agg_events = detect(agg, min_delta)
    stats["agg"] = len(agg_events)

    plug_events = {}
    for plug, name, cls, path, member in plugs:
        s = read_series(path, member)
        if s is None:
            continue
        evs = detect(s, PLUG_THR_FACTOR * min_delta)
        if evs:
            plug_events[plug] = (np.array([e[0] for e in evs]), np.array([e[1] for e in evs]), name, cls)

    matched, ambiguous = match_events(agg_events, plug_events, tol)
    stats["ambiguous"] = ambiguous

    day0 = datetime.strptime(day, "%Y-%m-%d")
    rows = []
    for t, d, plug, dt, dpl in matched:
        w = agg[t - WIN_BEFORE: t + WIN_AFTER + 1]
        f = event_features(w, d)
        if f is None:
            continue
        _tp, _dp, name, cls = plug_events[plug]
        rows.append([cls, name, f"{house}:{plug}:{name}", day,
                     str(day0 + timedelta(seconds=t)), d] + list(f) + [house, dt, dpl])
        stats["offsets"].append(dt)
    stats["matched"] = len(rows)
    return rows, stats


# ---------------------------------------------------------------- main

def find_source(eco_dir, prefix):
    for name in sorted(os.listdir(eco_dir)):
        base = name[:-4] if name.lower().endswith(".zip") else name
        if base == prefix:
            return os.path.join(eco_dir, name)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eco", required=True, help="carpeta con NN_sm_csv(.zip), NN_plugs_csv(.zip) y NN_doc.txt")
    ap.add_argument("--out", default="classifier/out/eco_events.csv")
    ap.add_argument("--houses", nargs="*", default=None, help="p. ej. 01 02 05 (por defecto, todas las que tengan doc)")
    ap.add_argument("--min-delta", type=float, default=20.0, help="umbral de |ΔP| en W (agregado)")
    ap.add_argument("--tol", type=int, default=2, help="tolerancia temporal agregado/enchufe, en segundos")
    ap.add_argument("--workers", type=int, default=max((os.cpu_count() or 2) - 1, 1))
    ap.add_argument("--max-days", type=int, default=0, help="0 = todos los días (usar un nº pequeño para pruebas)")
    ap.add_argument("--keep-rounded", action="store_true", help="no excluir los días con potencia redondeada a 10 W")
    ap.add_argument("--no-entertainment", action="store_true", help="no usar los enchufes 'Entertainment' (TV + equipo de música) como TV")
    ap.add_argument("--exclude-other", action="store_true", help="no incluir eventos de aparatos fuera de las clases (clase OTHER)")
    a = ap.parse_args()

    docs = sorted(f for f in os.listdir(a.eco) if re.match(r"^\d{2}_doc\.txt$", f))
    houses = a.houses or [f[:2] for f in docs]
    tasks = []
    for h in houses:
        doc = os.path.join(a.eco, f"{h}_doc.txt")
        sm_path = find_source(a.eco, f"{h}_sm_csv")
        pl_path = find_source(a.eco, f"{h}_plugs_csv")
        if not (os.path.isfile(doc) and sm_path and pl_path):
            print(f"Casa {h}: faltan ficheros (doc, sm_csv o plugs_csv); se omite", flush=True)
            continue
        names = parse_doc(doc)
        sm_idx = index_archive(sm_path, plugs=False)
        pl_idx = index_archive(pl_path, plugs=True)
        usable = {}
        for plug, name in names.items():
            cls = appliance_class(h, plug, name, not a.no_entertainment)
            if cls is None or (cls == "OTHER" and a.exclude_other) or plug not in pl_idx:
                continue
            usable[plug] = (name, cls)
        print(f"Casa {h}: {len(sm_idx)} días de agregado; enchufes usados: " +
              ", ".join(f"{p}={n}→{c}" for p, (n, c) in sorted(usable.items())), flush=True)

        days = sorted(sm_idx)
        if not a.keep_rounded and h in ROUNDED_UNTIL:
            days = [d for d in days if d > ROUNDED_UNTIL[h]]
        if a.max_days:
            days = days[: a.max_days]
        for day in days:
            plugs = [(p, n, c, pl_path, pl_idx[p][day]) for p, (n, c) in sorted(usable.items()) if day in pl_idx[p]]
            if plugs:
                tasks.append((h, day, sm_path, sm_idx[day], plugs, a.min_delta, a.tol))
    if not tasks:
        sys.exit("No hay nada que procesar: revisa la carpeta --eco")
    print(f"{len(tasks)} días a procesar con {a.workers} procesos", flush=True)

    rows, tot = [], {"agg": 0, "matched": 0, "ambiguous": 0}
    offsets = []
    with ProcessPoolExecutor(a.workers) as ex:
        for i, (r, st) in enumerate(ex.map(process_day, tasks, chunksize=2), 1):
            rows.extend(r)
            for k in tot:
                tot[k] += st[k]
            offsets.extend(st["offsets"])
            if i % 20 == 0 or i == len(tasks):
                print(f"  {i}/{len(tasks)} días, {len(rows)} eventos etiquetados", flush=True)

    cols = ["cls", "appliance", "device", "file", "t", "delta_p"] + FEATURE_NAMES + ["house", "match_dt", "plug_delta"]
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    out = pd.DataFrame(rows, columns=cols)
    out.to_csv(a.out, index=False)

    print(f"\nEscalones en el agregado: {tot['agg']}  |  emparejados: {tot['matched']}"
          f"  |  ambiguos descartados: {tot['ambiguous']}"
          f"  |  sin pareja (cargas sin enchufe, descartados): {tot['agg'] - tot['matched'] - tot['ambiguous']}")
    if offsets:
        vals, cnt = np.unique(offsets, return_counts=True)
        print("Desfase enchufe-agregado (s: nº de eventos): " + ", ".join(f"{v}: {c}" for v, c in zip(vals, cnt)))
    print(f"\n{len(out)} eventos -> {a.out}")
    print(out["cls"].value_counts().to_string())
    print("\nEventos por dispositivo:\n" + out["device"].value_counts().to_string())


if __name__ == "__main__":
    main()
