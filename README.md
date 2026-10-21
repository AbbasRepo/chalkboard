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
| WP2 | Ontology loader, derived edges, significance gate (Neo4j Aura) | Scaffolded |
| WP3 | MCP server, model client, Explainer and Personaliser agents | Scaffolded |
| WP4 | FastAPI and the two-pane UI | Scaffolded |
| WP5 | README, pitch, demo video | Not started |

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
