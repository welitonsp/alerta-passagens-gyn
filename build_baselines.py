#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import csv, json, math
from datetime import datetime, date
from pathlib import Path
from collections import defaultdict

HISTORY_PATH = Path("data/history.csv")
OUT_PATH = Path("data/baselines.json")

def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()

def _parse_ts(s: str) -> date:
    # ts_utc ISO8601
    return datetime.fromisoformat(s.replace("Z","+00:00")).date()

def _d_days(dep: date, collected: date) -> int:
    return max(0, (dep - collected).days)

def _bucket(dd: int) -> str:
    if dd <= 6: return "0-6"
    if dd <= 13: return "7-13"
    if dd <= 20: return "14-20"
    if dd <= 27: return "21-27"
    if dd <= 34: return "28-34"
    if dd <= 49: return "35-49"
    if dd <= 69: return "50-69"
    return "70-90"

def pct(vals, q):
    if not vals: return None
    vals = sorted(vals)
    i = max(0, min(len(vals)-1, int(q*(len(vals)-1))))
    return vals[i]

def main():
    if not HISTORY_PATH.exists():
        print("history.csv não encontrado; nada a fazer.")
        return
    buckets = defaultdict(list)
    with HISTORY_PATH.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                origem = row["origem"].strip().upper()
                destino = row["destino"].strip().upper()
                dep = _parse_date(row["departure_date"])
                tsd = _parse_ts(row["ts_utc"])
                dd = _d_days(dep, tsd)
                dow = dep.weekday()
                b = _bucket(dd)
                price = float(row["price_total"])
                if math.isfinite(price) and price > 0:
                    key = f"{origem}-{destino}-{dow}-{b}"
                    buckets[key].append(price)
            except Exception:
                continue

    out = {}
    for k, vals in buckets.items():
        if len(vals) < 3:
            # pouco dado, mas ainda podemos salvar p50
            out[k] = {"p10": None, "p25": None, "p50": pct(vals, 0.5)}
        else:
            out[k] = {"p10": pct(vals, 0.10), "p25": pct(vals, 0.25), "p50": pct(vals, 0.50)}
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Baselines salvos em {OUT_PATH} ({len(out)} chaves).")

if __name__ == "__main__":
    main()
