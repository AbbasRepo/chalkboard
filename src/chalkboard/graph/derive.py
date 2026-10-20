"""Recomputes the derived edges.

The raw action edges are the evidence; these are the summary. A question
like "who is this side routing through" is a traversal over hundreds of
PASSED_TO edges, and asking it fresh every three seconds would not keep up
with live play. Materialising it into a weighted edge turns that into a
single hop.

It is the same reasoning behind any materialised aggregate: pay the cost
once on write so the read is cheap, and accept that the summary is a few
seconds behind the facts.

Scope differs by edge, deliberately:

* ``LINKS_WITH``, ``CIRCULATES_THROUGH``, ``PRESSES_IN`` are per match - a
  passing network is a fact about one game.
* ``NEMESIS_OF`` spans every match in the graph, because a duel record is
  only worth remarking on once it has recurred across fixtures.

Run once over a finished match, or on a timer alongside a live stream:

    python -m chalkboard.graph.derive --match CBM-00042
    python -m chalkboard.graph.derive --match CBM-00042 --watch
    python -m chalkboard.graph.derive --all
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Protocol

DEFAULT_CYPHER = Path(__file__).resolve().parents[3] / "ontology" / "derived.cypher"

#: How often to recompute alongside a live stream. Three seconds is well
#: inside the rhythm of a match and cheap at this data volume.
DEFAULT_INTERVAL_S = 3.0

#: A pair has to combine at least this often before the link is worth
#: drawing. Below it the passing network becomes a hairball of one-offs.
MIN_PASSES = 3

#: Two players must have met this often before the matchup is a story.
MIN_DUELS = 2


class Session(Protocol):
    def run(self, query: str, **params: Any) -> Any: ...


def load_statements(path: Path | str = DEFAULT_CYPHER) -> list[tuple[str, str]]:
    """Split the Cypher file on its ``// --name:`` markers.

    A marker rather than a parser: the statements are ours, the file is
    small, and a dependency on a Cypher grammar would be absurd here.
    """
    text = Path(path).read_text(encoding="utf-8")
    parts = re.split(r"^//\s*--name:\s*(\w+)\s*$", text, flags=re.MULTILINE)
    statements: list[tuple[str, str]] = []
    for name, body in zip(parts[1::2], parts[2::2]):
        body = body.strip().rstrip(";").strip()
        if body:
            statements.append((name, body))
    return statements


class Deriver:
    def __init__(
        self,
        session: Session,
        statements: list[tuple[str, str]] | None = None,
        min_passes: int = MIN_PASSES,
        min_duels: int = MIN_DUELS,
    ) -> None:
        self.session = session
        self.statements = statements if statements is not None else load_statements()
        self.min_passes = min_passes
        self.min_duels = min_duels

    def run_once(self, match_id: str) -> list[str]:
        """Recompute every derived edge for one match. Returns names run."""
        params = {
            "matchId": match_id,
            "minPasses": self.min_passes,
            "minDuels": self.min_duels,
            "now": time.time(),
        }
        ran = []
        for name, body in self.statements:
            self.session.run(body, **params)
            ran.append(name)
        return ran

    def watch(
        self,
        match_id: str,
        interval_s: float = DEFAULT_INTERVAL_S,
        iterations: int | None = None,
    ) -> int:
        """Recompute on a timer until interrupted.

        ``iterations`` bounds the loop so tests do not have to interrupt it.
        """
        count = 0
        try:
            while iterations is None or count < iterations:
                self.run_once(match_id)
                count += 1
                if iterations is None or count < iterations:
                    time.sleep(interval_s)
        except KeyboardInterrupt:
            pass
        return count


def match_ids(session: Session) -> list[str]:
    result = session.run("MATCH (m:Match) RETURN m.match_id AS id ORDER BY id")
    return [record["id"] for record in result]


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Recompute the derived edges.")
    parser.add_argument("--match", help="match_id to recompute")
    parser.add_argument("--all", action="store_true", help="every match in the graph")
    parser.add_argument("--watch", action="store_true", help="recompute on a timer")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S)
    parser.add_argument("--cypher", default=str(DEFAULT_CYPHER))
    args = parser.parse_args(argv)

    if not args.match and not args.all:
        parser.error("pass --match <id> or --all")

    try:
        from dotenv import load_dotenv
        from neo4j import GraphDatabase
    except ImportError:
        print('neo4j and python-dotenv are required. pip install -e ".[dev]"', file=sys.stderr)
        return 2

    load_dotenv()
    uri, password = os.environ.get("NEO4J_URI"), os.environ.get("NEO4J_PASSWORD")
    if not uri or not password:
        print("NEO4J_URI and NEO4J_PASSWORD must be set in .env", file=sys.stderr)
        return 2

    statements = load_statements(args.cypher)
    driver = GraphDatabase.driver(uri, auth=(os.environ.get("NEO4J_USER", "neo4j"), password))
    try:
        with driver.session() as session:
            deriver = Deriver(session, statements)
            targets = match_ids(session) if args.all else [args.match]
            if not targets:
                print("no matches in the graph - load one first", file=sys.stderr)
                return 1

            if args.watch:
                target = targets[0]
                print(
                    f"recomputing {len(statements)} derived edges for {target} "
                    f"every {args.interval:g}s - Ctrl-C to stop",
                    file=sys.stderr,
                )
                deriver.watch(target, args.interval)
            else:
                for target in targets:
                    ran = deriver.run_once(target)
                    print(f"  {target}  {', '.join(ran)}", file=sys.stderr)
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
