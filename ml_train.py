import pandas as pd
import numpy as np
import joblib
from sqlalchemy import create_engine
import lightgbm as lgb

from sklearn.metrics import (
    classification_report,
    roc_auc_score,
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    confusion_matrix
)

import matplotlib.pyplot as plt

# =========================
# CONFIG
# =========================
DB_URI = "mysql+pymysql://root:@localhost/smart_dispenser_db"
WINDOW_MINUTES = 60
MODEL_PATH = "model_lgbm.pkl"

engine = create_engine(DB_URI)


# =========================
# DATASET BUILDER
# =========================
def build_dataset():
    q = """
    SELECT
        i.id AS intake_id,
        i.slot_id,
        i.scheduled_id,
        i.notification_id,
        i.scheduled_time,
        i.taken,
        i.taken_at,
        n.scheduled_datetime AS notif_scheduled_time,
        ms.medicine_id,
        s.slot_number
    FROM intake i
    LEFT JOIN notifications n ON n.id = i.notification_id
    LEFT JOIN medicine_schedule ms ON ms.id = i.scheduled_id
    LEFT JOIN slots s ON s.id = i.slot_id
    """

    df = pd.read_sql(q, engine)

    # Use notification time first if available, else fallback to intake scheduled_time
    df["scheduled_occurrence"] = df["notif_scheduled_time"].fillna(
        df["scheduled_time"]
    ).infer_objects(copy=False)

    df["scheduled_occurrence"] = pd.to_datetime(df["scheduled_occurrence"], errors="coerce")
    df["taken_at"] = pd.to_datetime(df["taken_at"], errors="coerce")

    # Keep rows with required fields
    df = df.dropna(subset=["scheduled_occurrence", "medicine_id", "slot_number"])

    # Label = missed if:
    # - not taken
    # - or taken_at is missing
    # - or taken too late beyond window
    deadline = df["scheduled_occurrence"] + pd.to_timedelta(WINDOW_MINUTES, unit="m")

    df["missed"] = np.where(
        (df["taken"] != 1) | (df["taken_at"].isna()) | (df["taken_at"] > deadline),
        1,
        0
    ).astype(int)

    # Time-based features available BEFORE the event happens
    df["sched_hour"] = df["scheduled_occurrence"].dt.hour
    df["sched_minute"] = df["scheduled_occurrence"].dt.minute
    df["sched_dow"] = df["scheduled_occurrence"].dt.dayofweek  # Monday=0

    # Sort chronologically
    df = df.sort_values("scheduled_occurrence").reset_index(drop=True)

    # Convert missed -> taken_int
    df["taken_int"] = (df["missed"] == 0).astype(int)

    # Rolling history by slot_number
    df["taken_last_7"] = (
        df.groupby("slot_number")["taken_int"]
        .rolling(window=7, min_periods=1)
        .sum()
        .reset_index(level=0, drop=True)
        .shift(1)
        .fillna(0)
    )

    df["missed_last_7"] = (
        df.groupby("slot_number")["missed"]
        .rolling(window=7, min_periods=1)
        .sum()
        .reset_index(level=0, drop=True)
        .shift(1)
        .fillna(0)
    )

    df["history_count_last_7"] = df["taken_last_7"] + df["missed_last_7"]

    df["adherence_rate_last_7"] = np.where(
        df["history_count_last_7"] > 0,
        df["taken_last_7"] / df["history_count_last_7"],
        0.0
    )

    # Final feature set
    features = [
        "slot_number",
        "sched_hour",
        "sched_minute",
        "sched_dow",
        "medicine_id",
        "taken_last_7",
        "missed_last_7",
        "adherence_rate_last_7"
    ]

    X = df[features].copy()
    y = df["missed"].astype(int).copy()

    for col in features:
        X[col] = pd.to_numeric(X[col], errors="coerce").fillna(0)

    return df, X, y, features


# =========================
# TRAIN + EVALUATE
# =========================
def train():
    df, X, y, features = build_dataset()

    if len(df) < 20:
        print("Warning: dataset is very small. Metrics may be unstable.\n")

    if y.nunique() < 2:
        raise ValueError("Training requires both classes (taken and missed).")

    # Time-aware split
    df = df.sort_values("scheduled_occurrence").reset_index(drop=True)
    split_idx = int(len(df) * 0.8)

    X_train = X.iloc[:split_idx]
    y_train = y.iloc[:split_idx]
    X_test = X.iloc[split_idx:]
    y_test = y.iloc[split_idx:]

    if len(X_train) == 0 or len(X_test) == 0:
        raise ValueError("Not enough data to split train/test.")

    model = lgb.LGBMClassifier(
        n_estimators=300,
        learning_rate=0.05,
        num_leaves=31,
        subsample=0.9,
        colsample_bytree=0.9,
        random_state=42
    )

    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]

    acc = accuracy_score(y_test, y_pred)
    prec = precision_score(y_test, y_pred, zero_division=0)
    rec = recall_score(y_test, y_pred, zero_division=0)
    f1 = f1_score(y_test, y_pred, zero_division=0)

    if len(np.unique(y_test)) == 2:
        auc = roc_auc_score(y_test, y_proba)
    else:
        auc = float("nan")

    print("\n=== Classification Report ===")
    print(classification_report(y_test, y_pred, digits=4))
    print("Accuracy :", round(acc, 4))
    print("Precision:", round(prec, 4))
    print("Recall   :", round(rec, 4))
    print("F1-score :", round(f1, 4))
    print("ROC-AUC  :", auc)

    print("\nConfusion Matrix (rows=true, cols=pred):")
    print(confusion_matrix(y_test, y_pred))

    labels = ["Accuracy", "Precision", "Recall", "F1-score"]
    scores = [acc, prec, rec, f1]

    plt.figure()
    plt.bar(labels, scores)
    plt.ylim(0, 1)
    for i, v in enumerate(scores):
        plt.text(i, v + 0.02, f"{v:.2f}", ha="center")
    plt.title("Medication Missed-Dose Risk Model Performance")
    plt.ylabel("Score")
    plt.show()

    joblib.dump(
        {
            "model": model,
            "features": features,
            "window_minutes": WINDOW_MINUTES
        },
        MODEL_PATH
    )

    print(f"\nSaved model -> {MODEL_PATH}")


if __name__ == "__main__":
    train()