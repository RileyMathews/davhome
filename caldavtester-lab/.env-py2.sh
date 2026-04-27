#!/usr/bin/env bash
CALDAVTESTER_LAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export CALDAVTESTER_LAB_ROOT
export PYTHONPATH="${CALDAVTESTER_LAB_ROOT}/ccs-pycalendar/src:${CALDAVTESTER_LAB_ROOT}/ccs-caldavtester:${PYTHONPATH:-}"
