"""The significance gate: decides which moments deserve a sentence.

This is the hinge the whole design turns on. Around 1,300 actions arrive in
a match and perhaps thirty are worth remarking on. Without a gate the system
either explains everything - unreadable, and 1,300 model calls - or picks
arbitrarily. The gate makes that choice explicit, cheap and reviewable.

Nothing reaches an agent that this has not already justified. The agents are
handed a subject *and* the facts behind it; they never decide what to talk
about and never query the graph speculatively. That is what makes the output
defensible rather than merely fluent.

Why a query rather than a fourth agent
--------------------------------------
* Deterministic - the same window always scores the same.
* Free - about 1,800 executions a match at no cost.
* Fast - milliseconds, so it keeps up with live play.
* Arguable - you can disagree with a weight and change it. You cannot
  disagree with a model's intuition.
* Demonstrable - the query and the subgraph it returned both go on screen.

Cooldown
--------
The window is 90 seconds and the gate runs every 3, so a single good passage
would otherwise fire thirty times in a row saying much the same thing. After
a moment fires, the gate stays quiet for ``cooldown_s`` of match time. That
is what makes the output read like commentary rather than a stuck alarm.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Protocol

DEFAULT_CYPHER = Path(__file__).resolve().parents[3] / "ontology" / "significance.cypher"

#: Seconds of match time the gate looks back over. A passage of play - a
#: press, a build-up, a counter - takes about this long. Shorter and you see
#: a single pass; longer and the goal is averaged in with the dull minutes
#: either side.
WINDOW_S = 90.0

#: How often the gate runs, in match seconds.
TICK_S = 3.0

#: Match seconds of silence after a moment fires. Tuned so the system speaks
#: roughly once every three minutes, which is the rhythm of real commentary.
COOLDOWN_S = 75.0

#: Most evidence actions handed to the Explainer. Enough to justify a claim,
#: few enough to fit a prompt and a readable subgraph on screen.
MAX_EVIDENCE = 12


class Session(Protocol):
    def run(self, query: str, **params: Any) -> Any: ...


@dataclass
class Moment:
    """A passage of play the gate judged worth explaining."""

    match_id: str
    clock_s: float
    score: float
    evidence: list[dict[str, Any]] = field(default_factory=list)
    window_size: int = 0

    @property
    def kinds(self) -> set[str]:
        return {e["kind"] for e in self.evidence}

    @property
    def players(self) -> list[str]:
        """Names the Explainer is permitted to use. Anything else is invented."""
        names = []
        for item in self.evidence:
            for key in ("actor", "target"):
                name = item.get(key)
                if name and name not in names:
                    names.append(name)
        return names

    @property
    def zones(self) -> list[str]:
        return [z for z in {e.get("zone") for e in self.evidence} if z]

    def summary(self) -> str:
        """One line, from graph facts alone. No model involved.

        This is also the fallback the Explainer degrades to when inference is
        unavailable - plainer, but never wrong.
        """
        parts = []
        shots = [e for e in self.evidence if e["kind"] == "SHOT"]
        turnovers = [e for e in self.evidence if e["kind"] == "DISPOSSESSED"]
        breaks = [e for e in self.evidence if e.get("breaking", 0) > 0]
        if shots:
            best = max(shots, key=lambda e: e.get("chance", 0))
            parts.append(f"{best['actor']} shot ({best['outcome'].lower()})")
        if turnovers:
            parts.append(f"{len(turnovers)} turnover(s) under pressure")
        if breaks:
            parts.append(f"{len(breaks)} line-breaking pass(es)")
        where = f" in {self.zones[0]}" if self.zones else ""
        return (", ".join(parts) or "notable passage") + where


def load_query(path: Path | str = DEFAULT_CYPHER) -> str:
    text = Path(path).read_text(encoding="utf-8")
    parts = re.split(r"^//\s*--name:\s*\w+\s*$", text, flags=re.MULTILINE)
    return parts[-1].strip().rstrip(";").strip()


class SignificanceGate:
    def __init__(
        self,
        session: Session,
        query: str | None = None,
        threshold: float | None = None,
        window_s: float = WINDOW_S,
        cooldown_s: float = COOLDOWN_S,
        max_evidence: int = MAX_EVIDENCE,
    ) -> None:
        self.session = session
        self.query = query if query is not None else load_query()
        self.threshold = (
            threshold
            if threshold is not None
            else float(os.environ.get("SIGNIFICANCE_THRESHOLD", "1.8"))
        )
        self.window_s = window_s
        self.cooldown_s = cooldown_s
        self.max_evidence = max_evidence
        self._last_fired_at: float | None = None

    def check(self, match_id: str, now_s: float) -> Moment | None:
        """Score the window ending at ``now_s``. Returns a Moment or nothing."""
        if self._in_cooldown(now_s):
            return None

        result = self.session.run(
            self.query,
            matchId=match_id,
            now=now_s,
            window=self.window_s,
            threshold=self.threshold,
            maxEvidence=self.max_evidence,
        )
        rows = list(result)
        if not rows:
            return None

        row = rows[0]
        evidence = [dict(e) for e in (row["evidence"] or []) if e.get("event_id")]
        if not evidence:
            # Scored above threshold but nothing citable. Staying silent is
            # correct: an unevidenced claim is the one thing this system
            # must never make.
            return None

        self._last_fired_at = now_s
        return Moment(
            match_id=match_id,
            clock_s=now_s,
            score=float(row["score"]),
            evidence=evidence,
            window_size=int(row.get("window_size") or len(evidence)),
        )

    def sweep(
        self, match_id: str, until_s: float, tick_s: float = TICK_S
    ) -> Iterator[Moment]:
        """Walk a finished match end to end, yielding every moment.

        Used to rehearse a demo and to tune the threshold offline, without
        waiting out a live stream.
        """
        now = self.window_s
        while now <= until_s:
            moment = self.check(match_id, now)
            if moment:
                yield moment
            now += tick_s

    def _in_cooldown(self, now_s: float) -> bool:
        if self._last_fired_at is None:
            return False
        return (now_s - self._last_fired_at) < self.cooldown_s


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run the significance gate.")
    parser.add_argument("--match", required=True)
    parser.add_argument("--until", type=float, default=5400.0,
                        help="match seconds to sweep to (default: full match)")
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--cypher", default=str(DEFAULT_CYPHER))
    args = parser.parse_args(argv)

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

    driver = GraphDatabase.driver(uri, auth=(os.environ.get("NEO4J_USER", "neo4j"), password))
    fired = 0
    try:
        with driver.session() as session:
            gate = SignificanceGate(session, load_query(args.cypher), args.threshold)
            print(f"threshold {gate.threshold}  window {gate.window_s:g}s  "
                  f"cooldown {gate.cooldown_s:g}s", file=sys.stderr)
            for moment in gate.sweep(args.match, args.until):
                fired += 1
                minute = int(moment.clock_s // 60)
                print(f"  {minute:>3}'  {moment.score:5.2f}  {moment.summary()}")
    finally:
        driver.close()

    print(f"\n{fired} moments. Aim for 25-35 - raise the threshold if more, "
          f"lower it if fewer.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
