# engine_google.py
from serpapi import GoogleSearch
import os
from database import logger

def buscar_voo_google(origem, destino, data_ida):
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key:
        logger.error("SERPAPI_KEY não configurada.")
        return None

    params = {
        "engine": "google_flights",
        "departure_id": origem,
        "arrival_id": destino,
        "outbound_date": data_ida,
        "currency": "BRL",
        "hl": "pt",
        "api_key": api_key
    }

    try:
        search = GoogleSearch(params)
        results = search.get_dict()
        
        # Pegamos a melhor opção (mais barata e otimizada pelo Google)
        if "best_flights" in results:
            best = results["best_flights"][0]
            return {
                "preco": best["price"],
                "companhia": best["flights"][0]["airline"],
                "link": results.get("search_metadata", {}).get("google_flights_url")
            }
    except Exception as e:
        logger.error(f"Erro na SerpApi: {e}")
    
    return None