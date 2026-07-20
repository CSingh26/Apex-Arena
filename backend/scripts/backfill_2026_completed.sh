#!/usr/bin/env bash
set -u

QUALI_ENDPOINTS="drivers,laps,position,race_control,weather,session_result,starting_grid"
RACE_ENDPOINTS="drivers,laps,position,intervals,pit,stints,race_control,weather,session_result,starting_grid"

failures=0

run_backfill() {
  local slug="$1"
  local endpoints="$2"

  echo ""
  echo "============================================================"
  echo "Backfilling: ${slug}"
  echo "============================================================"

  if ! python -m app.cli.backfill_openf1 \
    --season 2026 \
    --room-slug "${slug}" \
    --endpoints "${endpoints}" \
    --json-summary; then
    echo "FAILED: ${slug}"
    failures=$((failures + 1))
  fi
}

# Round 1 — Australia
run_backfill "2026-australian-grand-prix-race" "$RACE_ENDPOINTS"

# Round 2 — China
run_backfill "2026-chinese-grand-prix-sprint-qualifying" "$QUALI_ENDPOINTS"
run_backfill "2026-chinese-grand-prix-sprint" "$RACE_ENDPOINTS"
run_backfill "2026-chinese-grand-prix-qualifying" "$QUALI_ENDPOINTS"
run_backfill "2026-chinese-grand-prix-race" "$RACE_ENDPOINTS"

# Round 3 — Japan
run_backfill "2026-japanese-grand-prix-qualifying" "$QUALI_ENDPOINTS"
run_backfill "2026-japanese-grand-prix-race" "$RACE_ENDPOINTS"

# Round 4 — Miami
run_backfill "2026-miami-grand-prix-sprint-qualifying" "$QUALI_ENDPOINTS"
run_backfill "2026-miami-grand-prix-sprint" "$RACE_ENDPOINTS"
run_backfill "2026-miami-grand-prix-qualifying" "$QUALI_ENDPOINTS"
run_backfill "2026-miami-grand-prix-race" "$RACE_ENDPOINTS"

# Round 5 — Canada
run_backfill "2026-canadian-grand-prix-sprint-qualifying" "$QUALI_ENDPOINTS"
run_backfill "2026-canadian-grand-prix-sprint" "$RACE_ENDPOINTS"
run_backfill "2026-canadian-grand-prix-qualifying" "$QUALI_ENDPOINTS"
run_backfill "2026-canadian-grand-prix-race" "$RACE_ENDPOINTS"

# Round 6 — Monaco
run_backfill "2026-monaco-grand-prix-qualifying" "$QUALI_ENDPOINTS"
run_backfill "2026-monaco-grand-prix-race" "$RACE_ENDPOINTS"

# Round 7 — Barcelona
run_backfill "2026-barcelona-grand-prix-qualifying" "$QUALI_ENDPOINTS"
run_backfill "2026-barcelona-grand-prix-race" "$RACE_ENDPOINTS"

# Round 8 — Austria
run_backfill "2026-austrian-grand-prix-qualifying" "$QUALI_ENDPOINTS"
run_backfill "2026-austrian-grand-prix-race" "$RACE_ENDPOINTS"

# Round 9 — Britain
run_backfill "2026-british-grand-prix-sprint-qualifying" "$QUALI_ENDPOINTS"
run_backfill "2026-british-grand-prix-sprint" "$RACE_ENDPOINTS"
run_backfill "2026-british-grand-prix-qualifying" "$QUALI_ENDPOINTS"
run_backfill "2026-british-grand-prix-race" "$RACE_ENDPOINTS"

# Spa qualifying and race already exist, so they are intentionally omitted.

echo ""
echo "Backfill finished with ${failures} failed room(s)."

if [ "$failures" -gt 0 ]; then
  exit 1
fi