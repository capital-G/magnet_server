FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY --from=ghcr.io/astral-sh/uv:0.8.5 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml .
COPY uv.lock .
RUN uv sync --locked

COPY src .

# RUN python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["uv", "run", "gunicorn", "baryon_server.wsgi:application", "--bind", "0.0.0.0:8000"]
