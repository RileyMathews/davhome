#!/usr/bin/env bash
set -euo pipefail

base_url="${DAVHOME_TEST_BASE_URL:-http://127.0.0.1:8000}"

signup() {
  local username="$1"
  local password="$2"

  curl -fsS \
    -X POST \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    --data-urlencode "username=$username" \
    --data-urlencode "password=$password" \
    --data-urlencode "confirm_password=$password" \
    "$base_url/signup" >/dev/null
}

for n in $(seq -w 1 40); do
  signup "user$n" "davhome-test-user-$n"
done

signup admin davhome-test-admin-password
signup apprentice davhome-test-apprentice-password
signup superuser davhome-test-superuser-password

echo "Seeded DAV test users at $base_url"
