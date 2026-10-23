#!/bin/sh
set -e

python manage.py migrate --noinput
# creates the index on the first start, updates config.json if the download URL changed
python manage.py init_index

# several workers, as in production: index writes are serialized by a lock
exec gunicorn magnet.wsgi:application --bind 0.0.0.0:8000 --workers 3 --access-logfile -
