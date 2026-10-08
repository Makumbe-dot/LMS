#!/bin/sh
# Bring the schema up to date, then serve. Migrations are idempotent, so a
# restart with nothing new to apply costs a second. Set MIGRATE_ON_START=0 when
# migrations are run separately (several replicas, or a deployment account with
# more rights than the app's own login).
#
# Only the server migrates: `docker compose run backend python manage.py seed`
# and the nightly batch run their command and nothing else.
set -e

if [ "${MIGRATE_ON_START:-1}" = "1" ] && [ "$1" = "gunicorn" ]; then
    python manage.py migrate --noinput
fi

exec "$@"
