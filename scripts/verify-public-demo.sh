#!/usr/bin/env bash
# Verify an anonymous public read-only demo (local compose or hosted).
# Proves health/readiness, seeded reads, fail-closed mutations, optional web headers.
# Never prints token values. Exit 0 only when all required checks pass.
#
# Usage:
#   ./scripts/verify-public-demo.sh
#   API_BASE_URL=https://ledger-api.onrender.com WEB_BASE_URL=https://ledger.leihuang.me \
#     ./scripts/verify-public-demo.sh
#
# Env:
#   API_BASE_URL   default http://localhost:8000
#   WEB_BASE_URL   optional; when set, checks security headers + HTML secret scan
#   REQUIRE_WEB    if "true", WEB_BASE_URL is required
#   STRICT_CORS    if "true", also OPTIONS-probe with Origin: $EXPECTED_ORIGIN
#   EXPECTED_ORIGIN  default https://ledger.leihuang.me (CORS probe only)

set -euo pipefail

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
API_BASE_URL="${API_BASE_URL%/}"
WEB_BASE_URL="${WEB_BASE_URL:-}"
WEB_BASE_URL="${WEB_BASE_URL%/}"
REQUIRE_WEB="${REQUIRE_WEB:-false}"
STRICT_CORS="${STRICT_CORS:-false}"
EXPECTED_ORIGIN="${EXPECTED_ORIGIN:-https://ledger.leihuang.me}"

PASS=0
FAIL=0
SKIP=0

log() { printf '%s\n' "$*"; }
ok() { PASS=$((PASS + 1)); log "OK  $*"; }
bad() { FAIL=$((FAIL + 1)); log "FAIL $*"; }
skip() { SKIP=$((SKIP + 1)); log "SKIP $*"; }

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    log "missing required command: $1"
    exit 2
  fi
}

need_cmd curl
need_cmd python3

http_code() {
  # usage: http_code METHOD URL [curl args...]
  local method="$1"
  local url="$2"
  shift 2
  curl -sS -o /tmp/ledger-public-demo-body.$$ -w '%{http_code}' \
    --max-time 30 -X "$method" "$url" "$@" || true
}

body_file() { printf '%s' "/tmp/ledger-public-demo-body.$$"; }

cleanup() { rm -f /tmp/ledger-public-demo-body.$$ /tmp/ledger-public-demo-web.$$; }
trap cleanup EXIT

json_field() {
  # usage: json_field path  (reads body file; path is dotted for simple keys)
  local path="$1"
  python3 - "$path" "$(body_file)" <<'PY'
import json, sys
path, fp = sys.argv[1], sys.argv[2]
with open(fp, encoding="utf-8") as f:
    data = json.load(f)
cur = data
for part in path.split("."):
    if isinstance(cur, dict) and part in cur:
        cur = cur[part]
    else:
        sys.exit(1)
if isinstance(cur, (dict, list)):
    print(json.dumps(cur))
else:
    print(cur)
PY
}

count_list_key() {
  # count length of top-level list field if present
  local key="$1"
  python3 - "$key" "$(body_file)" <<'PY'
import json, sys
key, fp = sys.argv[1], sys.argv[2]
with open(fp, encoding="utf-8") as f:
    data = json.load(f)
val = data.get(key)
if isinstance(val, list):
    print(len(val))
elif isinstance(data, list):
    print(len(data))
else:
    print(0)
PY
}

assert_status() {
  local label="$1"
  local got="$2"
  local want="$3"
  if [[ "$got" == "$want" ]]; then
    ok "$label (HTTP $got)"
  else
    bad "$label (HTTP $got, expected $want)"
  fi
}

assert_status_one_of() {
  local label="$1"
  local got="$2"
  shift 2
  local want
  for want in "$@"; do
    if [[ "$got" == "$want" ]]; then
      ok "$label (HTTP $got)"
      return 0
    fi
  done
  bad "$label (HTTP $got, expected one of: $*)"
}

log "=== Ledger public demo verification ==="
log "API_BASE_URL=$API_BASE_URL"
if [[ -n "$WEB_BASE_URL" ]]; then
  log "WEB_BASE_URL=$WEB_BASE_URL"
else
  log "WEB_BASE_URL=(unset)"
fi
log

# --- API health / readiness ---
code=$(http_code GET "$API_BASE_URL/health")
assert_status "GET /health" "$code" "200"
if [[ "$code" == "200" ]]; then
  status=$(json_field status 2>/dev/null || true)
  if [[ "$status" == "ok" || "$status" == "healthy" || -n "$status" ]]; then
    ok "GET /health body status=$status"
  else
    bad "GET /health body missing usable status"
  fi
fi

code=$(http_code GET "$API_BASE_URL/ready")
assert_status "GET /ready" "$code" "200"

# --- Seeded read surfaces ---
code=$(http_code GET "$API_BASE_URL/incidents?limit=5")
assert_status "GET /incidents" "$code" "200"
incident_count=0
if [[ "$code" == "200" ]]; then
  incident_count=$(count_list_key incidents)
  if [[ "$incident_count" -ge 1 ]]; then
    ok "seeded incidents visible (count>=1, saw $incident_count)"
  else
    bad "expected seeded incidents, saw $incident_count"
  fi
fi

code=$(http_code GET "$API_BASE_URL/metrics/dashboard")
# dashboard path may be /dashboard or /metrics/* depending on router
if [[ "$code" != "200" ]]; then
  code=$(http_code GET "$API_BASE_URL/dashboard")
fi
assert_status_one_of "GET dashboard metrics" "$code" "200"

code=$(http_code GET "$API_BASE_URL/runs?limit=5")
assert_status "GET /runs" "$code" "200"
if [[ "$code" == "200" ]]; then
  run_count=$(python3 -c "import json,sys; d=json.load(open('$(body_file)')); print(len(d) if isinstance(d, list) else len(d.get('runs', [])))")
  if [[ "$run_count" -ge 1 ]]; then
    ok "seeded runs visible (count>=1, saw $run_count)"
  else
    bad "expected seeded runs, saw $run_count"
  fi
fi

code=$(http_code GET "$API_BASE_URL/approvals?limit=5")
assert_status_one_of "GET /approvals" "$code" "200"
if [[ "$code" == "200" ]]; then
  approval_count=$(python3 -c "import json,sys; d=json.load(open('$(body_file)')); print(len(d) if isinstance(d, list) else len(d.get('approvals', [])))")
  if [[ "$approval_count" -ge 1 ]]; then
    ok "seeded approvals visible (count>=1, saw $approval_count)"
  else
    bad "expected seeded approvals, saw $approval_count"
  fi
fi

# Eval regression: the good version passes every case and the degraded
# candidate regresses, so the studio comparison is never empty.
code=$(http_code GET "$API_BASE_URL/eval-results?dataset_id=mrr-drop-suite&agent_version_id=ledger_phase6")
good_passed=0
if [[ "$code" == "200" ]]; then
  good_passed=$(python3 -c "import json,sys; d=json.load(open('$(body_file)')); print(sum(1 for r in d.get('results',[]) if r.get('passed')))")
fi
code=$(http_code GET "$API_BASE_URL/eval-results?dataset_id=mrr-drop-suite&agent_version_id=ledger_phase6_degraded")
degraded_passed=0
if [[ "$code" == "200" ]]; then
  degraded_passed=$(python3 -c "import json,sys; d=json.load(open('$(body_file)')); print(sum(1 for r in d.get('results',[]) if r.get('passed')))")
fi
if [[ "$good_passed" -gt "$degraded_passed" && "$good_passed" -ge 1 ]]; then
  ok "eval regression visible (good $good_passed passed, degraded $degraded_passed passed)"
else
  bad "expected eval regression (good $good_passed passed vs degraded $degraded_passed passed)"
fi

code=$(http_code GET "$API_BASE_URL/agents")
assert_status "GET /agents" "$code" "200"

code=$(http_code GET "$API_BASE_URL/tools")
assert_status "GET /tools" "$code" "200"

# Capture a baseline incident count for mutation side-effect check
code=$(http_code GET "$API_BASE_URL/incidents?limit=100")
baseline_incidents=0
if [[ "$code" == "200" ]]; then
  baseline_incidents=$(count_list_key incidents)
fi

# --- Anonymous mutations must fail closed (403) ---
# Bodies are deliberately valid-shaped so the request reaches the token gate.
mutation_probes=(
  "POST|/incidents|{\"anomaly_id\":\"rev_anomaly_week_20260603\"}"
  "POST|/agent/investigations|{\"incident_id\":\"inc_rev_mrr_wow_drop_20260603\"}"
  "POST|/runs|{\"agent_version_id\":\"ledger_phase6\"}"
  "POST|/runs/run_does_not_exist/transitions|{\"status\":\"failed\"}"
  "POST|/approvals/apr_does_not_exist/approve|{}"
  "POST|/approvals/apr_does_not_exist/reject|{}"
  "POST|/mock-actions|{\"run_id\":\"run_demo\",\"action_type\":\"draft_slack_message\",\"title\":\"Draft\",\"description\":\"x\",\"target\":\"#ops\",\"payload\":{\"message\":\"hello\"}}"
  "POST|/agents|{\"id\":\"anon-probe\",\"name\":\"Anon\"}"
  "POST|/agents/ledger/versions|{}"
  "POST|/agents/ledger/versions/ledger_phase6/publish|{}"
  "POST|/tools|{\"id\":\"anon_tool\",\"name\":\"Anon\",\"description\":\"x\",\"input_schema\":{},\"output_schema\":{},\"permission_scope\":\"read_data\",\"implementation_ref\":\"app.demo.run\"}"
  "POST|/eval-datasets|{\"name\":\"anon\",\"case_ids\":[\"case_1\"]}"
  "POST|/evals/run|"
  "POST|/documents/ingest|{\"path\":\"billing-retry-regression-runbook.md\"}"
)

for probe in "${mutation_probes[@]}"; do
  IFS='|' read -r method path payload <<<"$probe"
  if [[ -n "${payload}" ]]; then
    code=$(http_code "$method" "$API_BASE_URL$path" \
      -H 'Content-Type: application/json' \
      --data "$payload")
  else
    code=$(http_code "$method" "$API_BASE_URL$path" \
      -H 'Content-Type: application/json' \
      --data '{}')
  fi
  if [[ "$code" == "403" ]]; then
    ok "anonymous $method $path rejected (403)"
  else
    bad "anonymous $method $path expected 403, got $code"
  fi
done

code=$(http_code GET "$API_BASE_URL/incidents?limit=100")
if [[ "$code" == "200" ]]; then
  after_incidents=$(count_list_key incidents)
  if [[ "$after_incidents" == "$baseline_incidents" ]]; then
    ok "incident count unchanged after anonymous mutation probes ($after_incidents)"
  else
    bad "incident count changed after anonymous probes ($baseline_incidents -> $after_incidents)"
  fi
else
  bad "could not re-read incidents after mutation probes (HTTP $code)"
fi

# --- Optional CORS probe ---
if [[ "$STRICT_CORS" == "true" ]]; then
  acao=$(curl -sS -o /dev/null -D - --max-time 15 \
    -X OPTIONS "$API_BASE_URL/health" \
    -H "Origin: $EXPECTED_ORIGIN" \
    -H "Access-Control-Request-Method: GET" \
    | tr -d '\r' | awk -F': ' 'tolower($1)=="access-control-allow-origin"{print $2; exit}' || true)
  if [[ "$acao" == "$EXPECTED_ORIGIN" || "$acao" == "*" ]]; then
    # "*" would be a misconfig for a credentialed demo; fail for the public demo
    if [[ "$acao" == "*" ]]; then
      bad "CORS ACAO is wildcard; set BACKEND_CORS_ORIGINS to exact origin $EXPECTED_ORIGIN"
    else
      ok "CORS allows $EXPECTED_ORIGIN"
    fi
  else
    bad "CORS missing/incorrect ACAO for $EXPECTED_ORIGIN (got '${acao:-empty}')"
  fi
else
  skip "CORS probe (set STRICT_CORS=true EXPECTED_ORIGIN=... to enable)"
fi

# --- Optional web surface ---
if [[ -z "$WEB_BASE_URL" ]]; then
  if [[ "$REQUIRE_WEB" == "true" ]]; then
    bad "WEB_BASE_URL required but unset"
  else
    skip "web checks (set WEB_BASE_URL to enable)"
  fi
else
  web_headers=$(curl -sS -D - -o /tmp/ledger-public-demo-web.$$ --max-time 30 "$WEB_BASE_URL/" || true)
  web_code=$(printf '%s' "$web_headers" | awk 'NR==1{print $2}')
  if [[ "$web_code" == "200" ]]; then
    ok "GET $WEB_BASE_URL/ (HTTP 200)"
  else
    bad "GET $WEB_BASE_URL/ expected 200, got ${web_code:-empty}"
  fi

  header_lower=$(printf '%s' "$web_headers" | tr '[:upper:]' '[:lower:]' | tr -d '\r')
  for needle in \
    "x-content-type-options: nosniff" \
    "x-frame-options: deny" \
    "referrer-policy:" \
    "content-security-policy:" \
    "permissions-policy:" \
    "strict-transport-security:"; do
    if printf '%s' "$header_lower" | grep -qF "$needle"; then
      ok "security header present: $needle"
    else
      # HSTS only expected on HTTPS deployments
      if [[ "$needle" == "strict-transport-security:" && "$WEB_BASE_URL" != https://* ]]; then
        skip "HSTS not required for non-HTTPS WEB_BASE_URL"
      else
        bad "missing security header: $needle"
      fi
    fi
  done

  if grep -qiE 'public read-only demo' /tmp/ledger-public-demo-web.$$; then
    ok "read-only banner text present in HTML"
  else
    bad "read-only banner text missing from HTML (OPERATOR_UI_ENABLED must be false)"
  fi

  # Secret leak scan: never allow known secret env names or sk- style keys in HTML
  if grep -qiE 'DEMO_OPERATOR_TOKEN|EVAL_RUN_TOKEN|DOCUMENT_INGEST_TOKEN|OPENAI_API_KEY|ANTHROPIC_API_KEY|STRIPE_(SECRET|API)_KEY|sk-[a-zA-Z0-9]{10,}|sk_live_|sk_test_[a-zA-Z0-9]{10,}' \
    /tmp/ledger-public-demo-web.$$; then
    bad "possible secret material found in HTML response"
  else
    ok "no obvious secret material in HTML"
  fi
fi

log
log "=== summary: pass=$PASS fail=$FAIL skip=$SKIP ==="
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
exit 0
