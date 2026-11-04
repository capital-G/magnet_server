"""The production settings (`magnet.settings_prod`), in a subprocess with their own
environment, since settings are loaded once per process."""

import json
import os
import subprocess
import sys

from django.conf import settings

ENVIRONMENT = {
    "DJANGO_SETTINGS_MODULE": "magnet.settings_prod",
    "DJANGO_SECRET_KEY": "a-long-random-production-key-" + "x" * 40,
    "MAGNET_API_BASE_URL": "https://magnet.example.org/",
    "EMAIL_HOST_USER": "mail@example.org",
    "DJANGO_ADMINS": "admin@example.org other@example.org",
}


def run(*args: str) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DJANGO_", "MAGNET_", "EMAIL_", "POSTGRES_"))
    }
    return subprocess.run(
        [sys.executable, *args],
        cwd=settings.BASE_DIR,
        env={**environment, **ENVIRONMENT},
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_deploy_checks_pass():
    result = run("manage.py", "check", "--deploy", "--fail-level", "WARNING")

    assert result.returncode == 0, result.stdout + result.stderr


def test_hosts_and_mail_come_from_the_environment():
    result = run(
        "-c",
        "import django, json; django.setup(); from django.conf import settings as s; "
        "from django.core.mail.backends.smtp import EmailBackend; "
        # the sender as the SMTP backend parses it (refuses e.g. unquoted dots)
        "sender = EmailBackend().prep_address(s.DEFAULT_FROM_EMAIL); "
        "print(json.dumps([s.ALLOWED_HOSTS, s.CSRF_TRUSTED_ORIGINS, s.ADMINS, "
        "s.EMAIL_HOST, s.EMAIL_PORT, s.EMAIL_USE_SSL, s.DEFAULT_FROM_EMAIL, sender, "
        "s.DEBUG]))",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        ["magnet.example.org"],
        ["https://magnet.example.org"],
        ["admin@example.org", "other@example.org"],
        "ssl0.ovh.net",
        465,
        True,
        "Magnet SuperCollider <mail@supercollider.online>",
        "mail@supercollider.online",
        False,
    ]
