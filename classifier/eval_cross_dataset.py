"""
Evaluación entre conjuntos de datos: se entrena con uno y se prueba con el otro.

    A -> B   p. ej. TraceBase -> ECO: aparatos aislados -> consumo agregado real
             (la situación de DOMOBOI: el clasificador verá una acometida completa)
    B -> A   al revés

Es la prueba más exigente: mide si lo aprendido con unos aparatos, otra casa y otra
instalación sirve con datos nuevos. Si la F1 cae mucho respecto a la validación dentro
de un mismo conjunto, hay una brecha de dominio y hará falta entrenar con datos propios.

Solo se evalúan las clases presentes en ambos conjuntos con eventos suficientes. OTHER
se excluye por defecto: no significa lo mismo en cada conjunto (en TraceBase son decenas
de aparatos; en ECO, unos pocos enchufes concretos).

Uso:
    python classifier/eval_cross_dataset.py --a classifier/out/tracebase_events.csv --b classifier/out/eco_events.csv
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix, f1_score

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from features import CLASSES, FEATURE_NAMES, select_features  # noqa: E402
from train_baseline import balance, make_models  # noqa: E402


def load(path, min_abs):
    df = pd.read_csv(path)
    df = df[df["cls"].isin(CLASSES)]
    return df[df["delta_p"].abs() >= min_abs]


def summary(tr, te, classes, na, nb):
    rows = []
    for c in classes:
        rows.append([c,
                     int((tr.cls == c).sum()), int(tr[tr.cls == c].device.nunique()),
                     int((te.cls == c).sum()), int(te[te.cls == c].device.nunique())])
    return pd.DataFrame(rows, columns=["clase", f"eventos {na}", f"aparatos {na}",
                                       f"eventos {nb}", f"aparatos {nb}"]).to_string(index=False)


def run(tr, te, na, nb, a):
    classes = [c for c in CLASSES
               if (a.include_other or c != "OTHER")
               and (tr.cls == c).sum() >= a.min_events and (te.cls == c).sum() >= a.min_events]
    print(f"\n{'=' * 78}\nENTRENA con {na}  ->  PRUEBA con {nb}\n{'=' * 78}")
    if len(classes) < 2:
        print("Menos de 2 clases comunes con eventos suficientes: no se puede evaluar.")
        return
    tr = balance(tr[tr.cls.isin(classes)], a.per_class, a.seed)
    te = balance(te[te.cls.isin(classes)], a.per_class, a.seed)
    print("Clases evaluadas (tras limitar eventos por clase y repartir entre aparatos):")
    print(summary(tr, te, classes, na, nb))
    print(f"\nAzar (clase al azar) ~ F1 macro {1 / len(classes):.2f}")

    feats = select_features(pd.concat([tr, te]), not a.no_session)
    print(f"Características usadas ({len(feats)}): " + ", ".join(feats))
    ytr, yte = tr["cls"].to_numpy(), te["cls"].to_numpy()
    results = {}
    for name, model in make_models(a.seed).items():
        model.fit(tr[feats].to_numpy(), ytr)
        pred = model.predict(te[feats].to_numpy())
        results[name] = (f1_score(yte, pred, average="macro", labels=classes), pred)
    ref = RandomForestClassifier(n_estimators=300, min_samples_leaf=2, class_weight="balanced_subsample",
                                 n_jobs=-1, random_state=a.seed)
    ref.fit(tr[["log_abs_delta", "sign"]].to_numpy(), ytr)
    f1_delta = f1_score(yte, ref.predict(te[["log_abs_delta", "sign"]].to_numpy()), average="macro", labels=classes)

    print()
    for name, (f1, _) in results.items():
        print(f"{name:22s} F1 macro = {f1:.3f}")
    print(f"{'Solo ΔP (referencia)':22s} F1 macro = {f1_delta:.3f}")
    best = max(results, key=lambda k: results[k][0])
    pred = results[best][1]
    print(f"\n##### Mejor: {best}\n")
    print(classification_report(yte, pred, labels=classes, digits=3, zero_division=0))
    cm = pd.DataFrame(confusion_matrix(yte, pred, labels=classes), index=classes, columns=classes)
    print("Matriz de confusión (filas = real, columnas = predicho):\n" + cm.to_string())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="CSV de eventos del conjunto A (p. ej. TraceBase)")
    ap.add_argument("--b", required=True, help="CSV de eventos del conjunto B (p. ej. ECO)")
    ap.add_argument("--name-a", default=None)
    ap.add_argument("--name-b", default=None)
    ap.add_argument("--per-class", type=int, default=3000, help="máx. eventos por clase en cada conjunto")
    ap.add_argument("--min-events", type=int, default=30, help="eventos mínimos por clase en cada conjunto")
    ap.add_argument("--min-abs-delta", type=float, default=0.0)
    ap.add_argument("--include-other", action="store_true", help="incluye la clase OTHER (poco comparable entre conjuntos)")
    ap.add_argument("--no-session", action="store_true", help="sin características de sesión (para comparar)")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    na = a.name_a or os.path.splitext(os.path.basename(a.a))[0].replace("_events", "")
    nb = a.name_b or os.path.splitext(os.path.basename(a.b))[0].replace("_events", "")
    da, db = load(a.a, a.min_abs_delta), load(a.b, a.min_abs_delta)
    run(da, db, na, nb, a)
    run(db, da, nb, na, a)


if __name__ == "__main__":
    main()
