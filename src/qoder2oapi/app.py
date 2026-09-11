import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from qoder2oapi.catalog import catalog_manager
from qoder2oapi.config import settings
from qoder2oapi.http import close_http_client
from qoder2oapi.refresh import ensure_fresh
from qoder2oapi.routes.admin import router as admin_router
from qoder2oapi.routes.openai import router as openai_router
from qoder2oapi.token_store import token_store


async def _background_pat_refresh_loop():
    while True:
        try:
            await asyncio.sleep(1800)
            accounts = token_store.list_accounts()
            for acc in accounts:
                if acc.kind == "pat" and acc.enabled and not acc.skip_auth:
                    try:
                        await ensure_fresh(acc)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            break
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    refresh_task = asyncio.create_task(_background_pat_refresh_loop())
    try:
        await catalog_manager.fetch_models()
    except Exception:
        pass
    try:
        yield
    finally:
        refresh_task.cancel()
        try:
            await refresh_task
        except asyncio.CancelledError:
            pass
        await close_http_client()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Qoder2OAPI",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(openai_router)
    app.include_router(admin_router)

    static_dir = Path(__file__).parent / "static" / "admin"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/admin/bootstrap")
    async def admin_bootstrap():
        return {
            "needs_api_key": True,
            "hint": "把启动日志或 data/api_key.txt 里的本代理密钥粘贴到控制台。",
        }

    @app.get("/")
    @app.get("/admin")
    async def admin_index():
        index_file = static_dir / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return {"message": "Qoder2OAPI Admin UI placeholder"}

    return app


app = create_app()


def main():
    uvicorn.run(
        "qoder2oapi.app:app",
        host=settings.qoder2oapi_host,
        port=settings.qoder2oapi_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
