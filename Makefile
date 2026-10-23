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
