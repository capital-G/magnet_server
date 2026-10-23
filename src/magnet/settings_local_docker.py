import os

from .settings import *  # noqa

# Settings of the local docker stack (docker-compose.yml). The index and artifact
# paths and the download URL come from the environment (see settings.py).

# defaults are the values of .local.env, so the settings also import without it,
# e.g. for `collectstatic` while building the image
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": os.environ.get("POSTGRES_HOST", "database"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "NAME": os.environ.get("POSTGRES_DB", "magnet"),
        "USER": os.environ.get("POSTGRES_USER", "magnet"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "magnetpw"),
    }
}

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
]

DEBUG = True

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

STORAGES = {
    **STORAGES,
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
