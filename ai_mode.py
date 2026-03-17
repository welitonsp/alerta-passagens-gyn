#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai_mode.py — Modo IA plugável para o monitor de passagens.
Adaptado para ler da base de dados SQLite.
"""

from __future__ import annotations
import os
import math
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Dict, Tuple, Optional, List
import warnings

# Importar configurações da nossa base de dados
from database import DB_PATH, logger

warnings.filterwarnings("ignore", category=UserWarning)

# ========== UTILITÁRIOS LEVES ==========
def _parse_date(s: str) -> datetime:
    """Converte string ISO em objeto datetime"""
    return datetime.fromisoformat(s[:10])

def _is_holiday(date_str: str) -> bool:
    """Verifica se a data corresponde a um feriado nacional fixo"""
    fixed_holidays = {"01-01", "21-04", "01-05", "07-09", "12-10", "02-11", "15-11", "25-12"}
    return _parse_date(date_str).strftime("%d-%m") in fixed_holidays

def _weekend_factor(date_str: str) -> float:
    """Fator de aumento de preço para fins de semana"""
    weekday = _parse_date(date_str).weekday()
    return 1.15 if weekday >= 5 else 1.0  # Sábado/Domingo: +15%

def _days_ahead_factor(date_str: str, today: Optional[datetime] = None) -> float:
    """Fator baseado na antecedência da viagem"""
    today = today or datetime.utcnow()
    days_ahead = (_parse_date(date_str).date() - today.date()).days
    
    if days_ahead <= 7:       
        return 1.25
    elif days_ahead <= 21:    
        return 1.10
    elif days_ahead >= 90:    
        return 0.92
    return 1.0                 

def _holiday_factor(date_str: str) -> float:
    """Fator de aumento para feriados"""
    return 1.20 if _is_holiday(date_str) else 1.0


# ========== PREDITOR HEURÍSTICO (PADRÃO) ==========
class HeuristicPredictor:
    def __init__(self, currency: str = "BRL"):
        self.currency = currency
        self.route_median: Dict[Tuple[str, str], float] = {}
        self.global_median: float = 950.0  
        
        self._load_history()
    
    def _load_history(self):
        """Carrega dados históricos do SQLite para calcular medianas"""
        if not os.path.exists(DB_PATH):
            return
        
        route_prices: Dict[Tuple[str, str], List[float]] = {}
        all_prices: List[float] = []
        
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT origem, destino, preco FROM historico WHERE preco IS NOT NULL")
            rows = cursor.fetchall()
            conn.close()
            
            for row in rows:
                orig = row["origem"]
                dest = row["destino"]
                price = float(row["preco"])
                
                key = (orig, dest)
                route_prices.setdefault(key, []).append(price)
                all_prices.append(price)
        except Exception as e:
            logger.error(f"Erro ao carregar histórico SQLite para IA: {e}")
            pass
        
        def calculate_median(prices: List[float]) -> float:
            if not prices:
                return self.global_median
            sorted_prices = sorted(prices)
            mid = len(sorted_prices) // 2
            return sorted_prices[mid] if len(sorted_prices) % 2 != 0 else \
                   (sorted_prices[mid - 1] + sorted_prices[mid]) / 2
        
        if all_prices:
            self.global_median = calculate_median(all_prices)
        
        for route, prices in route_prices.items():
            self.route_median[route] = calculate_median(prices)
    
    def _predict_leg(self, origin: str, destination: str, date_str: str) -> float:
        base_price = self.route_median.get((origin, destination), self.global_median / 2)
        
        factors = [
            _weekend_factor(date_str),
            _holiday_factor(date_str),
            _days_ahead_factor(date_str)
        ]
        
        adjusted_price = base_price * math.prod(factors)
        return max(150.0, adjusted_price)
    
    def predict_total(self, origin: str, destination: str, 
                     departure_date: str, return_date: str) -> float:
        ida_price = self._predict_leg(origin, destination, departure_date)
        return_price = self._predict_leg(destination, origin, return_date)
        return ida_price + return_price


# ========== PREDITOR SKLEARN (OPCIONAL) ==========
class SklearnPredictor:
    def __init__(self, currency: str = "BRL"):
        self.currency = currency
        
        try:
            import numpy as np
            import pandas as pd
            from sklearn.ensemble import RandomForestRegressor
            import joblib
            self.np = np
            self.pd = pd
            self.RandomForestRegressor = RandomForestRegressor
            self.joblib = joblib
        except ImportError:
            raise ImportError("Bibliotecas ML (numpy, pandas, scikit-learn) não instaladas")
        
        self.heuristic = HeuristicPredictor(currency)
        self.model = None
        self._train_if_possible()
    
    def _train_if_possible(self):
        if not os.path.exists(DB_PATH):
            return
        
        try:
            X, y = self._prepare_training_data()
            if len(y) >= 50:
                self.model = self.RandomForestRegressor(
                    n_estimators=120,
                    random_state=42,
                    n_jobs=-1
                )
                self.model.fit(X, y)
            else:
                self.model = None
        except Exception as e:
            self.model = None
            logger.error(f"Erro ao treinar modelo ML: {e}")
    
    def _prepare_training_data(self):
        X_list = []
        y_list = []
        
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT data, preco FROM historico WHERE preco IS NOT NULL AND data IS NOT NULL")
        rows = cursor.fetchall()
        conn.close()
        
        for row in rows:
            try:
                date_str = row["data"]
                features = self._create_features(date_str)
                X_list.append(features)
                y_list.append(float(row["preco"]))
            except Exception:
                continue
                
        return self.np.array(X_list), self.np.array(y_list)
    
    def _create_features(self, date_str: str):
        today = datetime.utcnow().date()
        date_obj = _parse_date(date_str).date()
        days_ahead = (date_obj - today).days
        return [
            days_ahead,
            date_obj.weekday(),
            date_obj.month,
            1 if _is_holiday(date_str) else 0
        ]

    def _predict_leg(self, origin: str, destination: str, date_str: str) -> float:
        if self.model is not None:
            features = self._create_features(date_str)
            ml_prediction = self.model.predict([features])[0]
            
            heuristic_pred = self.heuristic._predict_leg(origin, destination, date_str)
            blended_pred = 0.6 * ml_prediction + 0.4 * heuristic_pred
            
            return max(blended_pred, 150.0) 
        
        return self.heuristic._predict_leg(origin, destination, date_str)
    
    def predict_total(self, origin: str, destination: str, 
                     departure_date: str, return_date: str) -> float:
        ida = self._predict_leg(origin, destination, departure_date)
        volta = self._predict_leg(destination, origin, return_date)
        return ida + volta


# ========== FÁBRICA DE PREDITORES ==========
def load_predictor(history_path: Path, currency: str, engine: Optional[str] = None):
    """
    Nota: history_path é mantido na assinatura para retrocompatibilidade,
    mas os dados são lidos unicamente da base de dados SQLite.
    """
    engine = (engine or os.getenv("AI_ENGINE", "heuristic")).strip().lower()
    
    if engine == "sklearn":
        try:
            return SklearnPredictor(currency), "scikit-learn (Random Forest)"
        except ImportError:
            pred = HeuristicPredictor(currency)
            return pred, "heuristic (sklearn indisponível)"
    
    return HeuristicPredictor(currency), "heurístico (padrão)"

if __name__ == "__main__":
    predictor, engine_info = load_predictor(None, "BRL", "sklearn")
    print(f"Motor de predição: {engine_info}")
