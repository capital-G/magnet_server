# Magnet server

> Package index for SuperCollider

## Stack

Magnet server is written using the Python web Django and the django rest framework.
The REST API is protected via OAuth2.

The frontend is written within Django and uses HTMX and AlpineJS.

The packet assets are stored on the local filesystem - at a later stage a distributed storage such as Minio could be used.

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
