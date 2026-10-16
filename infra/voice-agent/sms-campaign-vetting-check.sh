#!/usr/bin/env bash
# Daily check: is the call-line 10DLC campaign through vetting?
# When it clears, attach the call-line number to the campaign and tell Joel.
# The campaign is a Joel-only alert line (opt-in page: https://shitrat.ai/sms).
# No texts are sent from here: sending waits for approval, then Joel only.
# Idempotent — exits quietly if the number is already attached or vetting
# is still pending. Safe to run forever; remove the LaunchAgent
# (com.joelclaw.sms-vetting-check) after success.
set -euo pipefail

CAMPAIGN_ID="4b30019f-5c00-f617-8e3c-bda78fb2fda1"
# Superseded first attempt (missing subUsecases, undeletable while TCR_PENDING);
# we retry deleting it each run until Telnyx lets go of it.
OLD_CAMPAIGN_ID="4b30019f-5bf8-c0c8-c836-616b50a35694"
# The call line: the number ShitRat calls Joel from.
LINE_DID="+13603894321"

lease() {
  local value
  value="$(secrets lease "$1" --ttl 5m)" || { echo "FATAL: lease failed for $1" >&2; exit 1; }
  case "$value" in '{'*|'') echo "FATAL: bad lease for $1" >&2; exit 1;; esac
  printf '%s' "$value"
}

VOICE_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
# Each event tells Joel once in his ShitRat chat (plain words, keyed by event)
# and gives the Switchboard the detail over Rat King. (joelclaw notify is
# retired; it dropped every alert silently.)
#   notify <event-key> <note for Joel> <summary for the Switchboard> [detail]
notify() {
  "$VOICE_DIR/.venv/bin/python" "$VOICE_DIR/joel_note.py" --key "sms-vetting-$1" --kind update "$2" || true
  "$VOICE_DIR/.venv/bin/python" "$VOICE_DIR/fleet_alert.py" --latch sms-vetting --summary "$3" --body "${4:-$3}" || true
}

API_KEY="$(lease telnyx_api_key)"

# Best-effort cleanup of the superseded campaign; quiet on any outcome.
curl -sS -X DELETE -H "Authorization: Bearer $API_KEY" \
  "https://api.telnyx.com/v2/10dlc/campaign/$OLD_CAMPAIGN_ID" >/dev/null 2>&1 || true

# A rejected campaign never clears by waiting: say so once, with the reasons.
campaign="$(curl -fsS -H "Authorization: Bearer $API_KEY" "https://api.telnyx.com/v2/10dlc/campaign/$CAMPAIGN_ID" || true)"
state="$(printf '%s' "$campaign" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("campaignStatus",""))' 2>/dev/null || true)"
case "$state" in
  TELNYX_FAILED|MNO_REJECTED|EXPIRED|TCR_EXPIRED|TCR_FAILED|TCR_SUSPENDED|MNO_PROVISIONING_FAILED)
    reasons="$(printf '%s' "$campaign" | python3 -c 'import json,sys; print(" | ".join(r.get("description","") for r in json.load(sys.stdin).get("failureReasons") or [])[:1200])' 2>/dev/null || true)"
    key="$state-$(printf '%s' "$reasons" | shasum | cut -c1-12)"
    notify "$key" \
      "Texting setup for the call line was turned down by the phone carriers' review. I've passed the reasons to the Switchboard; nothing for you to do yet." \
      "ShitRat SMS: call-line 10DLC campaign is $state, not vetting. It won't clear until it's fixed and resubmitted." \
      "Campaign $CAMPAIGN_ID is $state. Reasons: ${reasons:-none given}"
    exit 0
    ;;
esac

# Attach only once carriers have accepted it; anything else is still in review.
case "$state" in
  MNO_ACCEPTED|MNO_PROVISIONED) ;;
  *) echo "campaign $state: still in review"; exit 0 ;;
esac

# Already attached? Then we're done — quiet exit.
assigned="$(curl -fsS -H "Authorization: Bearer $API_KEY" \
  "https://api.telnyx.com/v2/10dlc/phone_number_campaigns?filter%5BtelephoneNumber%5D=$(printf '%s' "$LINE_DID" | sed 's/+/%2B/')" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); recs=d.get("records") or d.get("data") or []; print(len(recs))' 2>/dev/null || echo 0)"
if [ "$assigned" != "0" ]; then
  exit 0
fi

# Try the attach. 10036 = still vetting, 10007 = Telnyx transient — both
# quiet, we just try again tomorrow. Success = notify Joel.
response="$(curl -sS -X POST \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d "{\"phoneNumber\":\"$LINE_DID\",\"campaignId\":\"$CAMPAIGN_ID\"}" \
  "https://api.telnyx.com/v2/10dlc/phone_number_campaigns")"

if printf '%s' "$response" | grep -qE '"10036"|"10007"'; then
  exit 0
fi

if printf '%s' "$response" | grep -q '"errors"'; then
  codes="$(printf '%s' "$response" | python3 -c 'import json,sys; print(",".join(sorted(str(e.get("code","")) for e in json.load(sys.stdin).get("errors") or [])))' 2>/dev/null || echo unknown)"
  notify "attach-error-${codes:-unknown}" \
    "Texting for the call line was approved, but hooking the number up failed. I've passed it to the Switchboard." \
    "ShitRat SMS: 10DLC attach for the call line failed with an unexpected error. Check campaign $CAMPAIGN_ID." \
    "$(printf '%s' "$response" | head -c 600)"
  echo "$response" >&2
  exit 1
fi

notify "approved" \
  "Texting for the call line is approved. Once sending is set up, text START to the call line to opt in. Until then nothing is sent." \
  "ShitRat SMS: call-line 10DLC campaign cleared vetting and the call-line number is attached. Next: messaging profile on the number, START/STOP/HELP handling, then Joel-only alerts. Remove LaunchAgent com.joelclaw.sms-vetting-check." \
  "Campaign $CAMPAIGN_ID approved; attach response: $(printf '%s' "$response" | head -c 300)"
echo "attached: $response"
