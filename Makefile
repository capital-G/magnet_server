# assumes uv has been setup

test-mypy:
	uv run mypy .

test-unit:
	uv run pytest

schema:
	uv run manage.py spectacular > schema.yaml

test: test-unit test-mypy
