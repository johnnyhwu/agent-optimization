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
: "${PKCE_METHOD:=S256}"
export AUTH_MODE API_BASE KEYCLOAK_URL KEYCLOAK_REALM KEYCLOAK_CLIENT_ID PKCE_METHOD

envsubst '${AUTH_MODE} ${API_BASE} ${KEYCLOAK_URL} ${KEYCLOAK_REALM} ${KEYCLOAK_CLIENT_ID} ${PKCE_METHOD}' \
    < /etc/nginx/config.js.template > /var/www/config.js

# PKCE_METHOD is echoed because "off" is a security-relevant choice, and a
# container log is where someone looks to find out whether it is in force.
echo "app config: AUTH_MODE=${AUTH_MODE} API_BASE=${API_BASE} KEYCLOAK_REALM=${KEYCLOAK_REALM} PKCE_METHOD=${PKCE_METHOD}"
