- name: Validar secrets (fail fast)
  run: |
    for V in AMADEUS_API_KEY AMADEUS_API_SECRET TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID; do
      if [ -z "${!V}" ]; then
        echo "::error::Secret $V não definido"
        exit 1
      fi
    done

- name: Checar API Amadeus (diagnóstico)
  env:
    AMADEUS_ENV: test   # use "prod" se estiver em produção
  run: |
    set -x
    python check_amadeus_api.py
