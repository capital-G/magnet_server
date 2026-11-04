from .settings import *

DEBUG = True

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# the docs of the packages from the DEBUG view (docs.views.serve_docs)
MAGNET_DOCS_BASE_URL = "http://localhost:8000/docs/"
