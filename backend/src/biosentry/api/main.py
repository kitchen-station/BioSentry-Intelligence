"""ASGI entry point: `uvicorn biosentry.api.main:app --host 127.0.0.1` (make api-dev).

Bind loopback. In a container it binds 0.0.0.0 and the host publishes it on loopback only (deploy/).
"""

from .app import create_app

app = create_app()
