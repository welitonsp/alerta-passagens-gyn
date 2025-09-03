#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Monitor de passagens aéreas (Amadeus Self-Service API) – Versão Aprimorada e Robusta

• Fonte: Amadeus Flight Offers Search (OAuth2 + GET /v2/shopping/flight-offers)
• Origem fixa: GYN (ajustável por env ORIGEM)
• Destinos: capitais/cidades (ajustável por env DESTINOS)
• PAX: 2 adultos + 2 crianças (ajustável)
• Critérios de oportunidade: baseline por bucket + daydrop por data + teto de preço
• Histórico: data/history.csv
• Alertas: Telegram (BOT_TOKEN + CHAT_ID)

Requisitos:
  - requests >= 2.31.0
Secrets esperados no GitHub:
  - TELEGRAM_BOT_TOKEN (exportado como BOT_TOKEN no job)
  - TELEGRAM_CHAT_ID (exportado como CHAT_ID no job)
  - AMADEUS_API_KEY
  - AMADEUS_API_SECRET
"""

import os
import sys
import csv
import time
import math
import requests
import threading
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone, date
from typing import List, Dict, Any, Tuple, Optional
from statistics import mean, stdev
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------------- Logging ----------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# ============================== CONFIG ==============================
class Config:
    # Telegram
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    CHAT_ID   = os.getenv('CHAT_ID')

    # Amadeus creds (Self-Service)
    AMADEUS_API_KEY    = os.getenv('AMADEUS_API_KEY')
    AMADEUS_API_SECRET = os.getenv('AMADEUS_API_SECRET')

    # Endpoint (TEST). Para produção: https://api.amadeus.com
    AMADEUS_BASE_URL = os.getenv('AMADEUS_BASE_URL', 'https://test.api.amadeus.com')

    # Busca
    ORIGEM = os.getenv('ORIGEM', 'GYN').upper()
    DESTINOS_DEFAULT = [
        'RBR','MCZ','MCP','MAO','SSA','FOR','BSB','VIX','SLZ','CGB','CGR',
        'CNF','BEL','JPA','CWB','REC','THE','GIG','SDU','NAT','POA','PVH',
        'BVB','FLN','GRU','CGH','AJU','PMW','VCP','IGU','RAO','UDI'
    ]
    DESTINOS = [d.strip().upper() for d in os.getenv('DESTINOS', ','.join(DESTINOS_DEFAULT)).split(',') if d.strip()]
    _seen = set()
    DESTINOS = [x for x in DESTINOS if not (x in _seen or _seen.add(x))]

    DAYS_AHEAD_FROM = int(os.getenv('DAYS_AHEAD_FROM', '10'))
    DAYS_AHEAD_TO   = int(os.getenv('DAYS_AHEAD_TO',   '90'))
    STAY_NIGHTS_MIN = int(os.getenv('STAY_NIGHTS_MIN', '5'))
    STAY_NIGHTS_MAX = int(os.getenv('STAY_NIGHTS_MAX', '10'))

    # Amostragem para conter chamadas (e 429)
    SAMPLE_DEPARTURES = int(os.getenv('SAMPLE_DEPARTURES', '3'))  # nº de datas de ida amostradas
    SAMPLE_STAYS      = int(os.getenv('SAMPLE_STAYS', '2'))       # tipicamente [min, max]

    # Critérios de oportunidade
    MAX_PRECO_PP         = float(os.getenv('MAX_PRECO_PP', '1200'))
    MIN_DISCOUNT_PCT     = float(os.getenv('MIN_DISCOUNT_PCT', '0.25'))
    MIN_DAYDROP_PCT      = float(os.getenv('MIN_DAYDROP_PCT', '0.30'))
    BASELINE_WINDOW_DAYS = int(os.getenv('BASELINE_WINDOW_DAYS','30'))
    BIN_SIZE_DAYS        = int(os.getenv('BIN_SIZE_DAYS', '7'))
    MAX_PER_DEST         = int(os.getenv('MAX_PER_DEST', '1'))   # limite global de alertas (1 = top geral)
    MAX_STOPOVERS        = int(os.getenv('MAX_STOPOVERS', '99')) # filtro opcional por nº máx de conexões/paradas

    # Passageiros
    ADULTS   = int(os.getenv('ADULTS', '2'))
    CHILDREN = int(os.getenv('CHILDREN', '2'))
    # Amadeus aceita idades de crianças – ajuste se precisar
    CHILDREN_AGES = [int(x) for x in os.getenv('CHILDREN_AGES', '4,8').split(',')]
    PAX_TOTAL = ADULTS + CHILDREN

    # Moeda
    CURRENCY = os.getenv('CURRENCY', 'BRL')

    # Histórico
    HIST_DIR  = Path('data')
    HIST_FILE = HIST_DIR / 'history.csv'


# ============================== UTILS ==============================
class Utils:
    @staticmethod
    def brl(n: float) -> str:
        s = f'{n:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
        return f'R$ {s}'

    @staticmethod
    def enviar_telegram(texto: str) -> bool:
        if not Config.BOT_TOKEN or not Config.CHAT_ID:
            logging.error('ERRO: defina BOT_TOKEN e CHAT_ID.')
            return False
        url = f'https://api.telegram.org/bot{Config.BOT_TOKEN}/sendMessage'
        payload = {'chat_id': Config.CHAT_ID, 'text': texto, 'parse_mode': 'HTML', 'disable_web_page_preview': True}
        try:
            r = requests.post(url, json=payload, timeout=25)
            ok = (r.status_code == 200) and r.json().get('ok', False)
            if not ok:
                logging.warning(f'Telegram erro: HTTP {r.status_code} -> {r.text[:300]}')
            return ok
        except Exception as e:
            logging.error(f'Exceção Telegram: {e}')
            return False

    @staticmethod
    def bucket_from_dtd(dtd: int) -> str:
        b0 = (dtd // Config.BIN_SIZE_DAYS) * Config.BIN_SIZE_DAYS
        return f'{b0}-{b0 + Config.BIN_SIZE_DAYS - 1}'

    @staticmethod
    def get_stopover_count(segments: List[Dict]) -> int:
        # Conexões entre voos (ex.: 2 segments => 1 conexão)
        connections = max(0, len(segments) - 1)
        # Paradas técnicas declaradas no próprio segmento
        tech_stops  = sum(int(seg.get('numberOfStops', 0) or 0) for seg in segments)
        return connections + tech_stops


# ============================== CACHE (simples em memória) ==============================
class Cache:
    _cache_lock = threading.Lock()
    _api_cache: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def get(cls, key: str) -> Optional[Any]:
        with cls._cache_lock:
            item = cls._api_cache.get(key)
            if not item:
                return None
            if time.time() < item['expiry']:
                return item['value']
            # expirado
            del cls._api_cache[key]
            return None

    @classmethod
    def set(cls, key: str, value: Any, ttl: int = 1800):
        with cls._cache_lock:
            cls._api_cache[key] = {'value': value, 'expiry': time.time() + ttl}

    @classmethod
    def cleanup_expired(cls):
        with cls._cache_lock:
            for k in list(cls._api_cache.keys()):
                if time.time() > cls._api_cache[k]['expiry']:
                    del cls._api_cache[k]


# ============================== AMADEUS API ==============================
class AmadeusAPI:
    _token: Optional[str] = None
    _token_exp: float = 0.0

    @classmethod
    def _auth(cls) -> str:
        now = time.time()
        if cls._token and now < cls._token_exp - 20:
            return cls._token
        if not (Config.AMADEUS_API_KEY and Config.AMADEUS_API_SECRET):
            raise SystemExit('Faltam AMADEUS_API_KEY/AMADEUS_API_SECRET nos Secrets.')

        url = f'{Config.AMADEUS_BASE_URL}/v1/security/oauth2/token'
        data = {
            'grant_type': 'client_credentials',
            'client_id': Config.AMADEUS_API_KEY,
            'client_secret': Config.AMADEUS_API_SECRET
        }
        r = requests.post(url, data=data, timeout=25)
        r.raise_for_status()
        js = r.json()
        cls._token = js['access_token']
        cls._token_exp = now + int(js.get('expires_in', 1799))
        return cls._token

    @classmethod
    def search_roundtrip_get(cls, origin: str, dest: str, dep: date, ret: date, max_results: int = 10) -> List[Dict[str, Any]]:
        """
        GET /v2/shopping/flight-offers
        Parâmetros usados: originLocationCode, destinationLocationCode, departureDate, returnDate, adults, children, currencyCode, max
        Retorno simplificado por offer:
          { price_total, price_pp, stopovers, airlines }
        """
        cache_key = f"{origin}_{dest}_{dep.isoformat()}_{ret.isoformat()}"
        cached = Cache.get(cache_key)
        if cached is not None:
            return cached

        token = cls._auth()
        url = f'{Config.AMADEUS_BASE_URL}/v2/shopping/flight-offers'
        params = {
            'originLocationCode': origin,
            'destinationLocationCode': dest,
            'departureDate': dep.isoformat(),
            'returnDate': ret.isoformat(),
            'adults': str(Config.ADULTS),
            'children': str(Config.CHILDREN) if Config.CHILDREN > 0 else None,
            'currencyCode': Config.CURRENCY,
            'max': str(max_results)
        }
        params = {k: v for k, v in params.items() if v is not None}
        headers = {'Authorization': f'Bearer {token}'}

        for attempt in range(3):
            try:
                r = requests.get(url, headers=headers, params=params, timeout=35)
                if r.status_code == 429:
                    logging.warning('Amadeus rate-limited (429). Aguardando 5s...')
                    time.sleep(5)
                    continue
                r.raise_for_status()
                js = r.json()
                results = js.get('data', []) or []

                enhanced: List[Dict[str, Any]] = []
                for offer in results:
                    try:
                        price_total = float(offer.get('price', {}).get('total', '0'))
                    except Exception:
                        price_total = 0.0
                    pp = price_total / max(Config.PAX_TOTAL, 1)

                    stopovers = 0
                    airlines = set()
                    for itinerary in offer.get('itineraries', []):
                        segments = itinerary.get('segments', [])
                        stopovers += Utils.get_stopover_count(segments)
                        for seg in segments:
                            code = seg.get('carrierCode', '')
                            if code:
                                airlines.add(code)

                    enhanced.append({
                        'price_total': price_total,
                        'price_pp': pp,
                        'stopovers': int(stopovers),
                        'airlines': sorted(list(airlines)),
                    })

                Cache.set(cache_key, enhanced, ttl=1800)
                return enhanced

            except requests.RequestException as e:
                logging.error(f'Erro Amadeus para {origin}->{dest} ({dep}->{ret}) (tentativa {attempt+1}): {e}')
                if attempt == 2:
                    return []
                time.sleep(2)

        return []


# ============================== HISTÓRICO ==============================
class History:
    def load(self) -> List[Dict[str, Any]]:
        if not Config.HIST_FILE.exists():
            return []
        out: List[Dict[str, Any]] = []
        with open(Config.HIST_FILE, 'r', newline='', encoding='utf-8') as f:
            rd = csv.DictReader(f)
            for r in rd:
                try:
                    r['price_pp'] = float(r['price_pp'])
                    r['ts_utc']   = datetime.fromisoformat(r['ts_utc'])
                    r['date_out'] = datetime.fromisoformat(r['date_out']).date()
                    out.append(r)
                except Exception:
                    # Ignora linhas inválidas
                    continue
        return out

    def save(self, rows: List[Dict[str, Any]]):
        Config.HIST_DIR.mkdir(parents=True, exist_ok=True)
        write_header = not Config.HIST_FILE.exists()
        with open(Config.HIST_FILE, 'a', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    'ts_utc','origin','dest','date_out','date_back','days_to_departure',
                    'bucket_dtd','nights','stopovers','price_total','price_pp',
                    'provider','deep_link','airlines'
                ],
                extrasaction='ignore'
            )
            if write_header:
                w.writeheader()
            for r in rows:
                row = dict(r)
                # Serializa lista de cias para legibilidade no CSV
                if isinstance(row.get('airlines'), list):
                    row['airlines'] = '|'.join(row['airlines'])
                w.writerow(row)


# ============================== ANALISADOR DE TENDÊNCIAS ==============================
class TrendAnalyzer:
    def __init__(self, history: List[Dict[str, Any]]):
        self.history = history
        self._pre_process_history()

    def _pre_process_history(self):
        self.prices_by_bucket: Dict[Tuple[str, str], List[Tuple[datetime, float]]] = {}
        self.prices_by_date:   Dict[Tuple[str, date], List[Tuple[datetime, float]]] = {}
        for h in self.history:
            key_bucket = (h['dest'], h['bucket_dtd'])
            self.prices_by_bucket.setdefault(key_bucket, []).append((h['ts_utc'], h['price_pp']))

            key_date = (h['dest'], h['date_out'])
            self.prices_by_date.setdefault(key_date, []).append((h['ts_utc'], h['price_pp']))

    def calculate_trend(self, dest: str, bucket: str) -> Dict[str, float]:
        key = (dest, bucket)
        relevant = self.prices_by_bucket.get(key, [])
        relevant.sort(key=lambda x: x[0])  # por timestamp

        if len(relevant) < 2:
            return {'mean': float('inf'), 'std_dev': 0.0, 'trend_7d': 0.0, 'trend_30d': 0.0}

        prices = [p for _, p in relevant]
        mean_price = mean(prices)
        std_dev = stdev(prices) if len(prices) > 1 else 0.0

        now = datetime.now(timezone.utc)
        last_week  = [p for t, p in relevant if now - t <= timedelta(days=7)]
        last_month = [p for t, p in relevant if now - t <= timedelta(days=30)]

        def _pct(arr: List[float]) -> float:
            return ((arr[-1] - arr[0]) / arr[0]) * 100.0 if len(arr) > 1 and arr[0] != 0 else 0.0

        return {
            'mean': mean_price,
            'std_dev': std_dev,
            'trend_7d': _pct(last_week),
            'trend_30d': _pct(last_month),
        }


# ============================== FLIGHT SCRAPER ==============================
class FlightScraper:
    def __init__(self, amadeus_api: AmadeusAPI):
        self.amadeus_api = amadeus_api

    def scrape(self) -> List[Dict[str, Any]]:
        now = datetime.now(timezone.utc)
        today = now.date()
        observacoes: List[Dict[str, Any]] = []

        dep_dates = self._sample_departures(today)
        stays     = self._sample_stays()

        logging.info(f'Iniciando coleta | Destinos: {len(Config.DESTINOS)} | Datas: {len(dep_dates)} | Estadas: {len(stays)}')

        total = len(Config.DESTINOS) * len(dep_dates) * len(stays)
        max_workers = min(max(2, total), 6)  # segura 429; aumente se tiver cota maior

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {}
            for dest in Config.DESTINOS:
                for dep in dep_dates:
                    for nights in stays:
                        ret = dep + timedelta(days=nights)
                        fut = executor.submit(
                            self.amadeus_api.search_roundtrip_get,
                            Config.ORIGEM, dest, dep, ret, max_results=10
                        )
                        future_map[fut] = (dest, dep, ret, nights)

            for fut in as_completed(future_map):
                dest, dep, ret, nights = future_map[fut]
                try:
                    offers = fut.result()
                    if not offers:
                        continue

                    for offer in offers:
                        price_total = float(offer.get('price_total', 0.0))
                        pp          = float(offer.get('price_pp', 0.0))
                        dtd         = (dep - today).days
                        bucket      = Utils.bucket_from_dtd(max(dtd, 0))

                        observacoes.append({
                            'ts_utc': now.isoformat(),
                            'origin': Config.ORIGEM,
                            'dest': dest,
                            'date_out': dep.isoformat(),
                            'date_back': ret.isoformat(),
                            'days_to_departure': dtd,
                            'bucket_dtd': bucket,
                            'nights': nights,
                            'stopovers': int(offer.get('stopovers', 0)),
                            'price_total': f'{price_total:.2f}',
                            'price_pp': f'{pp:.2f}',
                            'provider': 'amadeus',
                            'deep_link': '',
                            'airlines': offer.get('airlines', []),
                        })
                except Exception as e:
                    logging.error(f"Erro ao processar futuro: {e}")

        logging.info(f'Coleta finalizada. {len(observacoes)} observações coletadas.')
        return observacoes

    def _sample_departures(self, today: date) -> List[date]:
        start = today + timedelta(days=Config.DAYS_AHEAD_FROM)
        end   = today + timedelta(days=Config.DAYS_AHEAD_TO)
        span  = (end - start).days
        if span <= 0:
            return [start]
        n = max(1, Config.SAMPLE_DEPARTURES)
        steps = [round(i * span / (n + 1)) for i in range(1, n + 1)]
        return [start + timedelta(days=s) for s in steps]

    def _sample_stays(self) -> List[int]:
        if Config.SAMPLE_STAYS <= 1 or Config.STAY_NIGHTS_MIN == Config.STAY_NIGHTS_MAX:
            return [Config.STAY_NIGHTS_MIN]
        return sorted({Config.STAY_NIGHTS_MIN, Config.STAY_NIGHTS_MAX})[:Config.SAMPLE_STAYS]


# ============================== FLIGHT MONITOR ==============================
class FlightMonitor:
    def __init__(self):
        self.amadeus_api = AmadeusAPI()
        self.scraper = FlightScraper(self.amadeus_api)
        self.history_manager = History()
        self.hist = self.history_manager.load()
        self.trend_analyzer = TrendAnalyzer(self.hist)

    def _normalize_for_memory(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for r in rows:
            try:
                out.append({
                    'ts_utc': datetime.fromisoformat(r['ts_utc']),
                    'origin': r['origin'],
                    'dest': r['dest'],
                    'date_out': datetime.fromisoformat(r['date_out']).date(),
                    'bucket_dtd': r['bucket_dtd'],
                    'price_pp': float(r['price_pp']),
                })
            except Exception:
                pass
        return out

    def _baseline(self, dest: str, bucket: str, now_utc: datetime) -> float:
        prices_by_bucket = self.trend_analyzer.prices_by_bucket.get((dest, bucket), [])
        start_time = now_utc - timedelta(days=Config.BASELINE_WINDOW_DAYS)
        vals = [price for ts, price in prices_by_bucket if ts >= start_time]
        return mean(vals) if vals else float('inf')

    def _daydrop(self, dest: str, d_out: date) -> Optional[float]:
        relevant = self.trend_analyzer.prices_by_date.get((dest, d_out), [])
        relevant.sort(key=lambda x: x[0])
        if len(relevant) >= 2:
            prev_price    = relevant[-2][1]
            current_price = relevant[-1][1]
            return (prev_price - current_price) / prev_price if prev_price > 0 else 0.0
        return None

    def _find_opportunities(self, obs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # 1) escolhe a melhor por destino (menor price_pp)
        melhores_por_dest: Dict[str, Dict[str, Any]] = {}
        for o in obs:
            dest = o['dest']
            price_pp = float(o['price_pp'])
            # filtro opcional por conexões
            if int(o.get('stopovers', 0)) > Config.MAX_STOPOVERS:
                continue

            item = {
                'dest': dest,
                'price_total': float(o['price_total']),
                'price_pp': price_pp,
                'ida': datetime.fromisoformat(o['date_out']).strftime('%d/%m/%Y'),
                'volta': datetime.fromisoformat(o['date_back']).strftime('%d/%m/%Y'),
                'nights': int(o['nights']),
                'bucket': o['bucket_dtd'],
                'dep_date': datetime.fromisoformat(o['date_out']).date(),
                'stopovers': int(o['stopovers']),
                'airlines': o.get('airlines', []),
            }
            if dest not in melhores_por_dest or price_pp < melhores_por_dest[dest]['price_pp']:
                melhores_por_dest[dest] = item

        # 2) aplica baseline + daydrop + teto
        oportunidades: List[Dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        for dest, it in melhores_por_dest.items():
            base = self._baseline(dest, it['bucket'], now)
            discount = (base - it['price_pp']) / base if math.isfinite(base) and base > 0 else 0.0

            daydrop = self._daydrop(dest, it['dep_date']) or 0.0

            if (it['price_pp'] <= Config.MAX_PRECO_PP) and (discount >= Config.MIN_DISCOUNT_PCT or daydrop >= Config.MIN_DAYDROP_PCT):
                trend = self.trend_analyzer.calculate_trend(dest, it['bucket'])
                it.update({
                    'discount': discount,
                    'daydrop': daydrop,
                    'trend_7d': trend['trend_7d'],
                    'trend_30d': trend['trend_30d'],
                })
                oportunidades.append(it)

        # 3) ordena e aplica limite global (MAX_PER_DEST = nº máximo de alertas)
        oportunidades.sort(key=lambda x: (-x['discount'], x['price_pp']))
        if Config.MAX_PER_DEST > 0:
            oportunidades = oportunidades[:Config.MAX_PER_DEST]
        return oportunidades

    def montar_msg(self, o: Dict[str, Any]) -> str:
        cias = ", ".join(o.get("airlines", [])) or "-"
        linhas = [
            '🔥 <b>Oportunidade Especial</b>',
            f'📍 <b>{Config.ORIGEM} → {o["dest"]}</b>',
            f'📅 <b>Datas:</b> {o["ida"]} – {o["volta"]}  ({o["nights"]} noites)',
            f'👨‍👩‍👧‍👦 {Config.ADULTS} Adultos + {Config.CHILDREN} Crianças',
            f'💳 <b>Preço por pessoa:</b> {Utils.brl(o["price_pp"])}',
            f'💳 <b>Total ({Config.PAX_TOTAL} pax):</b> {Utils.brl(o["price_total"])}',
            f'🛫 <b>Conexões/paradas:</b> {o["stopovers"]}',
            f'✈️ <b>Companhias:</b> {cias}',
            f'📉 <b>Desconto vs baseline:</b> {o["discount"]*100:.1f}%',
            f'↘️ <b>Queda diária:</b> {o["daydrop"]*100:.1f}%',
            f'<i>Tendência 7d: {o["trend_7d"]:+.1f}%</i>',
            f'<i>Tendência 30d: {o["trend_30d"]:+.1f}%</i>',
            '<i>Fonte: Amadeus (Self-Service API) • Monitor via GitHub Actions</i>'
        ]
        return '\n'.join(linhas)

    def run(self):
        logging.info(f'Monitor iniciado | origem={Config.ORIGEM} | destinos={len(Config.DESTINOS)} | janela={Config.DAYS_AHEAD_FROM}-{Config.DAYS_AHEAD_TO}d | stay={Config.STAY_NIGHTS_MIN}-{Config.STAY_NIGHTS_MAX}')
        try:
            obs = self.scraper.scrape()
            if obs:
                self.history_manager.save(obs)
                self.hist.extend(self._normalize_for_memory(obs))
                self.trend_analyzer = TrendAnalyzer(self.hist)
                logging.info(f'Histórico atualizado com {len(obs)} observações.')

            oportunidades = self._find_opportunities(obs)

            if not oportunidades:
                Utils.enviar_telegram('⚠️ Monitor rodou, mas nenhuma oportunidade detectada com os critérios atuais.')
                return

            for o in oportunidades:
                Utils.enviar_telegram(self.montar_msg(o))

            Utils.enviar_telegram(f'✅ Monitor finalizado com sucesso ({len(oportunidades)} oportunidades detectadas).')

        except Exception as e:
            logging.critical(f'❌ ERRO CRÍTICO NO MONITOR: {e}')
            Utils.enviar_telegram(f'❌ Ocorreu um erro crítico no monitor. Verifique os logs.\nErro: {e}')


# ============================== MAIN ==============================
if __name__ == '__main__':
    FlightMonitor().run()