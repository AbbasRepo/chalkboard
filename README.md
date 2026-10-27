# Chalkboard

**The tactics board that answers back.**

Chalkboard turns a live stream of synthetic football events into explainable
match intelligence, personalised for whoever's watching.

Two specialist agents work over a shared live match graph. A configurable
match simulator produces the football-realistic event data they reason about —
no real match data is used anywhere in this project, and all clubs, venues and
players are invented.

**The principle: the graph reasons, the model narrates.** Significance is
computed by a Cypher query over the live match graph, not guessed by a model.
The agents only narrate what the graph has already proven.

## Stack

| | |
| --- | --- |
| Orchestration | Microsoft Agent Framework |
| Inference | GitHub Copilot CLI |
| Agent tools | MCP server over the match graph |
| Development | GitHub Copilot Agent Mode, Claude Code |
| CI | GitHub Actions |
| Graph | Neo4j AuraDB Free |

Azure deployment is specified in `infra/main.bicep`. It is validated but
**not deployed** — the project runs locally.

---

**New to the project?** Read [docs/RUNBOOK.md](docs/RUNBOOK.md) — setup,
architecture, a worked example, the decisions log and troubleshooting.

---

## Status

| Work package | Scope | State |
| --- | --- | --- |
| WP1 | Synthetic event schema and match simulator | **Complete** |
| WP2 | Ontology loader, derived edges, significance gate (Neo4j Aura) | **Complete** |
| WP3 | MCP server, model client, Explainer and Personaliser agents | Scaffolded |
| WP4 | FastAPI and the two-pane UI | Scaffolded |
| WP5 | README, pitch, demo video | Not started |

---

## Architecture & Graph Internals

```
Synthetic generator
        │  JSON Lines, one event at a time
        ▼
  Ontology loader ──────────────► Neo4j
        │  reads ontology/match.yaml      (the match knowledge graph)
        │  no model involved                      │
        ▼                                         │
  Derived edges  ─────────────────────────────────┤
        │  LINKS_WITH, CIRCULATES_THROUGH,        │
        │  PRESSES_IN, NEMESIS_OF                 │
        ▼                                         │
  SIGNIFICANCE GATE ◄─────────────────────────────┘
        │  1. ESTIMATE_THRESHOLD (once on gate init, across all loaded matches)
        │  2. SCORE_WINDOW (every 3s over a 90s sliding window)
        │  ~1,300 actions in, ~25–35 moments out
        │
        │  moment + evidence
        ▼
   Explainer agent ──► MCP graph tools (read-only lookups)
        │          └─► Copilot CLI (one sentence)
        │          └─► grounding check ◄── rejects anything unsupported
        ▼
  Personaliser agent
        │  analyst rendering + supporter rendering
        ▼
   FastAPI (SSE) ──► two-pane UI + chalk board
```

### The Edge-Centric Graph Model (`ontology/match.yaml`)

Arity decides shape:
- **Two-participant actions** (`PASSED_TO`, `CARRIED_INTO`, `DISPOSSESSED`, `PRESSED_IN`) are **relationships** carrying the temporal spine (`event_id`, `clock_s`, `period`, `match_id`, `possession_id`, `sequence`, `outcome`).
- **Multi-participant actions** (`Shot`, `Foul`) and system outputs (`Insight`) are **nodes**.
- **Derived aggregate edges** (`LINKS_WITH`, `CIRCULATES_THROUGH`, `PRESSES_IN` per match; `NEMESIS_OF` across all matches) are materialised by `ontology/derived.cypher`.

### The Significance Gate & Dynamic Threshold Extraction (`ontology/significance.cypher`)

Roughly 1,300 actions arrive in a match and perhaps thirty deserve comment. Rather than asking a language model whether a passage mattered—or relying on a brittle static magic number—the gate uses two deterministic Cypher queries in `ontology/significance.cypher`:

#### 1. `ESTIMATE_THRESHOLD` — Global Statistical Parameter Extraction

When `SignificanceGate` initialises (and `SIGNIFICANCE_THRESHOLD` is set to `auto` or left unset), it runs a read-only Cypher query across all loaded matches in the graph to extract baseline event rates using the exact weights applied during window scoring:
- **Forced turnovers** (`DISPOSSESSED` with `pressure > 0.6`): weight `0.8`
- **Line-breaking passes** (`PASSED_TO` with `pass_progressive`, `pass_difficulty > 0.6`, and `outcome = 'SUCCESS'`): weight `0.5`
- **Shot quality** (`Shot.shot_xg`): weight `3.0 × shot_xg`

```cypher
// --name: ESTIMATE_THRESHOLD
MATCH (m:Match)
WITH count(m) AS matches,
     sum(coalesce(m.period_length_s, 2700.0)) AS total_clock_s
OPTIONAL MATCH (:Player)-[r]->()
WHERE type(r) IN ['PASSED_TO', 'CARRIED_INTO', 'DISPOSSESSED', 'PRESSED_IN']
WITH matches, total_clock_s,
     count(r) AS action_count,
     sum(CASE WHEN type(r) = 'DISPOSSESSED' AND coalesce(r.pressure, 0) > 0.6
              THEN 1 ELSE 0 END) AS forced_turnovers,
     sum(CASE WHEN type(r) = 'PASSED_TO' AND r.pass_progressive
                   AND coalesce(r.pass_difficulty, 0) > 0.6
                   AND r.outcome = 'SUCCESS'
              THEN 1 ELSE 0 END) AS breaking_passes
OPTIONAL MATCH (s:Shot)
WITH matches, total_clock_s, action_count, forced_turnovers, breaking_passes,
     count(s) AS shot_count,
     coalesce(sum(s.shot_xg), 0.0) AS total_xg
WITH matches, total_clock_s, action_count, forced_turnovers, breaking_passes,
     shot_count, total_xg,
     (forced_turnovers * 0.8 + breaking_passes * 0.5 + total_xg * 3.0) AS total_score_mass
WITH matches, action_count, forced_turnovers, breaking_passes, shot_count,
     round(1000.0 * total_xg) / 1000.0 AS total_xg,
     CASE WHEN total_clock_s > 0
          THEN total_score_mass * ($window / total_clock_s)
          ELSE 0.0 END AS expected_window_score
RETURN CASE WHEN expected_window_score > 0
            THEN round(1000.0 * expected_window_score * $multiplier) / 1000.0
            ELSE $defaultThreshold END AS threshold,
       round(1000.0 * expected_window_score) / 1000.0 AS expected_window_score,
       matches, action_count, forced_turnovers, breaking_passes, shot_count, total_xg;
```

Scaling `total_score_mass` by `$window / total_clock_s` yields the expected score of a 90-second window across the corpus, which is multiplied by `$multiplier` (`SIGNIFICANCE_MULTIPLIER`, default `1.5`) or falls back to `$defaultThreshold` (`1.8`) on an empty graph. Setting a numeric `SIGNIFICANCE_THRESHOLD` in `.env` (or passing `--threshold` on the CLI) overrides the dynamic calculation with a fixed threshold.

#### 2. `SCORE_WINDOW` — Sliding Window Evaluation

Every 3 match seconds (`TICK_S`), the gate scores the trailing 90-second window (`WINDOW_S`) for `$matchId`, unioning action relationships and `Shot` nodes. If `score > $threshold`, the gate emits a `Moment` containing the top `$maxEvidence` contributing actions and enters a 75-second cooldown (`COOLDOWN_S`) so the same passage is not narrated repeatedly.

```bash
# Load a match, compute derived edges, and sweep the significance gate
python -m chalkboard.graph.loader < data/sample_CBM-00042.jsonl
python -m chalkboard.graph.derive --all
python -m chalkboard.graph.significance --match CBM-00042
```

---

## WP1 — the synthetic match generator

The hackathon requires synthetic, football-realistic data, and rewards the
quality of that data directly. The generator is therefore a first-class part of
the project rather than a fixture loader.

### Model

Possession is modelled as a Markov chain. Each possession belongs to one team
and advances through actions — pass, carry, cross, shot — until it ends in a
turnover or a shot. Action selection and outcome probability are conditioned on
pitch position, the style profile of the team in possession, the press profile
of the team out of possession, and per-player skill weights.

Four tactical style profiles ship by default:

| Style | Behaviour |
| --- | --- |
| `possession` | Patient build-up, short passing, waits for a good chance |
| `high_press` | Aggressive press high up the pitch, forces turnovers in dangerous areas |
| `low_block` | Concedes territory, counter-attacks, fewest but highest-quality chances |
| `direct` | Long balls and wide play, shoots often from poorer positions |

Style is the single most important input to data quality. A match where both
sides play identically gives the downstream agents nothing to explain.

### Calibration

Measured over 80 generated matches:

| Metric | Chalkboard | Professional football |
| --- | --- | --- |
| Goals per match | 2.91 | ~2.8 |
| Shots per match | 28.7 | ~25 |
| xG per shot | 0.11 | ~0.10 |
| Pass accuracy | 79% | ~80% |
| Events per match | ~1,280 | ~1,500 |

Shot quality responds correctly to style: the `direct` profile takes the most
shots at the lowest average xG, the `low_block` profile the fewest at the
highest. That relationship is asserted in the test suite, because it is the
property that gives the Explainer agent something true to say.

The xG model is a logistic over distance to goal and shooting angle. It is not
calibrated against real data — it cannot be, since the data is synthetic — but
it behaves correctly: close and central is good, wide and far is poor, and
pressure degrades both.

### Event schema

Every event carries the metrics the brief calls for, computed at generation
time rather than inferred downstream:

- **Pass quality** — `pass_distance_m`, `pass_angle_deg`, `pass_progressive`,
  `pass_difficulty` (0-1), plus success or failure as the outcome
- **Speed** — `ball_speed_ms`, `shot_speed_ms`
- **Pressure** — `pressure` (0-1), `opponents_within_5m`, and discrete
  `PRESSURE` events when a press is triggered
- **Shots** — `shot_xg`, `body_part`, location
- **Player identification** — every event names the actor and, where relevant,
  the intended receiver or the opponent involved

Coordinates are normalised to a 0-100 grid and always expressed from the
perspective of the team in possession, which removes end-swapping from every
downstream consumer. Running score is stamped on every event, so any single
event is self-sufficient.

### Usage

```bash
# one match to JSON Lines
python -m chalkboard.generator.cli --seed 42 generate --out data/CBM-00042.jsonl

# a corpus for graph loading and evaluation fixtures
python -m chalkboard.generator.cli generate --matches 20 --out-dir data/

# force a tactical matchup
python -m chalkboard.generator.cli \
    --home-style high_press --away-style low_block \
    generate --out data/press_v_block.jsonl

# replay at 60x real time: a 90 minute match in 90 seconds
python -m chalkboard.generator.cli --seed 42 stream --speed 60
```

A seed reproduces a match exactly, including kickoff timestamps. That is what
makes evaluation fixtures and demo replays trustworthy.

### Tests

```bash
PYTHONPATH=src python -m pytest tests/ -q
```

The realism tests are loose-bounded on purpose. They are not asserting that the
simulator reproduces any particular league; they guard against a regression
that silently turns the data into noise and destroys every downstream agent's
ability to say anything sensible.

---

## Data licensing

All data produced by this repository is synthetic. Club names, venues and
player names are invented and any resemblance to real teams or people is
coincidental. No real match data, footage or third-party trademarks are used.
