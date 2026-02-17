set shell := ["bash", "-euo", "pipefail", "-c"]

setup-integration-fixtures:
	uv run python manage.py migrate --settings=config.settings_test --noinput
	uv run python manage.py shell --settings=config.settings_test -c "from django.contrib.auth.models import User; from calendars.models import Calendar; users=['admin','apprentice','superuser']+[f'user{i:02d}' for i in range(1,41)]; [((lambda user, username: (user.set_password(username), user.save(update_fields=['password','is_active','email']), Calendar.objects.get_or_create(owner=user, slug='calendar', defaults={'name':'calendar','timezone':'UTC'}), Calendar.objects.get_or_create(owner=user, slug='tasks', defaults={'name':'tasks','timezone':'UTC'})))(User.objects.update_or_create(username=username, defaults={'email': f'{username}@example.com', 'is_active': True})[0], username)) for username in users]"
	uv run python manage.py shell --settings=config.settings_test -c "from calendars.models import Calendar; users=['admin','apprentice','superuser']+[f'user{i:02d}' for i in range(1,41)]; [Calendar.objects.filter(owner__username=username, slug__in=['calendar-none','calendar-us','litmus']).delete() for username in users]; [cal.calendar_objects.all().delete() for cal in Calendar.objects.filter(owner__username__in=users, slug__in=['calendar','tasks'])]"

litmus-test:
	uv run python manage.py shell -c "from django.contrib.auth.models import User; from calendars.models import Calendar; user,_=User.objects.get_or_create(username='user01', defaults={'is_active':True,'email':'user01@example.com'}); user.set_password('user01'); user.save(update_fields=['password','is_active','email']); Calendar.objects.filter(owner=user, slug='litmus').delete()"
	nix develop path:.#litmus -c litmus "http://127.0.0.1:8000/dav/calendars/user01/" "user01" "user01"

caldavtester-test-suite:
	# Default implementation-loop suite: supported CalDAV subset only.
	nix develop path:.#caldavtester -c bash -lc 'cd caldavtester-lab && ./bootstrap.sh >/dev/null && source ./.env-py2.sh && cd ccs-caldavtester && MODULES="`rg -v "^\\s*(#|$$)" ../caldav-suite-modules.txt | tr "\n" " "`" && python2 testcaldav.py $MODULES'

integration-test:
	just litmus-test
	just caldavtester-test-suite

django-test *args:
	uv run python manage.py test --settings=config.settings_test --parallel {{args}}

django-test-server *args:
	just setup-integration-fixtures
	uv run python manage.py runserver --settings=config.settings_test {{args}}
