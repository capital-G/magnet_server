import logging
import shutil
import tempfile
from pathlib import Path

import pytest

from git_db.files import DuplicateIndexEntry, ProjectIndexFile
from git_db.tests.factories import ReleaseFactory
from packages.models import Project, Release


@pytest.fixture
def project_index_file_factory():
    mathlib_fake_index_file = Path(__file__).parent / "mathlib"
    assert mathlib_fake_index_file.exists()

    def _factory(
        project: Project, source_file=mathlib_fake_index_file
    ) -> tempfile.TemporaryDirectory[str]:
        temp_directory = tempfile.TemporaryDirectory()
        temp_path = Path(temp_directory.name)

        project_index_file_path = temp_path / project.slug[0] / project.slug
        project_index_file_path.parent.mkdir(parents=True)

        shutil.copy(source_file, project_index_file_path)

        return temp_directory

    return _factory


@pytest.fixture
def mathlib_release():
    return ReleaseFactory(project__name="MathLib", version="1.0.0")


@pytest.mark.django_db
def test_empty_index_file(caplog, mathlib_release: Release):
    caplog.set_level(logging.DEBUG, logger="packages.git_db")

    with tempfile.TemporaryDirectory() as tmp:
        temp_directory = Path(tmp)

        with caplog.at_level(logging.DEBUG):
            project_index_file = ProjectIndexFile.for_project(
                temp_directory, mathlib_release.project
            )

        assert len(caplog.records) == 1
        assert caplog.records[0].levelname == "INFO"
        assert "Create new project index file" in caplog.records[0].message

        # should not fail to iterate over an empty file
        assert list(project_index_file.releases) == []


@pytest.mark.django_db
def test_existing_project_index_file_release(
    project_index_file_factory, mathlib_release: Release
):
    temp_directory = project_index_file_factory(mathlib_release.project)
    with temp_directory:
        project_index_file = ProjectIndexFile.for_project(
            Path(temp_directory.name), mathlib_release.project
        )

        for release, version in zip(
            project_index_file.releases, ["1.0.0", "1.1.0"], strict=True
        ):
            assert release.version == version

        project_index_file.verify_integrity()


@pytest.mark.django_db
def test_project_index_add_release(
    project_index_file_factory, mathlib_release: Release
):
    temp_directory = project_index_file_factory(mathlib_release.project)
    with temp_directory:
        project_index_file = ProjectIndexFile.for_project(
            Path(temp_directory.name), mathlib_release.project
        )

        with pytest.raises(DuplicateIndexEntry):
            project_index_file.add_release(release=mathlib_release)

        mathlib_release.version = "1.3.0"
        project_index_file.add_release(release=mathlib_release)

        for release, version in zip(
            project_index_file.releases, ["1.0.0", "1.1.0", "1.3.0"], strict=True
        ):
            assert release.version == version
