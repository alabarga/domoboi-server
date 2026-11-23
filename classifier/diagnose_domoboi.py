"""Compara los eventos DOMOBOI con los de cada dataset público, clase por clase."""
import sys
import numpy as np
import pandas as pd
from features import FEATURE_NAMES

CLS = sys.argv[1] if len(sys.argv) > 1 else "KETTLE"
dm = pd.read_csv("classifier/out/domoboi_events.csv")
dm = dm[dm.cls == CLS]
print(f"DOMOBOI {CLS}: {len(dm)} eventos")
print("|ΔP| W  min/mediana/max:", *np.round([dm.delta_p.abs().min(), dm.delta_p.abs().median(), dm.delta_p.abs().max()]), "\n")

for name in ("eco", "tracebase"):
    pub = pd.read_csv(f"classifier/out/{name}_events.csv")
    pub = pub[pub.cls == CLS]
    if pub.empty:
        print(f"{name}: sin eventos {CLS}\n")
        continue
    p = pub.delta_p.abs()
    print(f"{name.upper()} {CLS}: {len(pub)} eventos, {pub.device.nunique()} dispositivos")
    print("|ΔP| W  p10/mediana/p90:", *np.round(p.quantile([.1, .5, .9]).values))
    print("fracción con |ΔP| < 1200 W:", round((p < 1200).mean(), 3))
    cols = ["rng_rel", "std_rel", "overshoot", "undershoot", "monotonic"]
    print(pd.DataFrame({"DOMOBOI": dm[cols].median(), name: pub[cols].median()}).round(3), "\n")