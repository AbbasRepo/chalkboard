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
import math
import random
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Appointment, MatchMeta, Official, Prediction, Stadium
from .simulator import MatchSimulator
from .teams import FORMATIONS, OFFICIALS, STADIA, STYLES, build_team


#: Fixed epoch for generated fixtures. Deriving kickoff from the seed rather
#: than the wall clock means a seed reproduces a match byte for byte, which is
#: what makes evaluation fixtures and demo replays trustworthy.
SEASON_START = datetime(2026, 8, 8, 15, 0, tzinfo=timezone.utc)


ROLES = ("referee", "assistant_1", "assistant_2", "var")


def _predict(home, away) -> Prediction:
    """A pre-game forecast, stored so the narrative can argue with the result.

    Deliberately simple and honest: team strength is the mean of the starting
    eleven's attacking and defending weights, nudged by style. Goals are then
    Poisson. It is not fitted to anything - it cannot be, the league does not
    exist - but it is explainable, which matters more here than accuracy.
    """
    def strength(team):
        starters = team.starters()
        attack = sum(p.finishing + p.passing for p in starters) / (2 * len(starters))
        defend = sum(p.defending for p in starters) / len(starters)
        tempo = STYLES[team.style].shot_appetite
        return attack * (0.85 + 0.3 * tempo), defend

    ha, hd = strength(home)
    aa, ad = strength(away)
    xg_home = round(max(0.3, 1.45 * (ha / max(ad, 0.1)) ** 0.9), 2)
    xg_away = round(max(0.3, 1.15 * (aa / max(hd, 0.1)) ** 0.9), 2)

    # Poisson scoreline matrix, truncated at 8 goals a side.
    def pois(k, lam):
        return math.exp(-lam) * lam**k / math.factorial(k)

    home_w = draw = away_w = 0.0
    for h in range(9):
        for a in range(9):
            p = pois(h, xg_home) * pois(a, xg_away)
            if h > a:
                home_w += p
            elif h == a:
                draw += p
            else:
                away_w += p
    total = home_w + draw + away_w
    return Prediction(
        model="poisson-squad-strength",
        win_prob_home=round(home_w / total, 3),
        draw_prob=round(draw / total, 3),
        win_prob_away=round(away_w / total, 3),
        expected_goals_home=xg_home,
        expected_goals_away=xg_away,
    )


def build_match(
    seed: int,
    home_style: str | None = None,
    away_style: str | None = None,
    period_length_s: int = 2700,
    kickoff_utc: str | None = None,
    matchweek: int = 1,
) -> MatchMeta:
    rng = random.Random(seed)
    styles = list(STYLES)
    formations = list(FORMATIONS)

    home_idx = rng.randrange(8)
    away_idx = (home_idx + 1 + rng.randrange(7)) % 8

    home = build_team(rng, home_idx, home_style or rng.choice(styles), rng.choice(formations))
    away = build_team(rng, away_idx, away_style or rng.choice(styles), rng.choice(formations))

    stadium_name, capacity = STADIA[home_idx % len(STADIA)]
    stadium = Stadium(
        stadium_id=f"ST-{home_idx + 1:02d}",
        name=stadium_name,
        capacity=capacity,
    )

    chosen = rng.sample(range(len(OFFICIALS)), 4)
    officials = tuple(
        Appointment(
            official=Official(referee_id=f"REF-{i + 1:02d}", name=OFFICIALS[i]),
            role=role,
        )
        for i, role in zip(chosen, ROLES)
    )

    return MatchMeta(
        match_id=f"CBM-{seed:05d}",
        competition="Chalkboard Invitational",
        season="2026/27",
        matchweek=matchweek,
        stadium=stadium,
        officials=officials,
        prediction=_predict(home, away),
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
            meta = build_match(
                args.seed + offset, args.home_style, args.away_style,
                matchweek=offset + 1,
            )
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
