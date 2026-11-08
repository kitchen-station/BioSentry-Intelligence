"""ASGI entry point: `uvicorn biosentry.api.main:app --host 127.0.0.1` (make api-dev).

Identity is a stub (POST /session issues a token to any known clinician id), so bind loopback.
"""

from .app import create_app

app = create_app()
