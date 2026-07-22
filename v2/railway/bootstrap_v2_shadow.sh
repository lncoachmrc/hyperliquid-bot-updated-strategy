#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_NAME="${V2_RAILWAY_PROJECT_NAME:-hyperliquid-v2-shadow}"
SERVICE_NAME="${V2_RAILWAY_SERVICE_NAME:-v2-shadow}"
POSTGRES_SERVICE="${V2_POSTGRES_SERVICE_NAME:-Postgres}"
SUPERVISOR_TOKEN="${V2_SUPERVISOR_TOKEN:-}"
PRIMARY_PROVIDER="${V2_PRIMARY_PROVIDER:-}"
PRIMARY_MODEL="${V2_PRIMARY_MODEL:-}"
PRIMARY_KEY=""
CHALLENGER_PROVIDER="${V2_CHALLENGER_PROVIDER:-}"
CHALLENGER_MODEL="${V2_CHALLENGER_MODEL:-}"
CHALLENGER_KEY=""
OBSERVER_PROVIDER="${V2_OBSERVER_PROVIDER:-}"
OBSERVER_MODEL="${V2_OBSERVER_MODEL:-}"
OBSERVER_KEY=""
GITHUB_TOKEN_VALUE="${V2_GITHUB_TOKEN:-}"

cleanup() {
  if [[ -n "${WORK_DIR:-}" && -d "${WORK_DIR:-}" ]]; then
    rm -rf "$WORK_DIR"
  fi
}
trap cleanup EXIT

fail() {
  printf '\nERRORE: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Comando mancante: $1"
}

prompt_default() {
  local variable_name="$1"
  local label="$2"
  local default_value="$3"
  local current_value="${!variable_name:-}"
  if [[ -n "$current_value" ]]; then
    return
  fi
  local answer
  read -r -p "$label [$default_value]: " answer
  printf -v "$variable_name" '%s' "${answer:-$default_value}"
}

prompt_secret() {
  local variable_name="$1"
  local label="$2"
  local current_value="${!variable_name:-}"
  if [[ -n "$current_value" ]]; then
    return
  fi
  local answer
  read -r -s -p "$label (Invio per saltare): " answer
  printf '\n'
  printf -v "$variable_name" '%s' "$answer"
}

provider_key_variable() {
  case "$1" in
    openai) printf 'OPENAI_API_KEY' ;;
    anthropic) printf 'ANTHROPIC_API_KEY' ;;
    deepseek) printf 'DEEPSEEK_API_KEY' ;;
    deterministic|'') printf '' ;;
    *) fail "Provider non supportato: $1" ;;
  esac
}

suggested_model() {
  case "$1" in
    deepseek) printf 'deepseek-v4-pro' ;;
    openai) printf 'gpt-5' ;;
    anthropic) printf 'claude-sonnet-4-5' ;;
    deterministic|'') printf 'v2-economic-fallback' ;;
    *) printf '' ;;
  esac
}

read_provider() {
  local role="$1"
  local provider_var="$2"
  local model_var="$3"
  local key_var="$4"
  local optional="$5"

  local provider="${!provider_var:-}"
  if [[ -z "$provider" ]]; then
    local prompt="Provider $role (openai/anthropic/deepseek"
    [[ "$optional" == "true" ]] && prompt+="/none"
    prompt+=")"
    local default_provider="deepseek"
    [[ "$optional" == "true" ]] && default_provider="none"
    read -r -p "$prompt [$default_provider]: " provider
    provider="${provider:-$default_provider}"
  fi
  provider="$(printf '%s' "$provider" | tr '[:upper:]' '[:lower:]')"
  [[ "$provider" == "none" ]] && provider=""
  printf -v "$provider_var" '%s' "$provider"

  if [[ -z "$provider" ]]; then
    printf -v "$model_var" '%s' ""
    printf -v "$key_var" '%s' ""
    return
  fi

  local model="${!model_var:-}"
  local default_model
  default_model="$(suggested_model "$provider")"
  if [[ -z "$model" ]]; then
    read -r -p "Model ID $role [$default_model]: " model
    model="${model:-$default_model}"
  fi
  printf -v "$model_var" '%s' "$model"

  if [[ "$provider" == "deterministic" ]]; then
    printf -v "$key_var" '%s' ""
    return
  fi

  local key="${!key_var:-}"
  if [[ -z "$key" ]]; then
    read -r -s -p "API key $provider per $role (Invio = fallback deterministico): " key
    printf '\n'
  fi
  printf -v "$key_var" '%s' "$key"
}

require_command railway
require_command git
require_command curl
require_command python3
require_command openssl

railway whoami >/dev/null 2>&1 || fail "Esegui prima: railway login"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || fail "Esegui lo script dentro la repository Git locale."
[[ -f "$REPO_ROOT/v2/Dockerfile" ]] || fail "Directory v2 non trovata. Esegui git pull sul branch main."

printf '\n=== Hyperliquid V2 → Railway (SHADOW ONLY) ===\n\n'
printf 'Verrà creato un PROGETTO RAILWAY SEPARATO.\n'
printf 'Il servizio V1 e il suo PostgreSQL non verranno toccati.\n'
printf 'La V2 non riceverà né richiederà PRIVATE_KEY.\n\n'

prompt_default PROJECT_NAME "Nome nuovo progetto Railway" "$PROJECT_NAME"
prompt_default SERVICE_NAME "Nome servizio V2" "$SERVICE_NAME"
prompt_default POSTGRES_SERVICE "Nome servizio PostgreSQL creato da Railway" "$POSTGRES_SERVICE"

WALLET_ADDRESS="${V2_WALLET_ADDRESS:-${WALLET_ADDRESS:-}}"
while [[ ! "$WALLET_ADDRESS" =~ ^0x[0-9a-fA-F]{40}$ ]]; do
  read -r -p "Wallet pubblico Hyperliquid (0x...): " WALLET_ADDRESS
  [[ "$WALLET_ADDRESS" =~ ^0x[0-9a-fA-F]{40}$ ]] || printf 'Formato wallet non valido.\n'
done

read_provider "primario" PRIMARY_PROVIDER PRIMARY_MODEL PRIMARY_KEY false
read_provider "challenger" CHALLENGER_PROVIDER CHALLENGER_MODEL CHALLENGER_KEY true
read_provider "observer indipendente" OBSERVER_PROVIDER OBSERVER_MODEL OBSERVER_KEY true

if [[ -z "$SUPERVISOR_TOKEN" ]]; then
  SUPERVISOR_TOKEN="$(openssl rand -hex 32)"
fi

if [[ -z "$GITHUB_TOKEN_VALUE" ]]; then
  prompt_secret GITHUB_TOKEN_VALUE "Fine-grained GitHub token per draft PR del Supervisor"
fi

printf '\nRiepilogo sicuro:\n'
printf '  progetto:        %s\n' "$PROJECT_NAME"
printf '  servizio:        %s\n' "$SERVICE_NAME"
printf '  database:        %s\n' "$POSTGRES_SERVICE"
printf '  wallet pubblico: %s...%s\n' "${WALLET_ADDRESS:0:6}" "${WALLET_ADDRESS: -4}"
printf '  primary:         %s / %s\n' "${PRIMARY_PROVIDER:-deterministic}" "${PRIMARY_MODEL:-v2-economic-fallback}"
printf '  challenger:      %s / %s\n' "${CHALLENGER_PROVIDER:-disattivato}" "${CHALLENGER_MODEL:-}"
printf '  observer:        %s / %s\n' "${OBSERVER_PROVIDER:-disattivato}" "${OBSERVER_MODEL:-}"
printf '  trading live:    DISABILITATO\n\n'
read -r -p "Creare e distribuire il nuovo progetto Railway? [s/N]: " CONFIRM
[[ "$CONFIRM" =~ ^[sSyY]$ ]] || fail "Operazione annullata."

WORK_DIR="$(mktemp -d -t hyperliquid-v2-railway.XXXXXX)"
cp -R "$REPO_ROOT/v2/." "$WORK_DIR/"
cd "$WORK_DIR"

printf '\n[1/7] Creo il progetto Railway separato...\n'
if [[ -n "${RAILWAY_WORKSPACE:-}" ]]; then
  railway init -n "$PROJECT_NAME" -w "$RAILWAY_WORKSPACE"
else
  railway init -n "$PROJECT_NAME"
fi

printf '\n[2/7] Creo PostgreSQL dedicato...\n'
railway add -d postgres

printf '\n[3/7] Creo il servizio shadow...\n'
railway add -s "$SERVICE_NAME"

VARIABLES=(
  'DATABASE_URL=${{Postgres.DATABASE_URL}}'
  "V2_WALLET_ADDRESS=$WALLET_ADDRESS"
  'V2_SYMBOLS=BTC,ETH,SOL'
  'V2_SHADOW_ONLY=true'
  'V2_LIVE_TRADING_ENABLED=false'
  'V2_FEATURE_INTERVAL_SECONDS=15'
  'V2_POSITION_REVIEW_SECONDS=60'
  'V2_ENTRY_REVIEW_SECONDS=300'
  'V2_DEFAULT_STOP_PCT=0.60'
  'V2_ROUND_TRIP_COST_BPS=10'
  'V2_MAX_RISK_FRACTION=0.005'
  'V2_MAX_EFFECTIVE_EXPOSURE=0.50'
  'V2_QUANT_MINIMUM_SAMPLES=50'
  "V2_SUPERVISOR_TOKEN=$SUPERVISOR_TOKEN"
  'V2_GITHUB_REPOSITORY=lncoachmrc/hyperliquid-bot-updated-strategy'
  'V2_GITHUB_BASE_BRANCH=main'
)

if [[ -n "$PRIMARY_PROVIDER" && "$PRIMARY_PROVIDER" != "deterministic" ]]; then
  VARIABLES+=("V2_PRIMARY_PROVIDER=$PRIMARY_PROVIDER" "V2_PRIMARY_MODEL=$PRIMARY_MODEL")
  KEY_NAME="$(provider_key_variable "$PRIMARY_PROVIDER")"
  [[ -n "$PRIMARY_KEY" ]] && VARIABLES+=("$KEY_NAME=$PRIMARY_KEY")
else
  VARIABLES+=('V2_PRIMARY_PROVIDER=deterministic' 'V2_PRIMARY_MODEL=v2-economic-fallback')
fi

if [[ -n "$CHALLENGER_PROVIDER" ]]; then
  VARIABLES+=("V2_CHALLENGER_PROVIDER=$CHALLENGER_PROVIDER" "V2_CHALLENGER_MODEL=$CHALLENGER_MODEL")
  KEY_NAME="$(provider_key_variable "$CHALLENGER_PROVIDER")"
  [[ -n "$CHALLENGER_KEY" ]] && VARIABLES+=("$KEY_NAME=$CHALLENGER_KEY")
fi

if [[ -n "$OBSERVER_PROVIDER" ]]; then
  VARIABLES+=("V2_OBSERVER_PROVIDER=$OBSERVER_PROVIDER" "V2_OBSERVER_MODEL=$OBSERVER_MODEL")
  KEY_NAME="$(provider_key_variable "$OBSERVER_PROVIDER")"
  [[ -n "$OBSERVER_KEY" ]] && VARIABLES+=("$KEY_NAME=$OBSERVER_KEY")
fi

if [[ -n "$GITHUB_TOKEN_VALUE" ]]; then
  VARIABLES+=("V2_GITHUB_TOKEN=$GITHUB_TOKEN_VALUE")
fi

printf '\n[4/7] Imposto le variabili shadow e il riferimento al database...\n'
railway variable set -s "$SERVICE_NAME" "${VARIABLES[@]}"

printf '\n[5/7] Distribuisco il servizio V2...\n'
railway up . --path-as-root -s "$SERVICE_NAME" --ci

printf '\n[6/7] Genero il dominio pubblico per health e n8n...\n'
DOMAIN_OUTPUT="$(railway domain -s "$SERVICE_NAME" 2>&1 || true)"
printf '%s\n' "$DOMAIN_OUTPUT"
DOMAIN="$(printf '%s\n' "$DOMAIN_OUTPUT" | grep -Eo 'https?://[^[:space:]]+|[A-Za-z0-9.-]+\.up\.railway\.app' | tail -1 || true)"
if [[ -n "$DOMAIN" && "$DOMAIN" != http* ]]; then
  DOMAIN="https://$DOMAIN"
fi

printf '\n[7/7] Verifico /health...\n'
HEALTH_OK=false
if [[ -n "$DOMAIN" ]]; then
  for ATTEMPT in $(seq 1 30); do
    if curl --silent --show-error --fail --max-time 10 "$DOMAIN/health" > /tmp/v2-health.json 2>/dev/null; then
      cat /tmp/v2-health.json
      printf '\n'
      HEALTH_OK=true
      break
    fi
    printf 'Attendo il servizio... (%s/30)\n' "$ATTEMPT"
    sleep 5
  done
fi

printf '\n=== RISULTATO ===\n'
railway status || true
printf '\nSupervisor token (salvalo per n8n):\n%s\n' "$SUPERVISOR_TOKEN"
if [[ "$HEALTH_OK" == "true" ]]; then
  printf '\nV2 SHADOW OPERATIVA: %s/health\n' "$DOMAIN"
  printf 'Stato completo:       %s/status\n' "$DOMAIN"
  printf 'Supervisor endpoint:  %s/supervisor/run\n' "$DOMAIN"
else
  printf '\nDeploy inviato, ma /health non è stato confermato automaticamente.\n'
  printf 'Apri Railway e controlla i log del servizio %s.\n' "$SERVICE_NAME"
  exit 2
fi
