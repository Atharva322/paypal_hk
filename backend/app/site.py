from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles


def serve(api: Starlette, store_dist: Path) -> Starlette:
    """One origin for a deployment: the built store at /, the API under /api as in dev."""

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        # A mounted app's own startup does not run, so the outer app runs it.
        async with api.router.lifespan_context(api):
            yield

    return Starlette(
        lifespan=lifespan,
        routes=[
            Mount("/api", app=api),
            Mount("/", app=StaticFiles(directory=store_dist, html=True)),
        ],
    )
