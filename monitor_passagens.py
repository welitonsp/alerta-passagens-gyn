# --- imports devem vir ANTES de usar qualquer módulo ---
import os
import sys
import time
import csv
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional
import requests

# -----------------------
# helpers de lista
# -----------------------
def brazil_capitals_iata() -> List[str]:
    return [
        "GIG","SDU","SSA","FOR","REC","NAT","MCZ","AJU",
        "MAO","BEL","SLZ","THE","BSB","FLN","POA","CWB",
        "CGR","CGB","CNF","VIX","JPA","PMW","PVH","BVB",
        "RBR","GYN","GRU","CGH"
    ]

def dedupe_keep_order(seq: List[str]) -> List[str]:
    seen = set(); out = []
    for x in seq:
        if x and x not in seen:
            seen.add(x); out.append(x)
    return out

# -----------------------
# Config
# -----------------------
class Config:
    ORIGEM = os.getenv("ORIGEM", "GYN").strip().upper()

    _dests_str = os.getenv("DESTINOS", ",".join(brazil_capitals_iata()))
    # normaliza apenas; NÃO use ORIGEM aqui dentro
    DESTINOS = [x.strip().upper() for x in _dests_str.split(",") if x.strip()]

    DAYS_AHEAD_FROM = int(os.getenv("DAYS_AHEAD_FROM", "10"))
    DAYS_AHEAD_TO   = int(os.getenv("DAYS_AHEAD_TO", "90"))
    SAMPLE_DEPARTURES = int(os.getenv("SAMPLE_DEPARTURES", "2"))
    MAX_OFFERS = int(os.getenv("MAX_OFFERS", "5"))
    CURRENCY = os.getenv("CURRENCY", "BRL")
    REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "1.2"))

# pós-processamento (agora pode referenciar Config.ORIGEM)
Config.DESTINOS = dedupe_keep_order([d for d in Config.DESTINOS if d != Config.ORIGEM])
