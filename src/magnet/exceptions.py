from django.core.exceptions import RequestDataTooBig
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from git_db.files import IndexWriteError

"""
Errors of the API are JSON `{"detail": "<message>"}`, which the CLI prints
(`../magnet_cli/src/http_client/publish.rs`); it drops bodies of another shape.
"""


class PayloadTooLarge(exceptions.APIException):
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    default_detail = "Request body too large."
    default_code = "payload_too_large"


def messages(detail) -> list[str]:
    """The messages of a DRF error detail, which may be nested in lists and dicts
    (serializer errors); dict keys become a `field: ` prefix."""
    if isinstance(detail, dict):
        return [
            message if key == "non_field_errors" else f"{key}: {message}"
            for key, value in detail.items()
            for message in messages(value)
        ]
    if isinstance(detail, list):
        return [message for item in detail for message in messages(item)]
    return [str(detail)]


def api_exception_handler(exc, context) -> Response | None:
    if isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError(exc.messages)
    elif isinstance(exc, IndexWriteError):
        exc = exceptions.ValidationError(str(exc))
    elif isinstance(exc, RequestDataTooBig):
        exc = PayloadTooLarge()

    response = exception_handler(exc, context)
    if response is not None and isinstance(exc, exceptions.ValidationError):
        response.data = {"detail": "; ".join(messages(exc.detail))}
    return response
