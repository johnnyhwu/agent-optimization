#!/bin/sh
# Render /var/www/config.js from the environment, before nginx starts.
#
# The nginx image runs everything in /docker-entrypoint.d/ at boot; its own
# 20-envsubst-on-templates.sh handles /etc/nginx/templates, which is why the
# server config is there and this one file is not — it lands in the web root, not
# in the nginx config directory.
#
# Only the listed variables are substituted. Left unrestricted, envsubst would
# also rewrite anything else that looks like a shell variable in the template.
set -eu

: "${AUTH_MODE:=fake}"
: "${API_BASE:=/api}"
: "${KEYCLOAK_URL:=}"
: "${KEYCLOAK_REALM:=tsmc}"
: "${KEYCLOAK_CLIENT_ID:=ai4bi-public}"
export AUTH_MODE API_BASE KEYCLOAK_URL KEYCLOAK_REALM KEYCLOAK_CLIENT_ID

envsubst '${AUTH_MODE} ${API_BASE} ${KEYCLOAK_URL} ${KEYCLOAK_REALM} ${KEYCLOAK_CLIENT_ID}' \
    < /etc/nginx/config.js.template > /var/www/config.js

echo "app config: AUTH_MODE=${AUTH_MODE} API_BASE=${API_BASE} KEYCLOAK_REALM=${KEYCLOAK_REALM}"
