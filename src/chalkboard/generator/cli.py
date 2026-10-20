"""Command line entry point for the Chalkboard synthetic match generator.

Two modes:

``generate``
    Write a complete match to JSON Lines. Use this to build a corpus for graph
    loading, evaluation fixtures and offline agent development.

``stream``
    Replay a match at a configurable multiple of real time, writing one event
    per line to stdout or to an Event Hub. This is what the demo runs against,
    because the brief asks for something responsive enough for live play.

Examples
--------
    python -m chalkboard.generator.cli generate --out data/match_001.jsonl
    python -m chalkboard.generator.cli generate --matches 20 --out-dir data/
    python -m chalkboard.generator.cli stream --speed 60 --home-style high_press
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import MatchMeta
from .simulator import MatchSimulator
from .teams import FORMATIONS, STYLES, build_team, venue_for


#: Fixed epoch for generated fixtures. Deriving kickoff from the seed rather
#: than the wall clock means a seed reproduces a match byte for byte, which is
#: what makes evaluation fixtures and demo replays trustworthy.
SEASON_START = datetime(2026, 8, 8, 15, 0, tzinfo=timezone.utc)


def build_match(
    seed: int,
    home_style: str | None = None,
    away_style: str | None = None,
    period_length_s: int = 2700,
    kickoff_utc: str | None = None,
) -> MatchMeta:
    rng = random.Random(seed)
    styles = list(STYLES)
    formations = list(FORMATIONS)

    home_idx = rng.randrange(8)
    away_idx = (home_idx + 1 + rng.randrange(7)) % 8

    home = build_team(rng, home_idx, home_style or rng.choice(styles), rng.choice(formations))
    away = build_team(rng, away_idx, away_style or rng.choice(styles), rng.choice(formations))

    return MatchMeta(
        match_id=f"CBM-{seed:05d}",
        competition="Chalkboard Invitational",
        venue=venue_for(home_idx),
        kickoff_utc=kickoff_utc
        or (SEASON_START + timedelta(days=7 * (seed % 38)))
        .isoformat()
        .replace("+00:00", "Z"),
        home=home,
        away=away,
        period_length_s=period_length_s,
        seed=seed,
    )


def _write_match(meta: MatchMeta, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    simulator = MatchSimulator(meta, seed=meta.seed)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "MATCH_META", **meta.to_dict()}, separators=(",", ":")) + "\n")
        for event in simulator.run():
            handle.write(event.to_json() + "\n")
            count += 1
    return count


def cmd_generate(args: argparse.Namespace) -> int:
    if args.matches > 1:
        out_dir = Path(args.out_dir or "data")
        total = 0
        for offset in range(args.matches):
            meta = build_match(args.seed + offset, args.home_style, args.away_style)
            written = _write_match(meta, out_dir / f"{meta.match_id}.jsonl")
            total += written
            print(
                f"{meta.match_id}  {meta.home.name} ({meta.home.style}) v "
                f"{meta.away.name} ({meta.away.style})  {written} events",
                file=sys.stderr,
            )
        print(f"{args.matches} matches, {total} events -> {out_dir}", file=sys.stderr)
        return 0

    meta = build_match(args.seed, args.home_style, args.away_style)
    path = Path(args.out or f"data/{meta.match_id}.jsonl")
    written = _write_match(meta, path)
    print(f"{written} events -> {path}", file=sys.stderr)
    return 0


def cmd_stream(args: argparse.Namespace) -> int:
    meta = build_match(args.seed, args.home_style, args.away_style)
    simulator = MatchSimulator(meta, seed=meta.seed)

    print(json.dumps({"type": "MATCH_META", **meta.to_dict()}, separators=(",", ":")), flush=True)
    previous = 0.0
    started = time.monotonic()

    for event in simulator.run():
        elapsed_match = (event.period - 1) * meta.period_length_s + event.clock_s
        target = started + elapsed_match / max(args.speed, 0.01)
        delay = target - time.monotonic()
        if delay > 0:
            time.sleep(min(delay, 2.0))
        print(event.to_json(), flush=True)
        previous = elapsed_match

    print(
        f"full time {meta.home.short_name} {simulator.score[meta.home.team_id]}"
        f"-{simulator.score[meta.away.team_id]} {meta.away.short_name}",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chalkboard-generator", description=__doc__)
    parser.add_argument("--seed", type=int, default=random.randrange(1, 99999))
    parser.add_argument("--home-style", choices=sorted(STYLES), default=None)
    parser.add_argument("--away-style", choices=sorted(STYLES), default=None)

    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="write a match (or many) to JSONL")
    gen.add_argument("--out", help="output file for a single match")
    gen.add_argument("--out-dir", help="output directory when --matches > 1")
    gen.add_argument("--matches", type=int, default=1)
    gen.set_defaults(func=cmd_generate)

    stream = sub.add_parser("stream", help="replay a match at speed to stdout")
    stream.add_argument(
        "--speed",
        type=float,
        default=60.0,
        help="match seconds per wall-clock second (60 = a 90 minute match in 90 seconds)",
    )
    stream.set_defaults(func=cmd_stream)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
