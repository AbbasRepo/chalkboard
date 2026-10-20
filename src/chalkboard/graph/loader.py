"""Writes the synthetic event stream into the match knowledge graph.

Reads JSON Lines on stdin, so it pipes straight from the generator:

    python -m chalkboard.generator.cli stream | python -m chalkboard.graph.loader

No language model is involved, and none should be. Events arrive as
structured records with explicit team, player, type, coordinates and outcome.
There is nothing to extract - an extraction step could only introduce error
into facts that arrived unambiguous. See CLAUDE.md, rule 1.

Shape of the work
-----------------
``ontology/match.yaml`` is the semantic model and this module executes it.
An event becomes a relationship when it has two participants (a pass is
Player -> Player) and a node only when arity forces it (a shot involves
shooter, keeper and zone). Adding an action should mean editing YAML.

Three records are handled in Python rather than by the mapping, because they
are not flat and the mapping describes flat structure only:

* the static pitch - 18 Zone nodes and their adjacency, written once
* MATCH_META - nested squads, officials, stadium, prediction
* insights - written later by the agents, not by this loader

Two properties that matter
--------------------------
**Idempotent.** Everything is MERGEd on its key, so replaying an event - or
restarting a stream mid-match - converges rather than duplicating. A
streaming loader that is not idempotent corrupts the graph the first time
anything is retried.

**Properties are only written when present.** An event names a player but
carries no name; the squad list has it. Writing absent fields as null would
blank the name the moment any event touched that player.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Protocol

import yaml

from ..generator.pitch import adjacent, all_zones

#: Labels and relationship types are interpolated into Cypher because Neo4j
#: does not accept them as parameters. They come from our own mapping file,
#: but validating them keeps a malformed edit from producing a broken query.
_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

DEFAULT_MAPPING = Path(__file__).resolve().parents[3] / "ontology" / "match.yaml"

#: Carried on every action edge. Without the temporal spine nothing can be
#: ordered; without match_id, aggregates over shared Zone nodes silently mix
#: fixtures together.
SPINE = ("event_id", "clock_s", "period", "match_id", "possession_id", "sequence", "outcome")


class Session(Protocol):
    """The slice of a neo4j session this module uses.

    Narrow on purpose: the tests supply a fake that records statements, so
    the suite runs with no database.
    """

    def run(self, query: str, **params: Any) -> Any: ...


def _ident(value: str, what: str) -> str:
    if not _IDENT.match(value):
        raise ValueError(f"invalid {what} in ontology mapping: {value!r}")
    return value


class OntologyLoader:
    """Turns event records into batched, idempotent Cypher writes."""

    def __init__(self, mapping: dict[str, Any], session: Session, batch_size: int = 200) -> None:
        self.mapping = mapping
        self.session = session
        self.batch_size = batch_size

        self.entities: dict[str, Any] = mapping.get("entities", {})
        self.reified: dict[str, Any] = mapping.get("reified", {})
        self.actions: list[dict[str, Any]] = mapping.get("actions", [])

        self.keys: dict[str, str] = {
            label: spec["key"]
            for label, spec in {**self.entities, **self.reified}.items()
        }

        self._node_rows: dict[str, dict[Any, dict[str, Any]]] = defaultdict(dict)
        self._edge_rows: dict[str, dict[tuple, dict[str, Any]]] = defaultdict(dict)
        self._edge_spec: dict[str, dict[str, Any]] = {}
        self._pending = 0
        self.counts: dict[str, int] = defaultdict(int)

        for label, key in self.keys.items():
            _ident(label, "node label")
            _ident(key, "key property")
        for edge in self.actions + mapping.get("structure", []):
            _ident(edge["type"], "relationship type")

        #: source_type -> the action rules it triggers. Built once; an event
        #: can fire several (a SHOT produces TOOK and FROM_ZONE).
        self._by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for rule in self.actions:
            for source in _as_list(rule.get("source_type", [])):
                self._by_source[source].append(rule)
        self._reified_by_source = {
            spec["source_type"]: (label, spec)
            for label, spec in self.reified.items()
            if "source_type" in spec
        }

    # ----------------------------------------------------------- constraints

    def ensure_constraints(self) -> None:
        """Uniqueness constraint on every node key.

        Not optional. MERGE without a supporting index degrades to a label
        scan, and a 1,300-event match becomes minutes rather than seconds.
        """
        for label, key in self.keys.items():
            self.session.run(
                f"CREATE CONSTRAINT {label.lower()}_{key} IF NOT EXISTS "
                f"FOR (n:{label}) REQUIRE n.{key} IS UNIQUE"
            )

    # ------------------------------------------------------------ the pitch

    def load_pitch(self) -> None:
        """Write the 18 Zone nodes and their adjacency. Static, once per graph."""
        zones = all_zones()
        for zone in zones:
            self._queue_node("Zone", "zone_id", zone["zone_id"], dict(zone))
        for a in zones:
            for b in zones:
                if adjacent(a["zone_id"], b["zone_id"]):
                    self._queue_edge(
                        "ADJACENT_TO", "Zone", "zone_id", a["zone_id"],
                        "Zone", "zone_id", b["zone_id"], {},
                    )
        self.flush()

    # ---------------------------------------------------------------- intake

    def load_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        record = json.loads(line)
        if record.get("type") == "MATCH_META":
            self.load_meta(record)
        else:
            self.load_event(record)

    def load_event(self, event: dict[str, Any]) -> None:
        source = event.get("type")

        reified = self._reified_by_source.get(source)
        if reified:
            label, spec = reified
            self._queue_node(
                label, spec["key"], event[spec["key"]],
                _present(event, spec.get("properties", [])),
            )

        for rule in self._by_source.get(source, []):
            src, dst = rule["from"], rule["to"]
            a, b = event.get(src["key"]), event.get(dst["key"])
            if a is None or b is None:
                continue
            props = _present(event, list(SPINE) + list(rule.get("properties", [])))
            self._queue_edge(
                rule["type"], src["node"], self.keys[src["node"]], a,
                dst["node"], self.keys[dst["node"]], b, props,
                temporal=True,
            )

        self.counts["events"] += 1
        self.counts[f"source:{source}"] += 1
        self._maybe_flush()

    def load_meta(self, meta: dict[str, Any]) -> None:
        """Expand the nested metadata record into the entity layer."""
        match_id = meta["match_id"]
        prediction = meta.get("prediction") or {}

        self._queue_node("Competition", "competition_id", meta["competition"], {
            "name": meta["competition"], "season": meta.get("season"),
        })
        self._queue_node("Match", "match_id", match_id, {
            "matchweek": meta.get("matchweek"),
            "kickoff_utc": meta.get("kickoff_utc"),
            "period_length_s": meta.get("period_length_s"),
            "pre_game_win_prob_home": prediction.get("win_prob_home"),
            "pre_game_draw_prob": prediction.get("draw_prob"),
            "pre_game_win_prob_away": prediction.get("win_prob_away"),
            "pre_game_xg_home": prediction.get("expected_goals_home"),
            "pre_game_xg_away": prediction.get("expected_goals_away"),
            "prediction_model": prediction.get("model"),
        })
        self._queue_edge("IN_COMPETITION", "Match", "match_id", match_id,
                         "Competition", "competition_id", meta["competition"], {})

        stadium = meta.get("stadium") or {}
        if stadium:
            self._queue_node("Stadium", "stadium_id", stadium["stadium_id"], dict(stadium))
            self._queue_edge("PLAYED_AT", "Match", "match_id", match_id,
                             "Stadium", "stadium_id", stadium["stadium_id"], {})

        for appointment in meta.get("officials", []):
            official = appointment["official"]
            self._queue_node("Referee", "referee_id", official["referee_id"],
                             {"name": official["name"]})
            self._queue_edge("OFFICIATED_BY", "Match", "match_id", match_id,
                             "Referee", "referee_id", official["referee_id"],
                             {"role": appointment["role"]})
            self.counts["officials"] += 1

        for side in ("home", "away"):
            team = meta.get(side)
            if not team:
                continue
            self._queue_node("Team", "team_id", team["team_id"], {
                "name": team.get("name"), "short_name": team.get("short_name"),
            })
            self._queue_edge(
                "HOME_TEAM" if side == "home" else "AWAY_TEAM",
                "Match", "match_id", match_id, "Team", "team_id", team["team_id"],
                {"formation": team.get("formation"), "style": team.get("style")},
            )
            self.counts["teams"] += 1

            for order, player in enumerate(team.get("players", [])):
                pid = player["player_id"]
                self._queue_node("Player", "player_id", pid, {"name": player.get("name")})
                self._queue_edge("PLAYS_FOR", "Player", "player_id", pid,
                                 "Team", "team_id", team["team_id"], {})
                self._queue_edge("PLAYED_IN", "Player", "player_id", pid,
                                 "Match", "match_id", match_id, {
                                     "shirt_number": player.get("shirt_number"),
                                     "position": player.get("position"),
                                     "starter": order < 11,
                                 })
                self.counts["players"] += 1

        self._maybe_flush()

    # ---------------------------------------------------------------- queues

    def _queue_node(self, label: str, key: str, value: Any, props: dict[str, Any]) -> None:
        clean = {k: v for k, v in props.items() if v is not None and k != key}
        bucket = self._node_rows[f"{label}|{key}"]
        row = bucket.get(value)
        if row is None:
            bucket[value] = {"key": value, "props": clean}
            self._pending += 1
        else:
            row["props"].update(clean)

    def _queue_edge(
        self, rel: str, from_label: str, from_key: str, a: Any,
        to_label: str, to_key: str, b: Any, props: dict[str, Any],
        temporal: bool = False,
    ) -> None:
        """Queue one relationship.

        ``temporal`` decides what identifies it, and the two cases are
        genuinely different:

        * An **action** is identified by its endpoints *and its moment*. Two
          players pass to each other many times in a match and each pass is
          its own relationship with its own evidence value, so clock_s and
          sequence join the merge key.
        * A **structural** edge is identified by its endpoints alone. There
          is one ADJACENT_TO between two zones, one PLAYS_FOR between a
          player and their club. These carry no timestamp, and Neo4j refuses
          to merge on a null property - so they must not be keyed on one.
        """
        clean = {k: v for k, v in props.items() if v is not None}
        keyed = temporal and clean.get("clock_s") is not None

        signature = f"{rel}|{from_label}|{from_key}|{to_label}|{to_key}|{int(keyed)}"
        self._edge_spec[signature] = {
            "type": rel, "from_label": from_label, "from_key": from_key,
            "to_label": to_label, "to_key": to_key, "keyed": keyed,
        }

        ident = (a, b, clean.get("clock_s"), clean.get("sequence")) if keyed else (a, b)
        bucket = self._edge_rows[signature]
        row = bucket.get(ident)
        if row is None:
            bucket[ident] = {"a": a, "b": b, "props": clean}
            self._pending += 1
        else:
            row["props"].update(clean)

    def _maybe_flush(self) -> None:
        if self._pending >= self.batch_size:
            self.flush()

    # ----------------------------------------------------------------- write

    def flush(self) -> None:
        """Nodes before edges, always: an edge MATCHes both endpoints."""
        for composite, bucket in self._node_rows.items():
            label, key = composite.split("|")
            rows = list(bucket.values())
            self.session.run(
                f"UNWIND $rows AS row "
                f"MERGE (n:{label} {{{key}: row.key}}) "
                f"SET n += row.props",
                rows=rows,
            )
            self.counts[f"node:{label}"] += len(rows)

        for signature, bucket in self._edge_rows.items():
            spec = self._edge_spec[signature]
            rows = list(bucket.values())
            merge = (
                f"MERGE (a)-[r:{spec['type']} {{clock_s: row.props.clock_s, "
                f"sequence: row.props.sequence}}]->(b)"
                if spec["keyed"]
                else f"MERGE (a)-[r:{spec['type']}]->(b)"
            )
            self.session.run(
                f"UNWIND $rows AS row "
                f"MATCH (a:{spec['from_label']} {{{spec['from_key']}: row.a}}) "
                f"MATCH (b:{spec['to_label']} {{{spec['to_key']}: row.b}}) "
                f"{merge} "
                f"SET r += row.props",
                rows=rows,
            )
            self.counts[f"edge:{spec['type']}"] += len(rows)

        self._node_rows.clear()
        self._edge_rows.clear()
        self._pending = 0

    def load_stream(self, lines: Iterable[str]) -> dict[str, int]:
        for line in lines:
            self.load_line(line)
        self.flush()
        return dict(self.counts)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _present(record: dict[str, Any], names: Iterable[str]) -> dict[str, Any]:
    return {n: record[n] for n in names if record.get(n) is not None}


def load_mapping(path: Path | str = DEFAULT_MAPPING) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


class _DryRunSession:
    """Prints statements instead of running them. No database needed."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def run(self, query: str, **params: Any) -> None:
        self.statements.append((query, params))


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Load events into the match graph.")
    parser.add_argument("--mapping", default=str(DEFAULT_MAPPING))
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--dry-run", action="store_true",
                        help="build the statements without connecting to Neo4j")
    args = parser.parse_args(argv)

    mapping = load_mapping(args.mapping)

    if args.dry_run:
        loader = OntologyLoader(mapping, _DryRunSession(), args.batch_size)
        loader.load_pitch()
        counts = loader.load_stream(sys.stdin)
        for name in sorted(counts):
            print(f"  {name:<28} {counts[name]:>6}", file=sys.stderr)
        return 0

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
    try:
        with driver.session() as session:
            loader = OntologyLoader(mapping, session, args.batch_size)
            loader.ensure_constraints()
            loader.load_pitch()
            counts = loader.load_stream(sys.stdin)
        for name in sorted(counts):
            print(f"  {name:<28} {counts[name]:>6}", file=sys.stderr)
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
