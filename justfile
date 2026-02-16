set shell := ["bash", "-euo", "pipefail", "-c"]

caldavtester-test-suite:
	nix develop path:.#caldavtester -c bash -lc 'cd caldavtester-lab && ./bootstrap.sh >/dev/null && source ./.env-py2.sh && cd ccs-caldavtester && python2 testcaldav.py --all'
