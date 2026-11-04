# Magnet server

> Package index for SuperCollider

## Stack

Magnet server is written using the Python web Django and the django rest framework.
The REST API is protected via OAuth2.

The frontend is written within Django and uses HTMX and AlpineJS.

The package index is a git repository, which only the server writes and the `magnet` CLI fetches.
The artifacts (`.tar.gz`) are stored on the local filesystem at their download path and served by nginx.
nginx also serves a static HTTP mirror of the index (`/index/`: `config.json`, `<c>/<name>.jsonl`) for clients without git, e.g. in the browser; the server exports it after every index write.

## Local stack

`docker-compose.yml` runs everything the `magnet` CLI needs:

| Service      | URL                                        |                                       |
|--------------|--------------------------------------------|---------------------------------------|
| `server`     | <http://localhost:8000>                    | Django: frontend, admin, API          |
| `nginx`      | <http://localhost:8080/dl/…>               | artifact downloads                    |
| `nginx`      | <http://localhost:8080/index/…>            | static HTTP mirror of the index       |
| `git_daemon` | `git://localhost:9418/magnet_index.git`    | the index                             |
| `git_web`    | <http://localhost:8081>                    | the index in gitweb, and over HTTP    |
| `nginx`      | <http://localhost:8082/<name>/>            | the docs of the packages              |
| `database`   | `localhost:5432`                           | postgres                              |
| `worker`     |                                            | background tasks (class extraction)   |
| `docs_worker`|                                            | docs builds (needs `make docs-builder`) |

```shell
make up          # docker compose up -d --build; creates the index on the first start
make superuser   # owner of the seeded packages
make seed        # publishes the CLI's fixture packages from ../magnet_cli/tests/fixtures
                 # (lines without `kinds`, the format before artifact kinds, are skipped)
```

`make` uses `podman` if `docker` is no executable (e.g. only a shell alias), or pass `DOCKER=…`.
Use the index with the CLI via `MAGNET_GIT_INDEX_URL=git://localhost:9418/magnet_index.git`, or over HTTP (git's smart protocol, read-only, as in production behind TLS) via `http://localhost:8081/magnet_index.git`.
Releases can also be published by hand:

```shell
docker compose exec server python manage.py publish --help
```

`magnet login` works without any setup (a migration creates the CLI's OAuth application).
For scripts and CI, create a token and pass it as `MAGNET_TOKEN`:

```shell
docker compose exec server python manage.py create_token --user you@example.org --days 30
```

Then `magnet package && magnet publish` in a project publishes it (the CLI finds the server via `api` in the index's `config.json`, or `MAGNET_API_URL`).
Uploads are limited to 50 MB (`MAGNET_MAX_UPLOAD_SIZE`); a proxy in front of Django needs the same limit, e.g. nginx `client_max_body_size 50m`.

### Package docs

`make docs-builder` builds the image `magnet-docs-builder:sc3.14.1` (`docs_builder/`): a headless SuperCollider and the magnet CLI, both compiled from source (SuperCollider takes a while, once).
The service `docs_worker` builds docs with it (queue `docs`): it starts the build containers through the engine's socket (`MAGNET_DOCS_SOCKET`, default podman machine's `/run/podman/podman.sock`; Docker: `/var/run/docker.sock`) and publishes them to `docs/<name>` in the `artifacts` volume.
Finalising a version that becomes the package's current one queues its build, so do yanks, un-yanks and hard deletes that change the current version (no version left: the docs are removed).
`python manage.py build_docs NAME … | --all` and the admin action "Rebuild the docs" queue builds by hand.
nginx serves the docs on their own port (`NGINX_DOCS_PORT`, default 8082), in production a host of its own (`MAGNET_DOCS_BASE_URL`); runserver serves them at `/docs/` (`settings_local`).
`scripts/build_docs.sh` builds the docs of one package by hand:

```shell
scripts/build_docs.sh redUniverse '{ legacy-quark = true }' /tmp/docs/reduniverse
```

The index and the artifacts live in the volumes `index` and `artifacts`; `docker compose down -v` resets everything.
The index (and its HTTP mirror) can always be regenerated from the database with `python manage.py rebuild_index`.

Background tasks (`django.tasks`) go to the database; `worker` runs those of the queue `default` (`manage.py db_worker`), its log shows each task.
Finished tasks stay in the table (admin: "Tasks Database Backend › Task Results"); `python manage.py prune_db_task_results` deletes old ones (see `--help`), in production e.g. from a timer.

Without docker, `cd src && DJANGO_SETTINGS_MODULE=magnet.settings_local uv run manage.py runserver` serves the
artifacts itself under `/dl/` and the index mirror under `/index/`, the index is a bare repository at `~/git/magnet/magnet_index.git`
(`manage.py init_index`, then `manage.py seed_cli_fixtures ../../magnet_cli/tests/fixtures`).

### Environment variables

The base settings read these, with defaults for a local checkout (the docker stack sets them in `docker-compose.yml`):

Variable | Default | Comment
--- | --- | ---
`GIT_INDEX_REPO_PATH` | `~/git/magnet/magnet_index.git` | bare git repo of the index
`MAGNET_ARTIFACTS_ROOT` | `~/git/magnet/magnet_artifacts` | artifacts, stored at their download path, and the index mirror (`index/`)
`MAGNET_DOWNLOAD_BASE_URL` | `http://localhost:8000/` | base URL of the `dl` template and of `http` (`<base>index/`) in the index `config.json`
`MAGNET_API_BASE_URL` | `http://localhost:8000/` | `api` in the index `config.json`, where the CLI logs in and publishes
`MAGNET_OAUTH_REDIRECT_URIS` | `http://localhost:8000/users/display-token http://127.0.0.1:8000/users/display-token` | redirect URIs of the CLI's OAuth application (applied by a migration)
`MAGNET_LEGAL_NAME`, `MAGNET_LEGAL_EMAIL`, `MAGNET_LEGAL_HOSTING` | none, `mail@supercollider.online`, none | operator, contact and hosting provider on `/legal` (legal notice, privacy policy)
`MAGNET_DOCS_BUILDS` | off | `1` enables docs builds (on in the docker stack)
`MAGNET_DOCS_RUNNER` | `podman` | CLI which starts the build containers, e.g. `docker` against podman's socket (`DOCKER_HOST`)
`MAGNET_DOCS_IMAGE` | `magnet-docs-builder:sc3.14.1` | builder image (`make docs-builder`), its SuperCollider in `MAGNET_DOCS_SCLANG_VERSION`
`MAGNET_DOCS_WORK_DIR` | `<tmp>/magnet-docs` | work dirs of the builds; a worker in a container must see it at the engine host's path
`MAGNET_DOCS_GIT_INDEX_URL` | | index for `magnet install` in the build container
`MAGNET_DOCS_INSTALL_NETWORK` | engine default | network of the install container, e.g. `host`
`MAGNET_DOCS_BASE_URL` | `http://localhost:8082/` | where the docs are served, for links to the docs of dependencies
`MAGNET_DOCS_TIMEOUT`, `_MEMORY`, `_PIDS_LIMIT`, `_CPUS` | `600`, `2g`, `256`, none | limits per build container

`manage.py init_index` rewrites `config.json` when one of the URLs changed.

## Deployment

`deploy/` runs magnet on a server with rootless podman behind the host's nginx (TLS): `compose.yml` (server, worker, docs worker, postgres, a static nginx for `/dl/`, `/index/` and the package docs, the index over HTTP; ports only on `127.0.0.1:8310–8313`), `env.example` (copied to `deploy/.env`, which is gitignored) and `nginx.conf` (the host's server blocks for `magnet.`, `git.magnet.` and `package-docs.magnet.supercollider.online`).

First setup on Ubuntu 24.04:

```shell
# slirp4netns/netavark/aardvark-dns are only recommended by podman: name them, in case
# apt skips recommended packages (rootless networking, service names in compose)
sudo apt install podman podman-compose uidmap slirp4netns netavark aardvark-dns systemd-container
sudo useradd --create-home --shell /bin/bash magnet   # no password: reached via machinectl only
grep magnet /etc/subuid /etc/subgid         # subordinate IDs for rootless podman; if missing:
# sudo usermod --add-subuids 200000-265535 --add-subgids 200000-265535 magnet  (a free range)
sudo loginctl enable-linger magnet          # its services run without a login

# as magnet, in a real systemd session (`sudo -iu` has no XDG_RUNTIME_DIR for `systemctl --user`)
sudo machinectl shell magnet@
systemctl --user enable --now podman.socket # the docs worker starts build containers through it
podman run --rm docker.io/library/alpine true   # rootless podman works
podman info --format '{{.Host.NetworkBackend}}' # netavark; if it says cni (podman's storage was set up
# before netavark was installed): put `[network]` `network_backend = "netavark"` into
# ~/.config/containers/containers.conf, then `podman system reset --force` (before anything else runs)
git clone <magnet_server repo> ~/magnet_server && git clone <magnet_cli repo> ~/magnet_cli
mkdir ~/docs-work
cd ~/magnet_server && DOCKER=podman make docs-builder   # compiles SuperCollider, takes a while
cp deploy/env.example deploy/.env           # fill in: secrets, mail password, MAGNET_DOCS_SOCKET (`id -u`), …
podman compose -f deploy/compose.yml up -d --build
podman compose -f deploy/compose.yml exec server python manage.py createsuperuser
podman compose -f deploy/compose.yml exec server python manage.py sendtestemail you@example.org
```

Then the host's nginx, certificate first (the config refers to it):

```shell
sudo certbot certonly --nginx -d magnet.supercollider.online \
    -d git.magnet.supercollider.online -d package-docs.magnet.supercollider.online
sudo cp deploy/nginx.conf /etc/nginx/sites-available/magnet
sudo ln -s /etc/nginx/sites-available/magnet /etc/nginx/sites-enabled/magnet
sudo nginx -t && sudo systemctl reload nginx
```

- **Start on boot and weekly cleanup:** `deploy/magnet.service` (the stack) and `deploy/magnet-prune.timer` (deletes finished tasks older than two weeks), as magnet:
  `cp deploy/magnet*.service deploy/magnet*.timer ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable magnet.service magnet-prune.timer && systemctl --user start magnet-prune.timer`.
- **Updates:** `git pull && podman compose -f deploy/compose.yml up -d --build` (migrations run on start). After a change of the CLI or the SuperCollider version: `make docs-builder`, then `… exec server python manage.py build_docs --all`.
- **Logs:** `podman compose -f deploy/compose.yml logs -f server`; the privacy policy says nginx's logs are kept 14 days at most (Ubuntu's logrotate default for nginx).
- **The CLI** uses `https://git.magnet.supercollider.online/magnet_index.git` by default (`MAGNET_GIT_INDEX_URL` replaces it, e.g. for the local stack).
- If Ubuntu's `podman-compose` doesn't wait for health checks (`depends_on: condition`), the workers restart until the server has migrated (`restart: unless-stopped`).

## Development

Magnet uses `pre-commit` to enforce formatting.
Run `pre-commit install` to set up all necessary tools which will be invoked before each commit.

### Testing

Magnet uses `pytest` with `factory_boy` and `mypy`.
To run all tests, use

```shell
make test
```

## Resources

### API Design

* Scztt spec: <https://github.com/scztt/baryon-cli/blob/main/src/specs/spec.json>
* <https://gist.github.com/scztt/9e91c28739d42c2ecc00cf034ccdab11>

### Rust / Cargo

* <https://doc.rust-lang.org/cargo/reference/registry-index.html>
* <https://doc.rust-lang.org/cargo/reference/registry-web-api.html>

### Python / PyPi

* <https://docs.pypi.org/api/json/>
* <https://docs.pypi.org/api/upload/>

## License

AGPL-3.0
