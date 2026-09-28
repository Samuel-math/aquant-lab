#!/bin/sh
set -eu
cd "${AQUANT_HOME:-/opt/aquant}"
# Provider must publish a complete, validated dataset before this job starts.
# Dates come from Asia/Shanghai; never silently fall back to older bars.
TODAY=$(TZ=Asia/Shanghai date +%F)
export PYTHONPATH="$PWD"
# Exit 3 means a known non-session; malformed/missing/stale calendars fail the job.
set +e
"${PYTHON:-python3}" -c 'import csv,sys; from datetime import date,timedelta; dates=[r["date"] for r in csv.DictReader(open(sys.argv[1]))]; today=sys.argv[2]; assert dates and dates[0] <= today and dates[-1] >= (date.fromisoformat(today)+timedelta(days=7)).isoformat(), "Calendar must cover today and the coming week"; sys.exit(0 if today in dates else 3)' "${AQUANT_DATA:-data/live}/calendar.csv" "$TODAY"
CALENDAR_STATUS=$?
set -e
if [ "$CALENDAR_STATUS" -eq 3 ]; then
  echo "Known non-trading day; skipped."
  exit 0
fi
if [ "$CALENDAR_STATUS" -ne 0 ]; then
  echo "Calendar validation failed." >&2
  exit "$CALENDAR_STATUS"
fi
exec "${PYTHON:-python3}" -m aquant daily \
  --config "${AQUANT_CONFIG:-configs/live.json}" \
  --data "${AQUANT_DATA:-data/live}" \
  --ledger "${AQUANT_LEDGER:-data/live-account.sqlite}" \
  --date "$TODAY" --out artifacts/reports --send
