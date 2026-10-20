"""FastAPI: the thin layer between the pipeline and the browser.

Four endpoints and a static page. The API owns no logic of its own - it
streams what the pipeline emits and exposes two controls the demo needs.

    uvicorn chalkboard.app.api:app --reload

Then open http://localhost:8000.

The model switch
----------------
``POST /api/model`` flips ``MODEL_ENABLED`` for the running process. That is
the kill switch demonstrated on camera: turn it off, watch the system keep
producing correct but plainer output straight from the graph, turn it back
on. It is a feature rather than a debug hook, because graceful degradation
is explicitly scored by the hackathon category this project is entered in.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..agents.explainer import Explainer
from ..agents.llm import get_client
from ..agents.personaliser import Personaliser
from ..graph.insights import InsightStore
from ..graph.significance import SignificanceGate
from ..mcp.tools import GraphTools
from .pipeline import MatchRunner

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Chalkboard", description="The tactics board that answers back")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_driver = None


def driver():
    global _driver
    if _driver is None:
        from dotenv import load_dotenv
        from neo4j import GraphDatabase

        load_dotenv()
        _driver = GraphDatabase.driver(
            os.environ["NEO4J_URI"],
            auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]),
        )
    return _driver


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/matches")
def matches() -> list[dict[str, Any]]:
    with driver().session() as session:
        rows = session.run(
            "MATCH (m:Match) "
            "OPTIONAL MATCH (m)-[:HOME_TEAM]->(h:Team) "
            "OPTIONAL MATCH (m)-[:AWAY_TEAM]->(a:Team) "
            "RETURN m.match_id AS match_id, m.matchweek AS matchweek, "
            "       h.short_name AS home, a.short_name AS away "
            "ORDER BY m.matchweek"
        )
        return [dict(r) for r in rows]


@app.get("/api/stream")
def stream(
    match: str,
    follow: str | None = None,
    language: str = "en",
    speed: float = 20.0,
    start: float = 0.0,
) -> StreamingResponse:
    """Server-sent events: clock ticks and insights, as the match plays."""

    def events() -> Iterator[str]:
        session = driver().session()
        try:
            runner = MatchRunner(
                session=session,
                match_id=match,
                gate=SignificanceGate(session),
                explainer=Explainer(
                    get_client(), GraphTools(session), store=InsightStore(session)
                ),
                personaliser=Personaliser(get_client(), follow, language),
                speed=speed,
            )
            for payload in runner.run(start_s=start):
                yield f"data: {json.dumps(payload)}\n\n"
        except Exception as error:  # noqa: BLE001
            log.exception("stream failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(error)})}\n\n"
        finally:
            session.close()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/model")
def model_state() -> dict[str, Any]:
    enabled = (os.environ.get("MODEL_ENABLED") or "true").lower() not in (
        "false", "0", "no", "off",
    )
    return {"enabled": enabled, "client": get_client().name}


@app.post("/api/model")
def set_model(enabled: bool) -> dict[str, Any]:
    """The kill switch. Flips inference for the running process."""
    os.environ["MODEL_ENABLED"] = "true" if enabled else "false"
    return model_state()


@app.get("/api/health")
def health() -> dict[str, Any]:
    try:
        with driver().session() as session:
            nodes = session.run("MATCH (n) RETURN count(n) AS n").single()["n"]
        return {"graph": "up", "nodes": nodes, **model_state()}
    except Exception as error:  # noqa: BLE001
        return {"graph": "down", "error": str(error), **model_state()}
