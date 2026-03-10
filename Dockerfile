FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT="/usr/local/"

COPY --from=ghcr.io/astral-sh/uv:0.8.5 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml .
COPY uv.lock .

RUN uv sync --locked

#RUN uv sync --locked --system
#
# RUN uv pip compile pyproject.toml > requirements.txt
# RUN uv pip install -r requirements.txt

#COPY entrypoint.sh /usr/local/bin/entrypoint.sh
#RUN chmod +x /usr/local/bin/entrypoint.sh

COPY src .

# RUN python manage.py collectstatic --noinput

EXPOSE 8000

#ENTRYPOINT [ "/bin/sh", "/usr/local/bin/entrypoint.sh" ]
