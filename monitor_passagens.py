# -----------------------
# Config
# -----------------------
def brazil_capitals_iata():
    return [
        "GIG","SDU","SSA","FOR","REC","NAT","MCZ","AJU",
        "MAO","BEL","SLZ","THE","BSB","FLN","POA","CWB",
        "CGR","CGB","CNF","VIX","JPA","PMW","PVH","BVB",
        "RBR","GYN","GRU","CGH"
    ]

def dedupe_keep_order(seq):
    seen = set(); out = []
    for x in seq:
        if x and x not in seen:
            seen.add(x); out.append(x)
    return out

class Config:
    ORIGEM = os.getenv("ORIGEM", "GYN").strip().upper()

    _dests_str = os.getenv("DESTINOS", ",".join(brazil_capitals_iata()))
    # apenas normaliza aqui; NÃO filtre por ORIGEM dentro da classe
    DESTINOS = [x.strip().upper() for x in _dests_str.split(",") if x.strip()]

    DAYS_AHEAD_FROM = int(os.getenv("DAYS_AHEAD_FROM", "10"))
    DAYS_AHEAD_TO   = int(os.getenv("DAYS_AHEAD_TO", "90"))
    SAMPLE_DEPARTURES = int(os.getenv("SAMPLE_DEPARTURES", "2"))
    MAX_OFFERS = int(os.getenv("MAX_OFFERS", "5"))
    CURRENCY = os.getenv("CURRENCY", "BRL")
    REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "1.2"))

# >>> pós-processamento fora da classe (agora pode usar Config.ORIGEM)
Config.DESTINOS = dedupe_keep_order([d for d in Config.DESTINOS if d != Config.ORIGEM])
