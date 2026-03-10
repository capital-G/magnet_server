FROM debian:12

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gitweb \
    lighttpd \
    && rm -rf /var/lib/apt/lists/*

COPY lighttpd.conf /etc/lighttpd/lighttpd.conf
COPY gitweb.conf /etc/gitweb.conf
