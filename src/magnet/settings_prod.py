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
