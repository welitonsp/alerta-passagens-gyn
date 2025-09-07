#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import csv
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import requests

HISTORY_PATH = Path(os.getenv("HISTORY_PATH", "data/history.csv"))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")

ONLY_YESTERDAY = os.getenv("REPORT_ONLY_YESTERDAY", "true").lower() == "true"
USE_BRAZIL_LOCAL_DAY = os.getenv("REPORT_USE_BRAZIL_LOCAL_DAY", "false").lower() == "true"
REPORT_DAYS_WINDOW = int(os.getenv("REPORT_DAYS_WINDOW", "1"))

REPORT_COMPARE = os.getenv("REPORT_COMPARE", "true").lower() == "true"
COMPARE_WINDOW_DAYS = int(os.getenv("REPORT_COMPARE_WINDOW_DAYS", "1"))

ORIGEM = (os.getenv("ORIGEM", "")).strip().upper()
TOP_K = int(os.getenv("REPORT_TOP_K", "15"))
TITLE = os.getenv("REPORT_TITLE", "📊 Relatório diário — menores preços (PROD)")
TG_PARSE_MODE = os.getenv("REPORT_PARSE_MODE", "MarkdownV2").strip()
INCLUDE_LINKS = os.getenv("REPORT_INCLUDE_LINKS", "true").lower() == "true"

MAX_TG_LEN = 4096

def log(msg: str):
    print(f"[{datetime.utcnow().isoformat()}Z] {msg}")

def escape_mdv2(s: str) -> str:
    specials = r"_*[]()~`>#+-=|{}.!"
    out = []
    for ch in s:
        out.append("\\" + ch if ch in specials else ch)
    return "".join(out)

def tg_send_chunked(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram não configurado; imprimindo no log.")
        print(text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    base_payload = {"chat_id": TELEGRAM_CHAT_ID, "text": "", "disable_web_page_preview": True}
    if TG_PARSE_MODE:
        base_payload["parse_mode"] = TG_PARSE_MODE
    remaining = text
    while len(remaining) > MAX_TG_LEN:
        cut = remaining.rfind("\n", 0, MAX_TG_LEN)
        if cut <= 0:
            cut = MAX_TG_LEN
        chunk = remaining[:cut]
        remaining = remaining[cut:].lstrip("\n")
        try:
            r = requests.post(url, json={**base_payload, "text": chunk}, timeout=20)
            r.raise_for_status()
        except requests.RequestException as e:
            log(f"Falha ao enviar Telegram (chunk): {e}")
    try:
        r = requests.post(url, json={**base_payload, "text": remaining}, timeout=20)
        r.raise_for_status()
    except requests.RequestException as e:
        log(f"Falha ao enviar Telegram (final): {e}")
    else:
        log("Relatório enviado ao Telegram.")

def parse_iso_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)

def read_history_rows():
    if not HISTORY_PATH.exists():
        log(f"Nenhum histórico encontrado em {HISTORY_PATH}")
        return []
    try:
        with HISTORY_PATH.open("r", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        log(f"Erro lendo CSV: {e}")
        return []

def brazil_local_day_utc_interval(target_date_local):
    offset = timedelta(hours=3)
    start_local = datetime(target_date_local.year, target_date_local.month, target_date_local.day, 0, 0, tzinfo=timezone(-offset))
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)

def get_primary_window():
    now_utc = datetime.now(timezone.utc)
    if ONLY_YESTERDAY:
        if USE_BRAZIL_LOCAL_DAY:
            today_br = (now_utc - timedelta(hours=3)).date()
            y_br = today_br - timedelta(days=1)
            s, e = brazil_local_day_utc_interval(y_br)
            label = y_br.strftime("%d/%m/%Y (BR)")
        else:
            y = (now_utc.date() - timedelta(days=1))
            s = datetime(y.year, y.month, y.day, tzinfo=timezone.utc)
            e = s + timedelta(days=1)
            label = y.strftime("%d/%m/%Y (UTC)")
        return s, e, label
    s = now_utc - timedelta(days=REPORT_DAYS_WINDOW)
    e = now_utc
    return s, e, f"últimos {REPORT_DAYS_WINDOW} dia(s) (UTC)"

def get_compare_window(primary_start: datetime, primary_end: datetime):
    if not REPORT_COMPARE:
        return None, None, ""
    if ONLY_YESTERDAY:
        length = timedelta(days=max(COMPARE_WINDOW_DAYS, 1))
        comp_end = primary_start
        comp_start = comp_end - length
    else:
        comp_end = primary_start
        comp_start = comp_end - (primary_end - primary_start)
    return comp_start, comp_end, "comparado ao período anterior"

def google_flights_link(origem: str, destino: str, date_str: str) -> str:
    if not (origem and destino and date_str):
        return ""
    return f"https://www.google.com/travel/flights?q=Flights%20{origem}%20to%20{destino}%20on%20{date_str}"

def collect_best_by_route(rows, start_utc, end_utc, origem_filter=""):
    best = defaultdict(lambda: {"price": float("inf"), "cur": "BRL", "dep": "", "airline": "", "ts": ""})
    for r in rows:
        try:
            ts = parse_iso_utc(r["ts_utc"])
        except Exception:
            continue
        if not (start_utc <= ts < end_utc):
            continue
        ori = r.get("origem", "").upper()
        dest = r.get("destino", "").upper()
        if origem_filter and ori != origem_filter:
            continue
        dep = r.get("departure_date") or ""
        cur = r.get("currency", "BRL")
        airline = r.get("airline", "")
        try:
            price = float(r["price_total"])
        except Exception:
            continue
        key = (ori, dest)
        if price < best[key]["price"]:
            best[key] = {"price": price, "cur": cur, "dep": dep, "airline": airline, "ts": ts.strftime("%Y-%m-%d %H:%M UTC")}
    return best

def format_money(v: float, cur: str) -> str:
    return f"{v:,.2f} {cur}".replace(",", "X").replace(".", ",").replace("X", ".")

def delta_pct_str(current: float, prev: float) -> str:
    if prev <= 0:
        return ""
    pct = (current - prev) / prev
    arrow = "⬇️" if pct < 0 else ("⬆️" if pct > 0 else "➡️")
    return f"{arrow} {abs(pct)*100:.1f}%"

def gerar_relatorio_texto():
    rows = read_history_rows()
    if not rows:
        return f"{TITLE}\n\nNenhum registro no histórico."

    p_start, p_end, p_label = get_primary_window()
    c_start, c_end, c_label = get_compare_window(p_start, p_end)

    best_now = collect_best_by_route(rows, p_start, p_end, ORIGEM)
    if not best_now:
        return f"{TITLE}\n\nSem dados para {p_label}."

    ranking = sorted(best_now.items(), key=lambda kv: kv[1]["price"])
    if TOP_K < len(ranking):
        ranking = ranking[:TOP_K]

    best_prev = {}
    if REPORT_COMPARE and c_start and c_end:
        best_prev = collect_best_by_route(rows, c_start, c_end, ORIGEM)

    n_down = n_up = n_flat = 0
    biggest_drop = (None, 0.0, None)
    biggest_rise = (None, 0.0, None)
    def _delta_pct(cur, prev):
        if prev and prev > 0: return (cur - prev) / prev
        return None

    for (ori, dest), cur_info in best_now.items():
        if not best_prev:
            continue
        prev_info = best_prev.get((ori, dest))
        if not prev_info or prev_info["price"] in (None, float("inf")):
            continue
        cur_p, prev_p = cur_info["price"], prev_info["price"]
        pct = _delta_pct(cur_p, prev_p)
        if pct is None:
            continue
        if pct < -1e-9:
            n_down += 1
            if abs(pct) > abs(biggest_drop[1]):
                biggest_drop = ((ori, dest), pct, prev_p)
        elif pct > 1e-9:
            n_up += 1
            if pct > biggest_rise[1]:
                biggest_rise = ((ori, dest), pct, prev_p)
        else:
            n_flat += 1

    title = TITLE
    if TG_PARSE_MODE == "MarkdownV2":
        title = escape_mdv2(title); period = escape_mdv2(p_label); origin_str = escape_mdv2(ORIGEM or "todas")
    else:
        period = p_label; origin_str = ORIGEM or "todas"

    lines = [f"{title}", f"Período: {period}" + (f" | {c_label}" if REPORT_COMPARE and best_prev else ""), f"Origem: {origin_str}"]

    if best_prev:
        def fmt_route(rt):
            if not rt: return "-"
            (ori, dest) = rt; return f"{ori}→{dest}"
        drop_line = "-"
        if biggest_drop[0]:
            rt = fmt_route(biggest_drop[0])
            cur_p = best_now[biggest_drop[0]]["price"]
            prev_p = biggest_drop[2]
            drop_line = f"{rt} ({format_money(prev_p, best_now[biggest_drop[0]]['cur'])} → {format_money(cur_p, best_now[biggest_drop[0]]['cur'])}, {delta_pct_str(cur_p, prev_p)})"
        rise_line = "-"
        if biggest_rise[0]:
            rt = fmt_route(biggest_rise[0])
            cur_p = best_now[biggest_rise[0]]["price"]
            prev_p = biggest_rise[2]
            rise_line = f"{rt} ({format_money(prev_p, best_now[biggest_rise[0]]['cur'])} → {format_money(cur_p, best_now[biggest_rise[0]]['cur'])}, {delta_pct_str(cur_p, prev_p)})"

        lines += ["—", f"Resumo: ⬇️ {n_down} | ⬆️ {n_up} | ➡️ {n_flat}", f"Maior queda: {drop_line}", f"Maior alta: {rise_line}"]

    lines.append("—")
    for i, ((ori, dest), info) in enumerate(ranking, start=1):
        price = info["price"]; cur = info["cur"]; dep = info["dep"]; airline = info["airline"]
        link = google_flights_link(ori, dest, dep) if (dep and INCLUDE_LINKS) else ""
        price_str = format_money(price, cur)
        delta_str = ""
        if best_prev:
            prev = best_prev.get((ori, dest))
            if prev and prev["price"] not in (None, float("inf")):
                delta_str = f" | {delta_pct_str(price, prev['price'])} vs {format_money(prev['price'], prev['cur'])}"

        if TG_PARSE_MODE == "MarkdownV2":
            line = f"{i:02d}\\. {escape_mdv2(ori)}→{escape_mdv2(dest)} {escape_mdv2(dep or '')} — {escape_mdv2(price_str)}"
            if airline: line += f" ({escape_mdv2(airline)})"
            if delta_str: line += " " + escape_mdv2(delta_str)
            if link: line += f"\n{escape_mdv2(link)}"
        else:
            line = f"{i:02d}. {ori}→{dest} {dep or ''} — {price_str}"
            if airline: line += f" ({airline})"
            if delta_str: line += f"{delta_str}"
            if link: line += f"\n{link}"
        lines.append(line)

    if ranking:
        menor = ranking[0][1]["price"]; cur0 = ranking[0][1]["cur"]
        media = sum(v["price"] for _, v in ranking)/len(ranking)
        agg_line_1 = f"Menor (Top {len(ranking)}): {format_money(menor, cur0)}"
        agg_line_2 = f"Média (Top {len(ranking)}): {format_money(media, cur0)}"
        if TG_PARSE_MODE == "MarkdownV2":
            agg_line_1 = escape_mdv2(agg_line_1); agg_line_2 = escape_mdv2(agg_line_2)
        lines += ["—", agg_line_1, agg_line_2]

    return "\n".join(lines)

def main():
    text = gerar_relatorio_texto()
    tg_send_chunked(text)
    print(text)

if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as e:
        log(f"Erro de rede ao enviar relatório: {e}")
        sys.exit(1)
