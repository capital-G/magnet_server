import os

from .settings import *  # noqa

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": os.environ["POSTGRES_HOST"],
        "PORT": os.environ["POSTGRES_PORT"],
        "NAME": os.environ["POSTGRES_DB"],
        "USER": os.environ["POSTGRES_USER"],
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
    }
}

GIT_INDEX_REPO_PATH = Path(os.environ["GIT_INDEX_REPO_PATH"])

ALLOWED_HOSTS: List[str] = [
    "127.0.0.1",
    "0.0.0.0",
]

DEBUG = True
