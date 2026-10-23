FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# install into the system python, so `python manage.py …` works without uv
ENV UV_PROJECT_ENVIRONMENT="/usr/local/"
ENV DJANGO_SETTINGS_MODULE=magnet.settings_local_docker

# git_db shells out to git to write the index
RUN apk add --no-cache git

COPY --from=ghcr.io/astral-sh/uv:0.8.5 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

COPY src .

# needs no database, only importable settings
RUN DJANGO_SECRET_KEY=collectstatic python manage.py collectstatic --noinput

EXPOSE 8000

ENTRYPOINT [ "/usr/local/bin/entrypoint.sh" ]
