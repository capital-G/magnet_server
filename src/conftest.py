import pytest


@pytest.fixture(autouse=True)
def magnet_data_dirs(settings, tmp_path):
    """Tests never touch the shared local index and artifacts (see settings.py)."""
    # PBKDF2 takes ~0.2s per user the factories create
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.GIT_INDEX_REPO_PATH = tmp_path / "magnet_index.git"
    settings.MAGNET_ARTIFACTS_ROOT = tmp_path / "magnet_artifacts"
    # reassigning STORAGES makes Django recreate the storages with the new root
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {
            "BACKEND": "packages.storage.ArtifactStorage",
            "OPTIONS": {"location": settings.MAGNET_ARTIFACTS_ROOT},
        },
    }
