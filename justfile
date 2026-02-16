set shell := ["bash", "-euo", "pipefail", "-c"]

litmus-test:
	nix develop path:.#litmus -c litmus "http://127.0.0.1:8000/dav/" "user01" "pass01"

caldavtester-test-suite:
	nix develop path:.#caldavtester -c bash -lc 'cd caldavtester-lab && ./bootstrap.sh >/dev/null && source ./.env-py2.sh && cd ccs-caldavtester && python2 testcaldav.py --all'

integration-test:
	just litmus-test
	just caldavtester-test-suite
