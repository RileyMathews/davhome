set shell := ["bash", "-euo", "pipefail", "-c"]

setup-integration-fixtures:
	uv run python manage.py migrate --noinput
	uv run python manage.py shell -c "from django.contrib.auth.models import User; from calendars.models import Calendar; users=['admin','apprentice','superuser']+[f'user{i:02d}' for i in range(1,41)]; [((lambda user, username: (user.set_password(username), user.save(update_fields=['password','is_active','email']), Calendar.objects.get_or_create(owner=user, slug='calendar', defaults={'name':'calendar','timezone':'UTC'})))(User.objects.update_or_create(username=username, defaults={'email': f'{username}@example.com', 'is_active': True})[0], username)) for username in users]"

litmus-test:
	nix develop path:.#litmus -c litmus "http://127.0.0.1:8000/dav/calendars/user01/" "user01" "user01"

caldavtester-test-suite:
	nix develop path:.#caldavtester -c bash -lc 'cd caldavtester-lab && ./bootstrap.sh >/dev/null && source ./.env-py2.sh && cd ccs-caldavtester && python2 testcaldav.py --all'

integration-test:
	just litmus-test
	just caldavtester-test-suite
