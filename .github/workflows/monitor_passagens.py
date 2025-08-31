#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Monitor de passagens com detecção de oportunidade (Kiwi/Tequila):
- Origem fixa: GYN
- Destinos: capitais do Brasil (ajustável)
- PAX: 2 adultos + 2 crianças (idades 4 e 8) -> children=2
- Oportunidade = preço por pessoa abaixo da média histórica (30d, por bucket de DTD)
                 e/ou queda relevante vs ontem (mesmo bucket)
- Histórico salvo em data/history.csv
"""

import os, sys, csv, math, requests
from pathlib import Path
from datetime import datetime, timedelta, timezone, date
from typing import List, Dict, Any, Tuple

# ---------------- VARIÁVEIS DE AMBIENTE / AJUSTES ----------------
BOT_TOKEN    = os.getenv("BOT_TOKEN")
CHAT_ID      = os.getenv("CHAT_ID")
KIWI_API_KEY = os.getenv("KIWI_API_KEY")

# Origem e destinos
ORIGEM = "GYN"  # fixo
DESTINOS_DEFAULT = [
    # Capitais (com múltiplos aeroportos onde faz sentido)
    "RBR","MCZ","MCP","MAO","SSA","FOR","BSB","VIX","SLZ","CGB","CGR",
    "CNF","BEL","JPA","CWB","REC","THE","GIG","SDU","NAT","POA","PVH",
    "BVB","FLN","GRU","CGH","AJU","PMW",
    # Principais cidades (pode editar à vontade)
    "VCP","IGU","VCP","RAO","UDI"
]
DESTINOS = [d.strip().upper() for d in os.getenv(
    "DESTINOS", ",".join(DESTINOS_DEFAULT)
).split(",") if d.strip()]
# remove duplicados preservando ordem
_seen=set(); DESTINOS=[x for x in DESTINOS if not (x in _seen or _seen.add(x))]

# Janela de busca (ida) e estadia
DAYS_AHEAD_FROM = int(os.getenv("DAYS_AHEAD_FROM", "10"))
DAYS_AHEAD_TO   = int(os.getenv("DAYS_AHEAD_TO",   "90"))
STAY_NIGHTS_MIN = int(os.getenv("STAY_NIGHTS_MIN", "5"))
STAY_NIGHTS_MAX = int(os.getenv("STAY_NIGHTS_MAX", "10"))

# Escalas
MAX_STOPOVERS   = int(os.getenv("MAX_STOPOVERS", "1"))

# Critérios de “barato”
MAX_PRECO_PP       = float(os.getenv("MAX_PRECO_PP", "1200"))  # cap absoluto (opcional)
MIN_DISCOUNT_PCT   = float(os.getenv("MIN_DISCOUNT_PCT", "0.25"))  # ≥25% abaixo da média
MIN_DAYDROP_PCT    = float(os.getenv("MIN_DAYDROP_PCT", "0.30"))   # ≥30% queda vs ontem
BASELINE_WINDOW_DAYS = int(os.getenv("BASELINE_WINDOW_DAYS", "30"))
BIN_SIZE_DAYS        = int(os.getenv("BIN_SIZE_DAYS", "7"))
MAX_PER_DEST         = int(os.getenv("MAX_PER_DEST", "1"))

# PAX: 2 ADT + 2 CHD (idades 4 e 8). A Kiwi usa apenas “children” (contagem).
ADULTS   = 2
CHILDREN = 2
# Observação: nem toda API aceita idades individuais; na Kiwi o “children” é suficiente para busca/indicativo.
CHILDREN_AGES = [4, 8]  # guardado para referência; não é enviado

PAX_TOTAL = ADULTS + CHILDREN

# Arquivo de histórico
HIST_DIR  = Path("data"); HIST_DIR.mkdir(parents=True, exist_ok=True)
HIST_FILE = HIST_DIR / "history.csv"

# ----------------- UTILIDADES -----------------
def brl(n: float) -> str:
    s = f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

def log(msg: str):
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] {msg}")

def enviar_telegram(texto: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        log("ERRO: defina BOT_TOKEN e CHAT_ID.")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": texto, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        r = requests.post(url, json=payload, timeout=25)
        ok = (r.status_code == 200) and r.json().get("ok", False)
        if not ok:
            log(f"Telegram erro: HTTP {r.status_code} -> {r.text[:300]}")
        return ok
    except Exception as e:
        log(f"Exceção Telegram: {e}")
        return False

def _dates_from_route(route: List[Dict[str, Any]]) -> Tuple[date, date]:
    ida = [s for s in route if s.get("return", 0) == 0]
    volta = [s for s in route if s.get("return", 0) == 1]
    def _first(seg_list):
        if not seg_list: return None
        d0 = min(seg_list, key=lambda s: s.get("local_departure",""))["local_departure"]
        return datetime.fromisoformat(d0.replace("Z","+00:00")).date()
    return _first(ida), _first(volta)

def _bucket(dtd: int) -> str:
    b0 = (dtd // BIN_SIZE_DAYS) * BIN_SIZE_DAYS
    return f"{b0}-{b0+BIN_SIZE_DAYS-1}"

# ----------------- KIWI (TEQUILA) -----------------
def kiwi_search(origin: str, dest: str) -> List[Dict[str, Any]]:
    if not KIWI_API_KEY:
        raise SystemExit("Falta KIWI_API_KEY (adicione nos Secrets).")
    today = datetime.now(timezone.utc).date()
    date_from = (today + timedelta(days=DAYS_AHEAD_FROM)).strftime("%d/%m/%Y")
    date_to   = (today + timedelta(days=DAYS_AHEAD_TO)).strftime("%d/%m/%Y")

    url = "https://tequila-api.kiwi.com/v2/search"
    params = {
        "fly_from": origin,
        "fly_to": dest,
        "date_from": date_from,
        "date_to": date_to,
        "nights_in_dst_from": STAY_NIGHTS_MIN,
        "nights_in_dst_to": STAY_NIGHTS_MAX,
        "adults": ADULTS,
        "children": CHILDREN,
        # "children_ages": ",".join(map(str, CHILDREN_AGES)),  # se a API aceitar no futuro
        "curr": "BRL",
        "locale": "pt-BR",
        "selected_cabins": "M",
        "max_stopovers": MAX_STOPOVERS,
        "one_for_city": 1,
        "limit": 10,
        "sort": "price",
        "asc": 1,
    }
    headers = {"apikey": KIWI_API_KEY}
    r = requests.get(url, params=params, headers=headers, timeout=35)
    r.raise_for_status()
    data = r.json()
    return data.get("data", []) or []

# ----------------- HISTÓRICO (BASELINE) -----------------
def carregar_historico() -> List[Dict[str, Any]]:
    if not HIST_FILE.exists(): return []
    out=[]
    with open(HIST_FILE, "r", newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                r["price_pp"] = float(r["price_pp"])
                r["ts_utc"] = datetime.fromisoformat(r["ts_utc"])
                r["date_out"] = datetime.fromisoformat(r["date_out"]).date()
                out.append(r)
            except Exception:
                continue
    return out

def salvar_historico(novas: List[Dict[str, Any]]):
    write_header = not HIST_FILE.exists()
    with open(HIST_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "ts_utc","origin","dest","date_out","date_back","days_to_departure",
            "bucket_dtd","nights","stopovers","price_total","price_pp","provider","deep_link"
        ])
        if write_header: w.writeheader()
        for r in novas: w.writerow(r)

def mediana(vals: List[float]) -> float:
    vals = [v for v in vals if math.isfinite(v)]
    if not vals: return float("nan")
    vals.sort()
    n=len(vals)
    return vals[n//2] if n%2 else (vals[n//2-1]+vals[n//2])/2

def baseline_pp(rows, origin, dest, bucket, now_utc) -> float:
    start = now_utc - timedelta(days=BASELINE_WINDOW_DAYS)
    vals = [r["price_pp"] for r in rows
            if r["origin"]==origin and r["dest"]==dest and r["bucket_dtd"]==bucket and r["ts_utc"]>=start]
    if not vals:
        vals = [r["price_pp"] for r in rows
                if r["origin"]==origin and r["dest"]==dest and r["ts_utc"]>=start]
    return mediana(vals)

def ontem_pp(rows, origin, dest, bucket, now_utc) -> float:
    y0 = (now_utc - timedelta(days=1)).date()
    y1 = y0 + timedelta(days=1)
    vals = [r["price_pp"] for r in rows
            if r["origin"]==origin and r["dest"]==dest and r["bucket_dtd"]==bucket
            and y0 <= r["ts_utc"].date() < y1]
    return min(vals) if vals else float("nan")

# ----------------- NÚCLEO: BUSCA E REGRA DE OPORTUNIDADE -----------------
def buscar_ofertas() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    historico = carregar_historico()
    now = datetime.now(timezone.utc)
    ofertas=[]; observacoes=[]

    for dest in DESTINOS:
        try:
            results = kiwi_search(ORIGEM, dest)
        except Exception as e:
            log(f"Falha ao buscar {dest}: {e}")
            continue
        if not results:
            log(f"Sem resultados p/ {dest}")
            continue

        # Pega só os mais baratos por destino
        results.sort(key=lambda x: x.get("price", 10**9))
        for it in results[:MAX_PER_DEST]:
            total = float(it.get("price", 0.0))
            pp    = total / max(PAX_TOTAL,1)
            route = it.get("route", [])
            d_out, d_back = _dates_from_route(route)
            if not d_out: continue
            dtd  = (d_out - now.date()).days
            bkt  = _bucket(max(dtd,0))
            deep = it.get("deep_link") or ""
            stopovers = 1 if it.get("has_stopovers", False) else 0
            nights = it.get("nightsInDest") or 0
            city_to = it.get("cityTo") or dest

            # registra histórico
            observacoes.append({
                "ts_utc": now.isoformat(),
                "origin": ORIGEM,
                "dest": dest,
                "date_out": d_out.isoformat(),
                "date_back": (d_back.isoformat() if d_back else ""),
                "days_to_departure": dtd,
                "bucket_dtd": bkt,
                "nights": nights,
                "stopovers": stopovers,
                "price_total": f"{total:.2f}",
                "price_pp": f"{pp:.2f}",
                "provider": "kiwi",
                "deep_link": deep
            })

            base = baseline_pp(historico, ORIGEM, dest, bkt, now)
            yd   = ontem_pp(historico, ORIGEM, dest, bkt, now)
            desc_base   = (base - pp)/base if (base and math.isfinite(base) and base>0) else float("nan")
            queda_ontem = (yd   - pp)/yd   if (yd   and math.isfinite(yd)   and yd>0)   else float("nan")

            bom=False; motivos=[]
            if math.isfinite(desc_base) and desc_base >= MIN_DISCOUNT_PCT:
                bom=True; motivos.append(f"abaixo da média {int(desc_base*100)}%")
            if math.isfinite(queda_ontem) and queda_ontem >= MIN_DAYDROP_PCT:
                bom=True; motivos.append(f"queda vs ontem {int(queda_ontem*100)}%")
            if pp <= MAX_PRECO_PP:
                bom=True; motivos.append(f"abaixo de {brl(MAX_PRECO_PP)}")

            if bom:
                ofertas.append({
                    "dest": dest,
                    "city_to": city_to,
                    "price_total": total,
                    "price_pp": pp,
                    "baseline_pp": (base if math.isfinite(base) else None),
                    "desc_base":   (desc_base if math.isfinite(desc_base) else None),
                    "ontem_pp":    (yd if math.isfinite(yd) else None),
                    "queda_ontem": (queda_ontem if math.isfinite(queda_ontem) else None),
                    "nights": nights,
                    "deep_link": deep,
                    "ida": d_out.strftime("%d/%m/%Y"),
                    "volta": (d_back.strftime("%d/%m/%Y") if d_back else "?"),
                    "bucket": bkt,
                    "motivos": motivos
                })

    return ofertas, observacoes

def montar_msg(o: Dict[str, Any]) -> str:
    linhas = [
        "🔥 <b>Oferta identificada</b>",
        f"📍 <b>{ORIGEM} → {o['dest']} ({o['city_to']})</b>",
        f"📅 <b>Datas:</b> {o['ida']} – {o['volta']}  ({o['nights']} noites)",
        "👨‍👩‍👧‍👦 2 Adultos + 2 Crianças (4 e 8 anos)",
        f"💳 <b>Preço por pessoa:</b> {brl(o['price_pp'])}",
        f"💳 <b>Total (4 pax):</b> {brl(o['price_total'])}",
        f"🧮 Bucket DTD: {o['bucket']} (dias até a partida)",
    ]
    if o.get("baseline_pp") is not None:
        linhas.append(f"📊 Média {BASELINE_WINDOW_DAYS}d: {brl(o['baseline_pp'])}")
    if o.get("desc_base") is not None:
        linhas.append(f"⬇️ Desconto vs média: {int(o['desc_base']*100)}%")
    if o.get("ontem_pp") is not None:
        linhas.append(f"📆 Ontem: {brl(o['ontem_pp'])}")
    if o.get("queda_ontem") is not None:
        linhas.append(f"↘️ Queda vs ontem: {int(o['queda_ontem']*100)}%")
    if o.get("deep_link"):
        linhas.append(f"🔗 <a href=\"{o['deep_link']}\">Ver detalhes</a>")
    if o.get("motivos"):
        linhas.append("✅ " + " • ".join(o["motivos"]))
    linhas.append("<i>Fonte: Kiwi (Tequila API) • Monitor via GitHub Actions</i>")
    return "\n".join(linhas)

def main():
    if not (BOT_TOKEN and CHAT_ID and KIWI_API_KEY):
        print("ERRO: defina BOT_TOKEN, CHAT_ID, KIWI_API_KEY nos Secrets.", file=sys.stderr)
        sys.exit(1)

    log(f"Monitor iniciado | origem=GYN | destinos={len(DESTINOS)} | janela={DAYS_AHEAD_FROM}-{DAYS_AHEAD_TO}d | stay={STAY_NIGHTS_MIN}-{STAY_NIGHTS_MAX} | bin={BIN_SIZE_DAYS}d | pax=2ADT+2CHD(4,8)")
    ofertas, obs = buscar_ofertas()

    if obs:
        salvar_historico(obs)
        log(f"Histórico atualizado com {len(obs)} observações.")

    if not ofertas:
        log("Nenhuma oferta que atenda aos critérios agora.")
        return

    ofertas.sort(key=lambda x: (-(x['desc_base'] or 0.0), x['price_pp']))
    for o in ofertas:
        enviar_telegram(montar_msg(o))

if __name__ == "__main__":
    main()
