"""Write every insight for a match once, so replays are free and instant.

Writing the sentence is the only expensive step in the pipeline. Through the
Copilot CLI it costs roughly seventeen seconds and a third of a credit per
call, because the CLI is a coding agent rather than a text generator. Twenty
insights is six minutes and seven credits - fine once, painful on every
rehearsal.

So: run this once per match. It sweeps the gate, writes each sentence, and
persists it to the graph wired to the evidence behind it. From then on the
UI reads those sentences instead of paying for them.

    python -m chalkboard.agents.precompute --match CBM-49861

Then set MODEL_ENABLED=false and the demo plays instantly from the graph -
with the real prose, not the plain summary, because the prose is in the
graph now.

Be straight about this in the README and the video: the sentences were
written ahead of time, not during playback. The pipeline is identical
either way, but claiming live inference when it is cached would be
misleading.
"""

from __future__ import annotations

import os
import sys
import time

from ..graph.insights import InsightStore
from ..graph.significance import SignificanceGate
from ..mcp.tools import GraphTools
from .explainer import Explainer
from .llm import get_client
from .personaliser import Personaliser  # noqa: F401 - imported for parity


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--match", required=True)
    parser.add_argument("--until", type=float, default=5400.0)
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument(
        "--limit", type=int, default=0,
        help="stop after this many insights - useful for a cheap first test",
    )
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

    # Pre-computing with the model switched off would write plain summaries
    # and cache nothing useful, which is a confusing way to waste a run.
    if (os.environ.get("MODEL_ENABLED") or "true").lower() in ("false", "0", "no", "off"):
        print("MODEL_ENABLED is off - nothing would be written. Set it to true.",
              file=sys.stderr)
        return 1

    driver = GraphDatabase.driver(uri, auth=(os.environ.get("NEO4J_USER", "neo4j"), password))
    written = skipped = 0
    started = time.monotonic()

    try:
        with driver.session() as session:
            store = InsightStore(session)
            explainer = Explainer(get_client(), GraphTools(session), store=store)
            gate = SignificanceGate(session, threshold=args.threshold)

            print(f"sweeping {args.match} - each sentence costs a model call",
                  file=sys.stderr)

            for moment in gate.sweep(args.match, args.until):
                insight = explainer.explain(moment)
                minute = int(moment.clock_s // 60)
                if insight.source == "stored":
                    skipped += 1
                    print(f"  {minute:>3}'  cached   {insight.sentence[:70]}")
                    continue
                written += 1
                tag = "written " if insight.source == "model" else "GRAPH   "
                print(f"  {minute:>3}'  {tag} {insight.sentence[:70]}")
                if insight.rejected:
                    print(f"        rejected: {insight.rejected[:90]}", file=sys.stderr)
                if args.limit and written >= args.limit:
                    print(f"  stopping at --limit {args.limit}", file=sys.stderr)
                    break
    finally:
        driver.close()

    elapsed = time.monotonic() - started
    print(f"\n{written} written, {skipped} already cached, {elapsed:.0f}s",
          file=sys.stderr)
    if written:
        print("Now set MODEL_ENABLED=false - the demo will replay these instantly.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
