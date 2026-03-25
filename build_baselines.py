#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, math
from datetime import datetime, date
from pathlib import Path
from collections import defaultdict
import psycopg2
from psycopg2.extras import DictCursor

# Importa a conexão com o Supabase do nosso arquivo database.py
from database import get_connection, logger

OUT_PATH = Path("data/baselines.json")
# Garante que a pasta data existe
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

def _parse_date(s: str) -> date:
    return datetime.fromisoformat(s[:10]).date()

def _parse_ts(s: str) -> date:
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
    conn = get_connection()
    if not conn:
        logger.error("Sem conexão com o Supabase. Abortando cálculo de baselines.")
        return
        
    buckets = defaultdict(list)
    
    try:
        with conn.cursor(cursor_factory=DictCursor) as cursor:
            cursor.execute("SELECT origem, destino, data, ts, preco FROM historico")
            rows = cursor.fetchall()
    except Exception as e:
        logger.error(f"Erro ao ler banco de dados: {e}")
        return
    finally:
        conn.close()

    # Processar os dados
    for row in rows:
        try:
            origem = row["origem"].strip().upper()
            destino = row["destino"].strip().upper()
            dep = _parse_date(row["data"])
            tsd = _parse_ts(row["ts"])
            dd = _d_days(dep, tsd)
            dow = dep.weekday()
            b = _bucket(dd)
            price = float(row["preco"])
            
            if math.isfinite(price) and price > 0:
                key = f"{origem}-{destino}-{dow}-{b}"
                buckets[key].append(price)
        except Exception:
            continue

    out = {}
    for k, vals in buckets.items():
        if len(vals) < 3:
            out[k] = {"p10": None, "p25": None, "p50": pct(vals, 0.5)}
        else:
            out[k] = {"p10": pct(vals, 0.10), "p25": pct(vals, 0.25), "p50": pct(vals, 0.50)}
            
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Baselines guardadas com sucesso ({len(out)} rotas analisadas).")

if __name__ == "__main__":
    main()
