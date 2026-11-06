"""
Línea base: clasifica eventos (ΔP + forma de la ventana) en tipos de aparato.

Valida AGRUPANDO por dispositivo físico: el aparato del conjunto de prueba nunca
se ha visto en el entrenamiento, que es lo que pasará con un aparato nuevo.

Uso:
    python classifier/train_baseline.py --events classifier/out/tracebase_events.csv
    python classifier/train_baseline.py --events classifier/out/tracebase_events.csv classifier/out/eco_events.csv
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix, f1_score
from sklearn.model_selection import StratifiedGroupKFold

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from features import CLASSES, FEATURE_NAMES  # noqa: E402


def balance(df, per_class, seed):
    """Limita eventos por clase (TraceBase tiene miles de nevera y lavadora, pocos de tetera)
    y reparte el cupo entre dispositivos para no memorizar uno solo."""
    parts = []
    rng = np.random.RandomState(seed)
    for cls, g in df.groupby("cls"):
        if len(g) <= per_class:
            parts.append(g)
            continue
        devs = g["device"].unique()
        quota = max(per_class // len(devs), 1)
        sub = [d.sample(min(len(d), quota), random_state=rng) for _, d in g.groupby("device")]
        parts.append(pd.concat(sub))
    return pd.concat(parts).reset_index(drop=True)


def make_models(seed):
    return {
        "RandomForest": RandomForestClassifier(
            n_estimators=300, min_samples_leaf=2, class_weight="balanced_subsample",
            n_jobs=-1, random_state=seed),
        "HistGradientBoosting": HistGradientBoosting(seed),
    }


def HistGradientBoosting(seed):
    return HistGradientBoostingClassifier(max_iter=200, learning_rate=0.08,
                                          class_weight="balanced", random_state=seed)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True, nargs="+",
                    help="uno o varios CSV de eventos (p. ej. TraceBase y ECO juntos)")
    ap.add_argument("--per-class", type=int, default=4000, help="máx. eventos por clase")
    ap.add_argument("--min-abs-delta", type=float, default=0.0,
                    help="descarta eventos con |ΔP| menor (W); imita el umbral de detección del dispositivo")
    ap.add_argument("--min-devices", type=int, default=3,
                    help="excluye las clases con menos dispositivos distintos: no se pueden validar "
                         "por dispositivo (si solo hay uno, nunca está en el entrenamiento y en la prueba a la vez)")
    ap.add_argument("--delta-only", action="store_true", help="usa solo ΔP (referencia mínima)")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--save", default=None, help="ruta para guardar el modelo (joblib); si se omite, no se guarda")
    a = ap.parse_args()

    df = pd.concat([pd.read_csv(p) for p in a.events], ignore_index=True)
    df = df[df["cls"].isin(CLASSES)]
    df = df[df["delta_p"].abs() >= a.min_abs_delta]
    ndev = df.groupby("cls")["device"].nunique()
    few = [c for c, n in ndev.items() if n < a.min_devices]
    if few:
        print(f"Clases sin dispositivos suficientes para validar por dispositivo (se excluyen): {few}")
        df = df[~df["cls"].isin(few)]
    df = balance(df, a.per_class, a.seed)
    print("Eventos usados por clase:\n" + df["cls"].value_counts().to_string())
    print("\nDispositivos por clase:\n" + df.groupby("cls")["device"].nunique().to_string())

    feats = ["log_abs_delta", "sign"] if a.delta_only else FEATURE_NAMES
    X = df[feats].to_numpy()
    y = df["cls"].to_numpy()
    groups = df["device"].to_numpy()
    labels = [c for c in CLASSES if c in set(y)]

    cv = StratifiedGroupKFold(n_splits=a.folds, shuffle=True, random_state=a.seed)
    results = {}
    for name, model in make_models(a.seed).items():
        pred = np.empty(len(y), dtype=object)
        for tr, te in cv.split(X, y, groups):
            model.fit(X[tr], y[tr])
            pred[te] = model.predict(X[te])
        f1 = f1_score(y, pred, average="macro", labels=labels)
        results[name] = (f1, pred)
        print(f"\n=== {name}: F1 macro (validación por dispositivo) = {f1:.3f} ===")

    best = max(results, key=lambda k: results[k][0])
    pred = results[best][1]
    print(f"\n##### Mejor: {best}\n")
    print(classification_report(y, pred, labels=labels, digits=3, zero_division=0))
    cm = pd.DataFrame(confusion_matrix(y, pred, labels=labels), index=labels, columns=labels)
    print("Matriz de confusión (filas = real, columnas = predicho):\n" + cm.to_string())

    # modelo final con todos los datos + importancia de características
    final = make_models(a.seed)["RandomForest"].fit(X, y)
    imp = pd.Series(final.feature_importances_, index=feats).sort_values(ascending=False)
    print("\nImportancia de características (RandomForest):\n" + imp.head(8).round(3).to_string())
    if not a.save:
        return
    try:
        import joblib
        os.makedirs(os.path.dirname(a.save) or ".", exist_ok=True)
        joblib.dump({"model": final, "features": feats, "classes": list(final.classes_)}, a.save)
        print(f"\nModelo guardado en {a.save}")
    except ImportError:
        pass


if __name__ == "__main__":
    main()
