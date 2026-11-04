import os
from urllib.parse import urlsplit

from .settings import *  # noqa

# Production (implementation_plan.md M7): everything comes from the environment (see
# deploy/magnet.env.example), behind the host's nginx, which terminates TLS. Only
# DJANGO_SECRET_KEY is required, so the settings import elsewhere (mypy).

DEBUG = False

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": os.environ.get("POSTGRES_HOST", "database"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "NAME": os.environ.get("POSTGRES_DB", "magnet"),
        "USER": os.environ.get("POSTGRES_USER", "magnet"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
    }
}

# the website is served at MAGNET_API_BASE_URL (settings.py), e.g.
# https://magnet.supercollider.online/
_site = urlsplit(MAGNET_API_BASE_URL)
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", _site.hostname or "").split()
CSRF_TRUSTED_ORIGINS = [f"{_site.scheme}://{_site.netloc}"]

# HTTPS: the proxy sets X-Forwarded-Proto and redirects http to https itself
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# short for the beta; covers the git and package docs hosts below the site's
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "3600"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SILENCED_SYSTEM_CHECKS = [
    # SECURE_SSL_REDIRECT: the proxy redirects
    "security.W008",
    # HSTS preload: not for a beta
    "security.W021",
]

# mail (signup, team changes, errors), by default via OVH's SMTP (MX Plan)
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST", "ssl0.ovh.net")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "465"))
EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "1") in ("1", "true")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "") in ("1", "true")
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_TIMEOUT = 30
# the name tells people where the mail comes from; OVH only sends from the mailbox
# the SMTP login belongs to
DEFAULT_FROM_EMAIL = os.environ.get(
    "DEFAULT_FROM_EMAIL", "magnet.supercollider.online <mail@supercollider.online>"
)
SERVER_EMAIL = DEFAULT_FROM_EMAIL
# get the tracebacks of server errors (space-separated addresses)
ADMINS = os.environ.get("DJANGO_ADMINS", "").split()

# everything from INFO on to stdout, the container's log (Django's own loggers
# propagate here; server errors are also mailed to ADMINS)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "{levelname} {name}: {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

STORAGES = {
    **STORAGES,
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# tasks go to the database, `manage.py db_worker` runs them
TASKS = {"default": {**TASKS["default"], "BACKEND": "django_tasks_db.DatabaseBackend"}}
