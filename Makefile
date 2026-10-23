# assumes uv has been setup

lint:
	uv run ruff check .
	uv run ruff format --check .

test-mypy:
	cd src && uv run mypy .

test-unit:
	cd src && uv run pytest

schema:
	cd src && uv run manage.py spectacular > schema.yaml

test: lint test-unit test-mypy

# local docker stack, see README.md

CLI_FIXTURES ?= ../magnet_cli/tests/fixtures
# docker, or podman where `docker` is only a shell alias
DOCKER ?= $(shell command -v docker >/dev/null 2>&1 && echo docker || echo podman)

up:
	$(DOCKER) compose up -d --build

superuser:
	$(DOCKER) compose exec server python manage.py createsuperuser

# publishes the fixture packages of the magnet CLI into the stack
seed:
	$(DOCKER) compose run --rm --no-deps \
		-v "$(abspath $(CLI_FIXTURES)):/fixtures:ro" \
		--entrypoint python server manage.py seed_cli_fixtures /fixtures

.PHONY: lint test-mypy test-unit schema test up superuser seed
