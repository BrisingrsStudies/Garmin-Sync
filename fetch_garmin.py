#!/usr/bin/env python3
"""
Garmin Connect → CSV-Sync für das Dashboard.

Zieht die letzten DAYS Tage aus Garmin Connect und schreibt data/garmin.csv
mit Spalten, die das Dashboard automatisch erkennt.

Umgebungsvariablen:
  GARMIN_EMAIL     Garmin-Connect-E-Mail (Pflicht)
  GARMIN_PASSWORD  Garmin-Connect-Passwort (Pflicht)
  DAYS             Anzahl Tage rückwirkend (Standard: 30)
"""

import csv
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from garminconnect import Garmin

DAYS = int(os.environ.get("DAYS", "30"))
OUT = Path("data/garmin.csv")

FIELDS = [
    "Datum", "Schritte", "Kalorien", "Ruheherzfrequenz", "HRV",
    "Schlafwert", "Schlafdauer", "Tiefschlaf", "REM", "Leichtschlaf", "Wach",
    "Schlafbeginn", "Schlafende",
    "Trainingsbereitschaft", "Body Battery Max", "Body Battery Min",
    "Trainingsbelastung", "VO2max",
]


def safe(fn, *args, default=None):
    """Einzelne API-Aufrufe dürfen fehlschlagen, ohne den Sync zu stoppen."""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠ {fn.__name__}{args}: {e}", file=sys.stderr)
        return default


def h(seconds):
    return round(seconds / 3600, 1) if seconds else None


def fetch_day(api: Garmin, d: str) -> dict:
    row = {"Datum": d}

    stats = safe(api.get_stats, d) or {}
    row["Schritte"] = stats.get("totalSteps")
    row["Kalorien"] = stats.get("totalKilocalories")
    row["Ruheherzfrequenz"] = stats.get("restingHeartRate")

    sleep = (safe(api.get_sleep_data, d) or {}).get("dailySleepDTO") or {}
    row["Schlafdauer"] = h(sleep.get("sleepTimeSeconds"))
    row["Tiefschlaf"] = h(sleep.get("deepSleepSeconds"))
    row["REM"] = h(sleep.get("remSleepSeconds"))
    row["Leichtschlaf"] = h(sleep.get("lightSleepSeconds"))
    row["Wach"] = h(sleep.get("awakeSleepSeconds"))
    score = ((sleep.get("sleepScores") or {}).get("overall") or {}).get("value")
    row["Schlafwert"] = score

    def hhmm(ms):
        if not ms:
            return None
        from datetime import datetime
        return datetime.utcfromtimestamp(ms / 1000).strftime("%H:%M")

    row["Schlafbeginn"] = hhmm(sleep.get("sleepStartTimestampLocal"))
    row["Schlafende"] = hhmm(sleep.get("sleepEndTimestampLocal"))

    hrv = (safe(api.get_hrv_data, d) or {}).get("hrvSummary") or {}
    row["HRV"] = hrv.get("lastNightAvg")

    readiness = safe(api.get_training_readiness, d) or []
    if readiness:
        row["Trainingsbereitschaft"] = readiness[0].get("score")

    bb = safe(api.get_body_battery, d, d) or []
    values = [v[1] for e in bb for v in (e.get("bodyBatteryValuesArray") or []) if v and v[1] is not None]
    if values:
        row["Body Battery Max"] = max(values)
        row["Body Battery Min"] = min(values)

    status = safe(api.get_training_status, d) or {}
    try:
        recent = status.get("mostRecentTrainingLoadBalance") or {}
        metrics = list((recent.get("metricsTrainingLoadBalanceDTOMap") or {}).values())
        if metrics:
            acute = metrics[0].get("trainingBalanceFeedbackPhrase")
            _ = acute  # nur Phrase vorhanden → Load stattdessen aus acuteLoad
        load_map = status.get("mostRecentTrainingStatus") or {}
        latest = list((load_map.get("latestTrainingStatusData") or {}).values())
        if latest:
            row["Trainingsbelastung"] = latest[0].get("acuteTrainingLoadDTO", {}).get("dailyTrainingLoadAcute")
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠ Trainingsbelastung: {e}", file=sys.stderr)

    vo2 = safe(api.get_max_metrics, d) or []
    if vo2:
        gen = (vo2[0].get("generic") or {})
        row["VO2max"] = gen.get("vo2MaxPreciseValue") or gen.get("vo2MaxValue")

    return row


def main() -> int:
    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    if not email or not password:
        print("GARMIN_EMAIL und GARMIN_PASSWORD müssen gesetzt sein.", file=sys.stderr)
        return 1

    print(f"Anmeldung bei Garmin Connect als {email[:3]}…")
    api = Garmin(email, password)
    api.login()

    # Bestehende Historie einlesen, damit alte Tage erhalten bleiben
    history: dict[str, dict] = {}
    if OUT.exists():
        with OUT.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("Datum"):
                    history[row["Datum"]] = row
        print(f"{len(history)} Tage Historie geladen.")

    today = date.today()
    for i in range(DAYS, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        print(f"→ {d}")
        history[d] = fetch_day(api, d)  # neue Abrufe überschreiben alte Einträge desselben Tages

    rows = [history[k] for k in sorted(history)]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) in (None, "") else row[k]) for k in FIELDS})

    print(f"✓ {len(rows)} Tage nach {OUT} geschrieben.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
