set shell := ["bash", "-euo", "pipefail", "-c"]

setup-integration-fixtures:
	uv run python manage.py migrate --settings=config.settings_test --noinput
	uv run python manage.py shell --settings=config.settings_test -c "from django.contrib.auth.models import User; from calendars.models import Calendar; users=['admin','apprentice','superuser']+[f'user{i:02d}' for i in range(1,41)]; [((lambda user, username: (user.set_password(username), user.save(update_fields=['password','is_active','email']), Calendar.objects.update_or_create(owner=user, slug='calendar', defaults={'name':'calendar','timezone':'UTC','component_kind':'VEVENT'}), Calendar.objects.update_or_create(owner=user, slug='tasks', defaults={'name':'tasks','timezone':'UTC','component_kind':'VTODO'}), Calendar.objects.update_or_create(owner=user, slug='inbox', defaults={'name':'inbox','timezone':'UTC','component_kind':'VEVENT'}), Calendar.objects.update_or_create(owner=user, slug='outbox', defaults={'name':'outbox','timezone':'UTC','component_kind':'VEVENT'})))(User.objects.update_or_create(username=username, defaults={'email': f'{username}@example.com', 'is_active': True})[0], username)) for username in users]"
	uv run python manage.py shell --settings=config.settings_test -c "from calendars.models import Calendar; users=['admin','apprentice','superuser']+[f'user{i:02d}' for i in range(1,41)]; [Calendar.objects.filter(owner__username=username, slug__in=['calendar-none','calendar-us','litmus','synccalendar1','synccalendar2']).delete() for username in users]; [cal.calendar_objects.all().delete() for cal in Calendar.objects.filter(owner__username__in=users, slug__in=['calendar','tasks','inbox','outbox'])]"

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

verify: setup-integration-fixtures django-test litmus-test caldavtester-test-suite

full-verify:
	@image="davhome-verify:local"; container="davhome-verify-$(date +%s)"; ready=0; \
	just django-test; \
	cleanup() { status=$?; if [ "$status" -ne 0 ]; then docker logs "$container" || true; fi; docker rm -f "$container" >/dev/null 2>&1 || true; exit "$status"; }; \
	trap cleanup EXIT; \
	docker build -t "$image" .; \
	docker run -d --name "$container" -p 8000:8000 "$image" sh -c 'python manage.py migrate --settings=config.settings_test --noinput && python manage.py shell --settings=config.settings_test -c "from django.contrib.auth.models import User; from calendars.models import Calendar; users=[\"admin\",\"apprentice\",\"superuser\"]+[f\"user{i:02d}\" for i in range(1,41)]; [((lambda user, username: (user.set_password(username), user.save(update_fields=[\"password\",\"is_active\",\"email\"]), Calendar.objects.update_or_create(owner=user, slug=\"calendar\", defaults={\"name\":\"calendar\",\"timezone\":\"UTC\",\"component_kind\":\"VEVENT\"}), Calendar.objects.update_or_create(owner=user, slug=\"tasks\", defaults={\"name\":\"tasks\",\"timezone\":\"UTC\",\"component_kind\":\"VTODO\"}), Calendar.objects.update_or_create(owner=user, slug=\"inbox\", defaults={\"name\":\"inbox\",\"timezone\":\"UTC\",\"component_kind\":\"VEVENT\"}), Calendar.objects.update_or_create(owner=user, slug=\"outbox\", defaults={\"name\":\"outbox\",\"timezone\":\"UTC\",\"component_kind\":\"VEVENT\"})))(User.objects.update_or_create(username=username, defaults={\"email\": f\"{username}@example.com\", \"is_active\": True})[0], username)) for username in users]" && python manage.py shell --settings=config.settings_test -c "from calendars.models import Calendar; users=[\"admin\",\"apprentice\",\"superuser\"]+[f\"user{i:02d}\" for i in range(1,41)]; [Calendar.objects.filter(owner__username=username, slug__in=[\"calendar-none\",\"calendar-us\",\"litmus\",\"synccalendar1\",\"synccalendar2\"]).delete() for username in users]; [cal.calendar_objects.all().delete() for cal in Calendar.objects.filter(owner__username__in=users, slug__in=[\"calendar\",\"tasks\",\"inbox\",\"outbox\"])]" && DJANGO_SETTINGS_MODULE=config.settings_test exec gunicorn config.wsgi:application --bind 0.0.0.0:8000'; \
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
