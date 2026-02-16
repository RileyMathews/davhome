# caldavtester-lab

Isolated workspace for running the legacy `apple/ccs-caldavtester` suite in a Nix shell.

## Why this setup

- `ccs-caldavtester` is Python 2-only.
- Nix keeps old runtime tooling isolated from your system and future Python 3 app work.

## Layout

- `../flake.nix`: Nix dev shell definition (`caldavtester` shell).
- `bootstrap.sh`: Creates `.env-py2.sh` with the right `PYTHONPATH`.
- `ccs-caldavtester/`: test framework.
- `ccs-pycalendar/`: required dependency.

## Enter shell

```bash
cd ..
nix develop path:.#caldavtester
cd caldavtester-lab
```

## Bootstrap Python env

```bash
./bootstrap.sh
source .env-py2.sh
```

## Smoke test

```bash
cd ccs-caldavtester
python2 -c 'import pycalendar, src.manager'
```

## Run tests

From `ccs-caldavtester/`, edit `serverinfo.xml` for your target server first, then run:

```bash
cd ccs-caldavtester
python2 testcaldav.py --all
```

Or run a single file for quick iteration:

```bash
python2 testcaldav.py scripts/tests/CalDAV/current-user-principal.xml
```
