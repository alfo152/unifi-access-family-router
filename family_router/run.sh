#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
# Start script of the Home Assistant add-on.
set -e

OPTIONS=/data/options.json

# If no broker is configured, use the Home Assistant MQTT service
# (Mosquitto add-on) provided by the Supervisor.
mqtt_host="$(jq -r '.mqtt.host // ""' "${OPTIONS}")"
if [ -z "${mqtt_host}" ]; then
    # The availability check is silenced: without a broker the Supervisor
    # answers "Service not enabled", which bashio would print as an ERROR.
    if bashio::services.available "mqtt" >/dev/null 2>&1; then
        MQTT_HOST="$(bashio::services mqtt 'host')"
        MQTT_PORT="$(bashio::services mqtt 'port')"
        MQTT_USERNAME="$(bashio::services mqtt 'username')"
        MQTT_PASSWORD="$(bashio::services mqtt 'password')"
        export MQTT_HOST MQTT_PORT MQTT_USERNAME MQTT_PASSWORD
        bashio::log.info "Using the Home Assistant MQTT broker ${MQTT_HOST}:${MQTT_PORT}"
    else
        bashio::log.warning "No MQTT broker found: install the Mosquitto broker add-on, or fill in mqtt.host"
    fi
fi

exec python3 -m family_router --options "${OPTIONS}"
