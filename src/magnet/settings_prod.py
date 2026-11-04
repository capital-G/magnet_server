import os

from .settings import *  # noqa

DEBUG = False

DATABASES = {"default": {}}

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

STORAGES = {
    **STORAGES,
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# tasks go to the database, `manage.py db_worker` runs them
TASKS = {"default": {**TASKS["default"], "BACKEND": "django_tasks_db.DatabaseBackend"}}
