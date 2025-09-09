#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai_mode.py — Modo IA plugável para o monitor de passagens.
Oferece duas implementações de predição de preços:
1. HeuristicPredictor (padrão): Baseado em heurísticas e estatísticas históricas
2. SklearnPredictor (opcional): Usa machine learning com Random Forest

Interface pública:
    load_predictor(history_path: Path, currency: str, engine: str) -> (predictor, info_str)
    
O predictor expõe o método:
    predict_total(orig: str, dest: str, depart_date: str, return_date: str) -> float
"""

from __future__ import annotations
import os
import csv
import math
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional, List
import warnings

warnings.filterwarnings("ignore", category=UserWarning)


# ========== UTILITÁRIOS LEVES ==========
def _parse_date(s: str) -> datetime:
    """Converte string ISO em objeto datetime"""
    return datetime.strptime(s, "%Y-%m-%d")


def _is_holiday(date_str: str) -> bool:
    """Verifica se a data corresponde a um feriado nacional"""
    # Feriados nacionais fixos (exemplo simplificado)
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
    
    if days_ahead <= 7:       # Poucos dias: +25%
        return 1.25
    elif days_ahead <= 21:    # Até 3 semanas: +10%
        return 1.10
    elif days_ahead >= 90:    # Mais de 3 meses: -8%
        return 0.92
    return 1.0                 # Valor padrão


def _holiday_factor(date_str: str) -> float:
    """Fator de aumento para feriados"""
    return 1.20 if _is_holiday(date_str) else 1.0


# ========== PREDITOR HEURÍSTICO (PADRÃO) ==========
class HeuristicPredictor:
    """
    Preditor de preços baseado em heurísticas e estatísticas históricas.
    Não requer bibliotecas externas além das padrões do Python.
    
    Características:
    - Mediana histórica por rota (se disponível)
    - Multiplicadores para fim de semana, feriados e antecedência
    - Fallback para média global quando dados insuficientes
    """
    
    def __init__(self, history_path: Path, currency: str = "BRL"):
        self.currency = currency
        self.route_median: Dict[Tuple[str, str], float] = {}
        self.global_median: float = 950.0  # Valor médio fallback para voos domésticos
        
        # Carrega dados históricos
        self._load_history(history_path)
    
    def _load_history(self, history_path: Path):
        """Carrega dados históricos do CSV para calcular medianas"""
        if not history_path.exists():
            return
        
        route_prices: Dict[Tuple[str, str], List[float]] = {}
        all_prices: List[float] = []
        
        try:
            with history_path.open("r", encoding="utf-8") as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    try:
                        # Extrai informações da linha
                        orig = row["origem"]
                        dest = row["destino"]
                        price = float(row["price_total"])
                        
                        # Armazena para cálculo de medianas
                        key = (orig, dest)
                        route_prices.setdefault(key, []).append(price)
                        all_prices.append(price)
                    except (KeyError, ValueError):
                        continue  # Ignora linhas inválidas
        except Exception:
            # se der erro ao abrir/ler, seguimos com medianas padrão
            pass
        
        # Calcula medianas
        def calculate_median(prices: List[float]) -> float:
            if not prices:
                return self.global_median
            sorted_prices = sorted(prices)
            mid = len(sorted_prices) // 2
            return sorted_prices[mid] if len(sorted_prices) % 2 != 0 else \
                   (sorted_prices[mid - 1] + sorted_prices[mid]) / 2
        
        # Mediana global
        if all_prices:
            self.global_median = calculate_median(all_prices)
        
        # Medianas por rota
        for route, prices in route_prices.items():
            self.route_median[route] = calculate_median(prices)
    
    def _predict_leg(self, origin: str, destination: str, date_str: str) -> float:
        """
        Prediz o preço de uma única perna (ida ou volta)
        Combina mediana histórica com fatores de ajuste
        """
        # Base: mediana histórica da rota ou média global
        base_price = self.route_median.get((origin, destination), self.global_median / 2)
        
        # Aplica fatores de ajuste
        factors = [
            _weekend_factor(date_str),
            _holiday_factor(date_str),
            _days_ahead_factor(date_str)
        ]
        
        adjusted_price = base_price * math.prod(factors)
        
        # Garante um mínimo razoável
        return max(150.0, adjusted_price)
    
    def predict_total(self, origin: str, destination: str, 
                     departure_date: str, return_date: str) -> float:
        """
        Prediz o preço total de ida e volta
        """
        ida_price = self._predict_leg(origin, destination, departure_date)
        return_price = self._predict_leg(destination, origin, return_date)
        
        return ida_price + return_price


# ========== PREDITOR SKLEARN (OPCIONAL) ==========
class SklearnPredictor:
    """
    Preditor de preços usando machine learning com Random Forest.
    Requer bibliotecas adicionais: numpy, pandas, scikit-learn, joblib.
    
    Funciona como fallback para o preditor heurístico caso não consiga treinar um modelo robusto.
    """
    
    def __init__(self, history_path: Path, currency: str = "BRL"):
        self.currency = currency
        
        # Tenta importar bibliotecas necessárias
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
            raise ImportError("Bibliotecas necessárias (numpy, pandas, scikit-learn, joblib) não estão instaladas")
        
        # Inicializa o modelo com fallback para heurístico
        self.heuristic = HeuristicPredictor(history_path, currency)
        self.model = None
        self._train_if_possible(history_path)
    
    def _train_if_possible(self, history_path: Path):
        """Tenta treinar um modelo se houver dados suficientes"""
        if not history_path.exists():
            return
        
        try:
            # Prepara dados de treinamento
            X, y = self._prepare_training_data(history_path)
            
            # Treina apenas se houver dados suficientes
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
            print(f"Erro ao treinar modelo: {e}")
    
    def _prepare_training_data(self, history_path: Path):
        """Prepara conjunto de dados para treinamento"""
        X_list = []
        y_list = []
        
        with history_path.open("r", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                try:
                    # Extrai datas
                    departure_date = row["departure_date"]
                    return_date = row["return_date"]
                    
                    # Converte para features
                    features = self._create_features(departure_date, return_date)
                    X_list.append(features)
                    
                    # Preço total como target
                    y_list.append(float(row["price_total"]))
                except (KeyError, ValueError):
                    continue
        
        return self.np.array(X_list), self.np.array(y_list)
    
    def _create_features(self, departure_date: str, return_date: str):
        """Cria features para o modelo"""
        today = datetime.utcnow().date()
        
        def date_features(date_str: str):
            date_obj = _parse_date(date_str).date()
            days_ahead = (date_obj - today).days
            return [
                days_ahead,
                date_obj.weekday(),  # Segunda=0, Domingo=6
                date_obj.month,
                1 if _is_holiday(date_str) else 0
            ]
        
        # Features para ida e volta
        ida_feats = date_features(departure_date)
        volta_feats = date_features(return_date)
        
        return ida_feats + volta_feats
    
    def predict_total(self, origin: str, destination: str, 
                     departure_date: str, return_date: str) -> float:
        """Prediz preço total combinando ML e heurística"""
        if self.model is not None:
            # Usa modelo ML com fallback para heurística
            features = self._create_features(departure_date, return_date)
            ml_prediction = self.model.predict([features])[0]
            
            # Mistura com predição heurística para suavizar
            heuristic_pred = self.heuristic.predict_total(origin, destination, 
                                                        departure_date, return_date)
            blended_pred = 0.6 * ml_prediction + 0.4 * heuristic_pred
            
            return max(blended_pred, 200.0)  # Garante mínimo razoável
        
        # Fallback para heurístico se modelo não estiver disponível
        return self.heuristic.predict_total(origin, destination, 
                                          departure_date, return_date)


# ========== FÁBRICA DE PREDITORES ==========
def load_predictor(history_path: Path, currency: str, engine: Optional[str] = None):
    """
    Factory function para criar instância de preditor.
    
    Args:
        history_path: Caminho para arquivo CSV com histórico de preços
        currency: Moeda para previsão (padrão: BRL)
        engine: Motor de predição ('heuristic' ou 'sklearn')
    
    Returns:
        tuple(predictor, info_str): Instância do preditor e descrição do motor usado
    """
    engine = (engine or os.getenv("AI_ENGINE", "heuristic")).strip().lower()
    
    if engine == "sklearn":
        try:
            return SklearnPredictor(history_path, currency), "scikit-learn (Random Forest)"
        except ImportError:
            # Fallback silencioso para heurístico
            pred = HeuristicPredictor(history_path, currency)
            return pred, "heuristic (sklearn indisponível)"
    
    # Padrão: heurístico
    return HeuristicPredictor(history_path, currency), "heurístico (padrão)"


# Exemplo de uso (para testes):
if __name__ == "__main__":
    # Configuração
    history_file = Path("data/history.csv")
    currency = "BRL"
    
    # Carrega preditor
    predictor, engine_info = load_predictor(history_file, currency, "sklearn")
    print(f"Motor de predição: {engine_info}")
    
    # Exemplo de predição
    origem = "GYN"
    destino = "GRU"
    ida = "2024-06-15"
    volta = "2024-06-22"
    
    preco_previsto = predictor.predict_total(origem, destino, ida, volta)
    print(f"\nPrevisão para {origem}-{destino} ({ida} ↔ {volta}):")
    print(f"Preço total estimado: R$ {preco_previsto:.2f}")