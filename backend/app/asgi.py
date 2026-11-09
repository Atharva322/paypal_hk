import os
from pathlib import Path

from .main import create_app
from .site import serve

api = create_app()
# A deployment sets STORE_DIST to the built store. Locally Vite serves the store and
# proxies /api, so the API alone is enough.
store_dist = os.environ.get("STORE_DIST")
app = serve(api, Path(store_dist)) if store_dist else api
