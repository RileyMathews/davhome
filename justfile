set shell := ["bash", "-euo", "pipefail", "-c"]

setup-integration-fixtures:
	uv run python manage.py migrate --settings=config.settings_test --noinput
	uv run python manage.py setup_integration_fixtures --settings=config.settings_test

litmus-test:
	uv run python manage.py shell --settings=config.settings_test -c "from django.contrib.auth.models import User; from calendars.models import Calendar; user,_=User.objects.get_or_create(username='user01', defaults={'is_active':True,'email':'user01@example.com'}); user.set_password('user01'); user.save(update_fields=['password','is_active','email']); Calendar.objects.filter(owner=user, slug='litmus').delete()"
	nix develop path:.#litmus -c litmus "http://127.0.0.1:8000/dav/calendars/user01/" "user01" "user01"

caldavtester-test-suite:
	# Default implementation-loop suite: supported CalDAV subset only.
	nix develop path:.#caldavtester -c bash -lc 'cd caldavtester-lab && ./bootstrap.sh >/dev/null && source ./.env-py2.sh && cd ccs-caldavtester && MODULES="`rg -v "^\\s*(#|$$)" ../caldav-suite-modules.txt | tr "\n" " "`" && python2 testcaldav.py $MODULES'

integration-test:
	just litmus-test
	just caldavtester-test-suite

django-test *args:
	uv run python manage.py test --settings=config.settings_test {{args}}

django-test-cov *args:
	uv run coverage erase
	uv run coverage run manage.py test --settings=config.settings_test {{args}}
	uv run coverage report

django-test-cov-html *args:
	uv run coverage erase
	uv run coverage run manage.py test --settings=config.settings_test {{args}}
	uv run coverage html

django-test-cov-xml *args:
	uv run coverage erase
	uv run coverage run manage.py test --settings=config.settings_test {{args}}
	uv run coverage xml

django-test-server *args:
	just setup-integration-fixtures
	uv run python manage.py runserver --settings=config.settings_test {{args}}

type-check:
	uv run mypy

verify: type-check setup-integration-fixtures django-test litmus-test caldavtester-test-suite

full-verify:
	@image="davhome-verify:local"; container="davhome-verify-$(date +%s)"; ready=0; \
	cleanup() { status=$?; if [ "$status" -ne 0 ]; then docker logs "$container" || true; fi; docker rm -f "$container" >/dev/null 2>&1 || true; exit "$status"; }; \
	trap cleanup EXIT; \
	uv run mypy; \
	docker build -t "$image" .; \
	docker run -d --name "$container" -p 8000:8000 "$image" sh -c 'python manage.py migrate --settings=config.settings_test --noinput && python manage.py setup_integration_fixtures --settings=config.settings_test && DJANGO_SETTINGS_MODULE=config.settings_test exec gunicorn config.wsgi:application --bind 0.0.0.0:8000'; \
	for _ in {1..120}; do \
		code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:8000/dav/" || true); \
		if [ "$code" != "000" ]; then ready=1; break; fi; \
		sleep 1; \
	done; \
	if [ "$ready" -ne 1 ]; then \
		docker logs "$container"; \
		exit 1; \
	fi; \
	nix develop path:.#litmus -c litmus "http://127.0.0.1:8000/dav/calendars/user01/" "user01" "user01"; \
	nix develop path:.#caldavtester -c bash -lc 'cd caldavtester-lab && ./bootstrap.sh >/dev/null && source ./.env-py2.sh && cd ccs-caldavtester && MODULES="`rg -v "^\\s*(#|$$)" ../caldav-suite-modules.txt | tr "\n" " "`" && python2 testcaldav.py $MODULES'; \
	just django-test
