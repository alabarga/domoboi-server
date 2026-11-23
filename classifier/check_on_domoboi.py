"""
Comprobación rápida con pocos eventos propios: entrena con conjuntos públicos y
predice los eventos etiquetados de DOMOBOI, evento a evento.

No sustituye a una evaluación (con pocos eventos y un solo aparato no hay F1 fiable);
sirve para ver si el pipeline funciona de extremo a extremo y qué confunde.

Uso:
    python classifier/check_on_domoboi.py --train classifier/out/tracebase_events.csv classifier/out/eco_events.csv --domoboi classifier/out/domoboi_events.csv
"""
import argparse
import os
import sys

import pandas as pd
from sklearn.ensemble import RandomForestClassifier

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from features import CLASSES, FEATURE_NAMES  # noqa: E402
from train_baseline import balance  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", nargs="+", required=True)
    ap.add_argument("--domoboi", required=True)
    ap.add_argument("--per-class", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    tr = pd.concat([pd.read_csv(p) for p in a.train], ignore_index=True)
    tr = balance(tr[tr["cls"].isin(CLASSES)], a.per_class, a.seed)
    te = pd.read_csv(a.domoboi)
    te = te[te["cls"].isin(CLASSES)].reset_index(drop=True)
    if te.empty:
        sys.exit("No hay eventos DOMOBOI con clases conocidas.")

    rf = RandomForestClassifier(n_estimators=300, min_samples_leaf=2, class_weight="balanced_subsample",
                                n_jobs=-1, random_state=a.seed).fit(tr[FEATURE_NAMES].to_numpy(), tr["cls"])
    proba = rf.predict_proba(te[FEATURE_NAMES].to_numpy())
    te["pred"] = rf.classes_[proba.argmax(axis=1)]
    te["conf"] = proba.max(axis=1).round(2)
    te["ok"] = te["pred"] == te["cls"]

    print(te[["t", "cls", "delta_p", "pred", "conf", "ok"]].to_string(index=False))
    print("\nAciertos por clase real:")
    print(te.groupby("cls")["ok"].agg(["sum", "count"]).rename(columns={"sum": "aciertos", "count": "eventos"}).to_string())
    print(f"\nEntrenado con {len(tr)} eventos públicos de {sorted(tr['cls'].unique())}.")


if __name__ == "__main__":
    main()
