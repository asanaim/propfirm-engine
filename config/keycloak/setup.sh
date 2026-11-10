#!/bin/bash
# Keycloak setup run at every start (compose job `keycloak-theme`); safe to
# run again. 1. The pipridge sign-in themes. 2. Sign in with Google (rule
# AC26), only when GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are set: the
# "Continue with Google" button appears on the trader sign-in page; a
# Google sign-in still asks for the authenticator code (an OTP step after
# the Google step), so a second factor is never skipped. Unset both to
# switch the button off again.
set -euo pipefail
k=/opt/keycloak/bin/kcadm.sh
c=(--config /tmp/kcadm.config)
R=propfirm

$k config credentials "${c[@]}" --server http://keycloak:8080 --realm master \
  --user admin --password "$ADMIN_PASSWORD"

# 1. themes
$k update "realms/$R" "${c[@]}" -s loginTheme=pipridge
bo=$($k get clients "${c[@]}" -r $R -q clientId=backoffice --fields id --format csv --noquotes)
$k update "clients/$bo" "${c[@]}" -r $R -s attributes.login_theme=pipridge-staff
echo "pipridge sign-in themes set"

# 2. Google
FLOW="otp-after-social-sign-in"
has_google=$($k get identity-provider/instances "${c[@]}" -r $R --fields alias --format csv --noquotes | grep -cx google || true)
if [[ -z "${GOOGLE_CLIENT_ID:-}" || -z "${GOOGLE_CLIENT_SECRET:-}" ]]; then
  if [[ "$has_google" != 0 ]]; then
    $k update identity-provider/instances/google "${c[@]}" -r $R -s enabled=false
    echo "Google sign-in switched off (no keys set)"
  else
    echo "Google sign-in not set up (no keys set)"
  fi
  exit 0
fi

# the OTP step after a Google sign-in, recorded as "otp" in the token's amr;
# each part is checked on its own, so a run cut short is completed next time
if ! $k get authentication/flows "${c[@]}" -r $R --fields alias --format csv --noquotes | grep -qxF "$FLOW"; then
  $k create authentication/flows "${c[@]}" -r $R -s "alias=$FLOW" -s providerId=basic-flow \
    -s topLevel=true -s builtIn=false -s "description=Authenticator code after Google (AC26)"
fi
steps() { $k get "authentication/flows/$FLOW/executions" "${c[@]}" -r $R; }
if ! steps | grep -q '"providerId" : "auth-otp-form"'; then
  $k create "authentication/flows/$FLOW/executions/execution" "${c[@]}" -r $R -s provider=auth-otp-form
fi
ex=$($k get "authentication/flows/$FLOW/executions" "${c[@]}" -r $R --fields id --format csv --noquotes | head -1)
if ! steps | grep -q '"requirement" : "REQUIRED"'; then
  $k update "authentication/flows/$FLOW/executions" "${c[@]}" -r $R -b "{\"id\":\"$ex\",\"requirement\":\"REQUIRED\"}"
fi
if ! steps | grep -q '"authenticationConfig"'; then
  $k create "authentication/executions/$ex/config" "${c[@]}" -r $R -s alias=amr-otp-social \
    -s 'config."default.reference.value"=otp' -s 'config."default.reference.maxAge"=36000'
fi

settings=(-s enabled=true -s displayName=Google -s trustEmail=true
  -s "firstBrokerLoginFlowAlias=first broker login" -s "postBrokerLoginFlowAlias=$FLOW"
  -s "config.clientId=$GOOGLE_CLIENT_ID" -s "config.clientSecret=$GOOGLE_CLIENT_SECRET"
  -s config.syncMode=IMPORT -s config.defaultScope="openid profile email")
if [[ "$has_google" == 0 ]]; then
  $k create identity-provider/instances "${c[@]}" -r $R -s alias=google -s providerId=google "${settings[@]}"
else
  $k update identity-provider/instances/google "${c[@]}" -r $R "${settings[@]}"
fi
echo "Google sign-in on"
