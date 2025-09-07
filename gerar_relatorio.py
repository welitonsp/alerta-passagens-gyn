#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Relatório diário do monitor de passagens.
- Lê data/history.csv (gerado pelo monitor)
- Seleciona o MENOR preço por rota (origem-destino) do dia anterior (UTC)
- Envia um resumo para o Telegram com: preço, data do voo, companhia e link
"""

from __future__ import annotations
import os
import csv
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import requests
from pathlib import Path
from typing import Dict, Any, List, Tuple

# ----------------------------
# Config via env
# ----------------------------
HISTORY_PATH = Path(os.getenv("HISTORY_PATH", "data/history.csv"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")

# Tamanho máximo do Telegram é ~4096 chars; deixo margem
TG_MAX = 3900


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}")


def tg_send(text: str) -> None:
    """Envia uma mensagem single para o Telegram (texto simples)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram não configurado; pulando envio.")
        return

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=20,
        )
        r.raise_for_status()
        log("Relatório enviado ao Telegram.")
    except requests.RequestException as e:
        log(f"Falha ao enviar ao Telegram: {e}")


def tg_send_chunked(text: str) -> None:
    """Envia em múltiplas mensagens se ultrapassar o limite."""
    if len(text) <= TG_MAX:
        tg_send(text)
        return

    start = 0
    part = 1
    while start < len(text):
        end = min(len(text), start + TG_MAX)
        chunk = text[start:end]
        prefix = f"(parte {part})\n" if start > 0 else ""
        tg_send(prefix + chunk)
        start = end
        part += 1


def ler_historico_do_dia_anterior(path: Path) -> List[Dict[str, Any]]:
    """
    Lê o CSV e retorna apenas as linhas cujo ts_utc é do dia ANTERIOR (UTC).
    Espera cabeçalho com, no mínimo:
      ts_utc, origem, destino, departure_date, price_total, currency, airline, deeplink
    """
    if not path.exists():
        log(f"Histórico não encontrado em {path}.")
        return []

    ontem_utc = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    itens: List[Dict[str, Any]] = []

    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts_raw = (row.get("ts_utc") or "").strip()
            if not ts_raw:
                continue
            try:
                # Aceita "2025-09-07T12:34:56Z" ou ISO com offset
                if ts_raw.endswith("Z"):
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                else:
                    ts = datetime.fromisoformat(ts_raw)
                ts = ts.astimezone(timezone.utc)
            except Exception:
                continue

            if ts.date() == ontem_utc:
                itens.append(row)

    return itens


def melhores_por_rota(linhas: List[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """
    Retorna um dict com a melhor (menor) tarifa por rota (origem, destino).
    """
    best: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in linhas:
        try:
            origem = (row.get("origem") or "").strip()
            destino = (row.get("destino") or "").strip()
            preco = float((row.get("price_total") or "inf").replace(",", "."))
            if not origem or not destino:
                continue
        except Exception:
            continue

        key = (origem, destino)
        cur = best.get(key)
        if cur is None or preco < cur["price"]:
            best[key] = {
                "price": preco,
                "currency": (row.get("currency") or "BRL").strip(),
                "date": (row.get("departure_date") or "").strip(),
                "airline": (row.get("airline") or "N/A").strip(),
                "deeplink": (row.get("deeplink") or "").strip(),
                "ts_utc": (row.get("ts_utc") or "").strip(),
            }
    return best


def formatar_relatorio(best: Dict[Tuple[str, str], Dict[str, Any]]) -> str:
    """
    Monta o texto final do relatório.
    """
    if not best:
        ontem = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%d/%m/%Y")
        return f"📊 Relatório diário ({ontem})\nNenhum dado registrado no dia anterior."

    ontem_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%d/%m/%Y")
    linhas = [f"📊 Relatório diário ({ontem_str})", ""]

    # Ordena por rota
    for (origem, destino), info in sorted(best.items(), key=lambda x: (x[0][0], x[0][1])):
        preco = f"{info['price']:.2f}"
        moeda = info["currency"]
        data_voo = info["date"] or "—"
        cia = info["airline"] or "N/A"
        link = info["deeplink"]

        base = f"✈️ {origem} → {destino}\n• Menor preço: {preco} {moeda}\n• Voo: {data_voo} ({cia})"
        if link:
            base += f"\n• Link: {link}"
        linhas.append(base)
        linhas.append("")  # linha em branco entre rotas

    return "\n".join(linhas).rstrip()


def main() -> None:
    log("Gerando relatório diário…")
    linhas = ler_historico_do_dia_anterior(HISTORY_PATH)
    best = melhores_por_rota(linhas)
    texto = formatar_relatorio(best)
    tg_send_chunked(texto)
    log("Relatório finalizado.")


if __name__ == "__main__":
    main()