#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${ROOT_DIR}/.env-py2.sh"

if [[ ! -d "${ROOT_DIR}/ccs-caldavtester" || ! -d "${ROOT_DIR}/ccs-pycalendar" ]]; then
  echo "Expected ccs-caldavtester and ccs-pycalendar under ${ROOT_DIR}" >&2
  exit 1
fi

cat >"${ENV_FILE}" <<EOF
#!/usr/bin/env bash
CALDAVTESTER_LAB_ROOT="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
export CALDAVTESTER_LAB_ROOT
export PYTHONPATH="\${CALDAVTESTER_LAB_ROOT}/ccs-pycalendar/src:\${CALDAVTESTER_LAB_ROOT}/ccs-caldavtester:\${PYTHONPATH:-}"
EOF

chmod +x "${ENV_FILE}"

source "${ENV_FILE}"

python2 - <<'PY'
import pycalendar
print("Imported pycalendar from:", pycalendar.__file__)
PY

echo
echo "Bootstrap complete."
echo "Load env with: source .env-py2.sh"
echo "Smoke test:    cd ccs-caldavtester && python2 -c 'import pycalendar, src.manager'"
