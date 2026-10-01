"""FastAPI service. Serves the JSON API and, when it has been built, the React app."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__, config
from ..engine import Engine


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    planner: str = Field(default="rules", pattern="^(rules|llm)$")


def create_app(db_path: Path | None = None) -> FastAPI:
    app = FastAPI(title="Marketing Copilot", version=__version__,
                  description="Governed natural-language analytics over synthetic marketing data. Every answer shows its plan, SQL and grounding check.")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_methods=["GET", "POST"], allow_headers=["*"])
    engine = Engine(db_path)
    app.state.engine = engine

    @lru_cache(maxsize=1)
    def overview() -> dict:
        return engine.overview()

    @lru_cache(maxsize=1)
    def feed() -> list:
        return engine.anomaly_feed()

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__, "as_of": engine.end.isoformat(), "data_start": engine.start.isoformat(),
                "llm_planner": engine.llm is not None}

    @app.get("/api/catalog")
    def catalog() -> dict:
        return {**engine.cat.catalog_json(), "glossary": [{"id": g["id"], "title": g["title"]} for g in engine.cat.glossary],
                "questions": engine.sample_questions()}

    @app.get("/api/overview")
    def get_overview() -> dict:
        return overview()

    @app.get("/api/anomalies")
    def get_anomalies() -> dict:
        return {"incidents": feed()}

    @app.post("/api/ask")
    def ask(req: AskRequest) -> dict:
        try:
            ans = engine.ask(req.question, planner=req.planner)
        except Exception as e:  # never leak a stack trace to a client
            raise HTTPException(status_code=500, detail="The copilot could not answer that question.") from e
        return ans.model_dump(mode="json")

    @app.get("/api/eval")
    def get_eval() -> dict:
        path = config.REPORTS_DIR / "eval.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="No evaluation report yet. Run `python -m mktg_copilot eval`.")
        return json.loads(path.read_text())

    dist = config.ROOT / "frontend" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            f = dist / path
            return FileResponse(f if path and f.is_file() else dist / "index.html")

    return app


app = create_app()
