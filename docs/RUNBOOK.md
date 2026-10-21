# Chalkboard — complete guide

**The tactics board that answers back.**

Explainable football match intelligence from synthetic events, personalised
per viewer. Built for the Microsoft × Premier League *Inside the Game*
hackathon, October 2026.

This document is everything: why the project is shaped the way it is, how to
set it up, how it works end to end, how to test it, and how it maps to the
judging criteria. It is written for someone joining the project cold.

---

## Contents

1. [The idea in one page](#1-the-idea-in-one-page)
2. [Prerequisites and setup](#2-prerequisites-and-setup)
3. [Products and technologies used](#3-products-and-technologies-used)
4. [Architecture](#4-architecture)
5. [The data model](#5-the-data-model)
6. [How it works — a worked example](#6-how-it-works--a-worked-example)
7. [The user interface](#7-the-user-interface)
8. [Key code, explained](#8-key-code-explained)
9. [Key features](#9-key-features)
10. [How it meets the judging criteria](#10-how-it-meets-the-judging-criteria)
11. [Testing](#11-testing)
12. [File structure](#12-file-structure)
13. [Decisions and considerations log](#13-decisions-and-considerations-log)
14. [Open items](#14-open-items)
15. [Appendix A — command reference](#appendix-a--command-reference)
16. [Appendix B — troubleshooting](#appendix-b--troubleshooting)
17. [Appendix C — references](#appendix-c--references)

---

## 1. The idea in one page

### The problem

Match coverage reports **what happened**, to one undifferentiated audience.
Three gaps follow:

- **Explanation does not scale.** A commentator can explain why a handful of
  moments mattered, at the pace a human can think. Most of a match goes
  unexplained.
- **Every viewer gets the same feed.** An analyst who wants press intensity
  and a supporter following one player receive identical output, in one
  language.
- **Statistics without significance are noise.** Coverage already shows
  possession, xG and pass counts. None of it says which number mattered.

### The solution

Chalkboard watches a stream of football events and explains the moments that
matter, differently for each viewer.

### The one principle

> **The graph reasons. The model narrates.**

- Significance is computed by a **Cypher query** over a live match graph,
  not guessed by a language model.
- The agents only narrate what the graph has already proven.
- Every sentence is checked against its evidence before anyone sees it.

That separation is the project's whole claim. It makes output traceable, it
costs almost nothing per event, and a judge can verify it on screen in ten
seconds.

### What it produces

For one moment, two viewers get genuinely different experiences:

| Viewer | Output |
| --- | --- |
| Analyst | "Two line-breaking passes carried play into the left final third — third time in eight minutes." |
| Supporter following #6 | "Your man just broke their press again." |
| Supporter following #11 | "Nothing for Lindqvist here — play has moved to the other side." |

Alongside them, the chalk board draws the exact actions those sentences came
from.

---

## 2. Prerequisites and setup

Nothing here costs money. No Azure subscription is required.

### 2.1 Install

| # | What | Where from | Why |
| --- | --- | --- | --- |
| 1 | Python 3.12+ | [python.org/downloads](https://www.python.org/downloads/) | Everything is Python. On Windows tick **Add python.exe to PATH**. |
| 2 | Git | [git-scm.com/downloads](https://git-scm.com/downloads) | Repository |
| 3 | Visual Studio Code | [code.visualstudio.com](https://code.visualstudio.com/) | Editor, and host for Copilot Agent Mode |
| 4 | GitHub Copilot extension | VS Code Extensions, search `@id:GitHub.copilot` | Hero technology; free tier is enough |
| 5 | Node.js LTS | [nodejs.org](https://nodejs.org/) | Needed only to install the Copilot CLI |
| 6 | GitHub Copilot CLI | `npm install -g @github/copilot` | Inference |
| 7 | Neo4j AuraDB Free account | [neo4j.com/cloud/aura](https://neo4j.com/cloud/aura/) | The graph. Free tier, no card. |

**Not needed:** Docker, an Azure subscription, a React toolchain, a local
database server.

### 2.2 Accounts and configuration

**Neo4j AuraDB Free**

1. Sign up and create an instance on the **AuraDB Free** tier.
2. A password is shown **once** when it provisions. Download the credentials
   file or copy the password immediately — it can only be reset afterwards.
3. Note the connection URI: `neo4j+s://xxxxxxxx.databases.neo4j.io`.
4. Open the instance's **Query** tab and run `RETURN 1` to confirm it is alive.
5. Free instances **pause after a few days idle**. Resume from the console
   before any session — this is the single most common "nothing works"
   cause.

**GitHub Copilot CLI**

1. `npm install -g @github/copilot`
2. Run `copilot` once and use `/login` to sign in.
3. Verify: `copilot -p "say hello" --allow-all-tools`

### 2.3 The project

```bash
git clone https://github.com/AbbasRepo/chalkboard
cd chalkboard

python -m venv .venv
# Windows:        .venv\Scripts\activate
# macOS or Linux: source .venv/bin/activate

pip install -e ".[dev]"
python -m pytest tests/ -q          # expect 130 passed

cp .env.example .env                # Windows: copy .env.example .env
```

### 2.4 Environment variables

`.env` is gitignored and must never be committed.

| Variable | Example | Notes |
| --- | --- | --- |
| `NEO4J_URI` | `neo4j+s://ab12cd34.databases.neo4j.io` | From the Aura instance card |
| `NEO4J_USER` | `neo4j` | Always literally `neo4j` on Aura Free |
| `NEO4J_PASSWORD` | | Shown once at creation |
| `COPILOT_CMD` | `copilot` | **Windows: `copilot.cmd`** — npm installs a `.cmd` shim that `subprocess` cannot launch by the bare name |
| `COPILOT_ARGS` | `-p\|{prompt}\|--allow-all-tools` | `--allow-all-tools` prevents it stopping to ask permission |
| `COPILOT_STDIN` | `true` | **Windows:** pipes the prompt instead of passing it as an argument, because `cmd.exe` mangles embedded newlines |
| `COPILOT_ISOLATED` | `true` | Runs the CLI from an empty directory so it does not inspect the repo before answering |
| `COPILOT_TIMEOUT_S` | `60` | Generous for pre-compute; lower to ~8 for live use |
| `SIGNIFICANCE_THRESHOLD` | `0.5` | Tune until 20–35 moments surface per match |
| `MODEL_ENABLED` | `true` | The kill switch |

**TLS note.** If your network performs TLS inspection (common on corporate
machines), `neo4j+s://` fails with a self-signed-certificate error. Using
`neo4j+ssc://` skips certificate verification as a workaround. It keeps
encryption but drops the check that you are talking to Aura — acceptable
locally, not something to ship.

---

## 3. Products and technologies used

### 3.1 Hackathon hero technologies

The brief names these: Microsoft Foundry, Agent Framework, Azure MCP, GitHub
Copilot Agent Mode, Fabric, GitHub SDK, GitHub CLI, Azure Apps and AI
Services, Azure databases.

| Hero technology | Used? | How |
| --- | --- | --- |
| **GitHub Copilot CLI** | Yes | All inference. `src/chalkboard/agents/llm.py` |
| **GitHub Copilot Agent Mode** | Yes | Used throughout development. `.github/copilot-instructions.md` carries the project rules |
| **GitHub Actions** | Yes | CI runs the test suite on every push |
| **MCP** | Yes | The Explainer reaches the graph as an MCP client over stdio. `mcp/client.py` → `mcp/server.py` → four read-only tools |
| Microsoft Agent Framework | No | Not required. The category permits "GitHub Copilot, Microsoft Agent Framework, A2A, or another supported orchestration approach, alone or combined" — Copilot alone satisfies it |
| Microsoft Foundry | No | Requires an Azure subscription |
| Fabric | No | Requires an Azure subscription |

**Why no Azure.** The project was built with no Azure subscription and no
spend. `infra/main.bicep` describes the intended cloud path; it is validated
but has never been deployed, and the README says so plainly.

**Why not GitHub Models.** The original design used GitHub Models for free
OpenAI-compatible inference. It was **retired on 30 July 2026** and its
endpoint now returns HTTP 410 — confirmed against a live token during
development. GitHub's replacement for free individual inference is the
Copilot CLI, which is a terminal program rather than an API. That decision
shapes the architecture: see [section 13](#13-decisions-and-considerations-log).

### 3.2 Full stack

| Layer | Technology | Why |
| --- | --- | --- |
| Language | Python 3.12 | Standard library first, few dependencies |
| Graph database | Neo4j AuraDB Free | Free permanently, hosted, Cypher reads well on screen |
| Graph driver | `neo4j` Python driver | Official |
| Inference | GitHub Copilot CLI (subprocess) | Free, Microsoft-owned |
| Agent tools | MCP (`mcp` package, FastMCP) | Declared read-only tool surface |
| API | FastAPI + Uvicorn | Server-sent events for the live stream |
| Front end | Plain HTML, CSS, JavaScript | No build step; nothing to break on demo day |
| Config | `python-dotenv` | |
| Mapping | PyYAML | The ontology is a YAML file |
| Tests | pytest | 120 tests, all offline |
| CI | GitHub Actions | |

**No frontend framework** is deliberate. A build step is one more thing to
fail during a demo, and the UI is one page.

---

## 4. Architecture

### 4.1 The pipeline

```
Synthetic generator
        │  JSON Lines, one event at a time
        ▼
  Ontology loader ──────────────► Neo4j
        │  reads ontology/match.yaml      (the match knowledge graph)
        │  no model involved                      │
        ▼                                         │
  Derived edges  ─────────────────────────────────┤
        │  LINKS_WITH, PRESSES_IN, NEMESIS_OF     │
        │  recomputed on a timer                  │
        ▼                                         │
  SIGNIFICANCE GATE ◄────────────────────────────┘
        │  a scored Cypher query, every 3s
        │  ~1,300 actions in, ~20 moments out
        │
        │  moment + evidence
        ▼
   Explainer agent ──► MCP graph tools (read-only lookups)
        │          └─► Copilot CLI  (one sentence)
        │          └─► grounding check  ◄── rejects anything unsupported
        ▼
  Personaliser agent
        │  analyst rendering + supporter rendering
        ▼
   FastAPI (SSE) ──► two-pane UI + chalk board
```

### 4.2 Components

| Component | File | What it does | Model involved? |
| --- | --- | --- | --- |
| Generator | `generator/simulator.py` | Possession Markov chain producing football-realistic events | No |
| Loader | `graph/loader.py` | Executes `ontology/match.yaml`, writes nodes and edges | No |
| Deriver | `graph/derive.py` | Recomputes aggregate edges on a timer | No |
| **Gate** | `graph/significance.py` | Scores the last 90s, decides what deserves a sentence | **No** |
| Graph tools | `mcp/tools.py` | Four read-only lookups | No |
| Model client | `agents/llm.py` | Copilot CLI, stub, or off | Yes |
| Explainer | `agents/explainer.py` | Subgraph → one grounded sentence | Yes |
| Personaliser | `agents/personaliser.py` | One sentence → two viewer renderings | Yes |
| Insight store | `graph/insights.py` | Persists sentences with their evidence | No |
| Conductor | `app/pipeline.py` | Owns the clock, joins the pieces | No |
| API | `app/api.py` | SSE stream, kill switch, squad | No |
| UI | `app/static/index.html` | Two panes plus the chalk board | No |

**Note the column.** Eight of eleven components never touch a model. That is
the architecture, not an accident.

### 4.3 Where things run

| | Where | Cost |
| --- | --- | --- |
| Generator, loader, gate, agents, API, UI | Your laptop | Free |
| Neo4j AuraDB | Neo4j cloud, free tier | Free |
| Copilot CLI | Your laptop, calls GitHub | ~0.35 credits per sentence |

---

## 5. The data model

The whole model lives in one readable file: **`ontology/match.yaml`**. That
is the first thing to open after this document.

### 5.1 The rule that decides everything: arity

A Neo4j relationship connects exactly two nodes. So:

- An action with **two participants** is a **relationship**. A pass is
  Player → Player. Making it a node would turn a one-hop fact into a two-hop
  detour.
- An action with **more than two** has no choice but to be a **node**. A shot
  involves shooter, goalkeeper, zone and sometimes an assister.

Nothing is made a node for taste. One match is about **90 nodes** and
**1,450 relationships** — small enough to read on screen, which matters
because the graph goes on camera.

### 5.2 Nouns → nodes

| Node | Key | Properties | Count per match |
| --- | --- | --- | --- |
| `Competition` | `competition_id` | name, season | 1 |
| `Match` | `match_id` | matchweek, kickoff, period length, pre-game win probabilities, prediction model | 1 |
| `Stadium` | `stadium_id` | name, capacity, pitch dimensions | 1 |
| `Team` | `team_id` | name, short_name | 2 |
| `Player` | `player_id` | name | 32 |
| `Referee` | `referee_id` | name | 4 |
| `Zone` | `zone_id` | band, channel, name, half_space | 18 (shared) |
| `Shot` | `event_id` | xG, body part, outcome, speed, clock | ~30 |
| `Foul` | `event_id` | clock, card | ~20 |
| `Insight` | `insight_id` | sentence, score, clock, source | ~20 |

**Deliberately *not* nodes:**

- **Appearance** — shirt number and position live on the `PLAYED_IN` edge.
  Reification with no arity justification.
- **Possession** — `possession_id` is stamped on every action edge, so a
  passage is reconstructed by traversal rather than by hopping through a hub.

**`Insight` is not part of the football domain.** It is the system's own
record of what it said, kept separate for that reason.

### 5.3 Verbs → relationships

**Structural** — written once, the stable skeleton:

```
(Match)-[:IN_COMPETITION]->(Competition)
(Match)-[:PLAYED_AT]->(Stadium)
(Match)-[:OFFICIATED_BY {role}]->(Referee)       role: referee | assistant_1 | assistant_2 | var
(Match)-[:HOME_TEAM {formation, style}]->(Team)
(Match)-[:AWAY_TEAM {formation, style}]->(Team)
(Player)-[:PLAYS_FOR]->(Team)
(Player)-[:PLAYED_IN {shirt_number, position, starter}]->(Match)
(Zone)-[:ADJACENT_TO]->(Zone)                    54 edges, static pitch topology
```

**Actions** — the high-volume layer:

```
(Player)-[:PASSED_TO    {distance, angle, progressive, difficulty, hold_time_s, pressure, ball_speed, start_zone, end_zone}]->(Player)
(Player)-[:CARRIED_INTO {distance, pressure, hold_time_s, start_zone}]->(Zone)
(Player)-[:DISPOSSESSED {pressure, opponents_within_5m, start_zone}]->(Player)
(Player)-[:PRESSED_IN   {pressure, opponents_within_5m}]->(Zone)
(Player)-[:TOOK]->(Shot)
(Shot)-[:FROM_ZONE]->(Zone)
(Shot)-[:SAVED_BY]->(Player)
```

**Provenance** — how an insight is wired to its evidence:

```
(Insight)-[:EVIDENCED_BY]->(Shot | Player)
(Insight)-[:ABOUT_ZONE]->(Zone)
(Insight)-[:IN_MATCH]->(Match)
```

### 5.4 The temporal spine

Every action edge carries:

```python
SPINE = ("event_id", "clock_s", "period", "match_id",
         "possession_id", "sequence", "outcome")
```

- **`event_id`** — so an insight can cite this exact action by name.
- **`clock_s` + `sequence`** — order, and the merge key that keeps repeated
  passes between the same pair distinct.
- **`match_id`** — **critical.** The graph holds several matches and `Zone`
  nodes are shared across all of them. Without it, "where does this side
  press" silently averages across fixtures.

### 5.5 Zones

18 zones: six bands by three channels, **attack-relative** — always from the
perspective of the team in possession. `Z6C` is "in front of the goal they
are attacking", whichever way the teams are kicking. That removes
end-swapping from every downstream query.

| Band | Name |
| --- | --- |
| 1 | own box |
| 2 | defensive third |
| 3 | own half |
| 4 | opposition half |
| 5 | final third |
| 6 | penalty area |

`half_space` is a property, not a separate zone: true for the left and right
channels in bands 4 and above. Keeps the grid regular while letting the
narrative use the term coaches actually use.

### 5.6 Derived edges

Recomputed on a timer by `graph/derive.py`, never written by the loader.
These are the clean aggregates the UI draws and the gate reads; the raw
action edges underneath are what make their weights true.

| Edge | Between | Carries | Scope |
| --- | --- | --- | --- |
| `LINKS_WITH` | Player → Player | passes, progressive, completion, mean difficulty, mean hold | per match |
| `CIRCULATES_THROUGH` | Team → Player | received count, share | per match |
| `PRESSES_IN` | Team → Zone | turnovers, mean pressure | per match |
| `NEMESIS_OF` | Player → Player | wins, losses, win rate | **across all matches** |

**Why the scope differs.** A passing network is a fact about one game. A duel
record only becomes worth remarking on once it has recurred across fixtures —
that is what makes "seven of nine across these fixtures" sayable.

---

## 6. How it works — a worked example

Follow one moment from raw event to two screens.

### Step 1 — an event is generated

The simulator emits JSON Lines, one event at a time, paced to the match clock:

```json
{"event_id":"a4f2...","match_id":"CBM-49861","sequence":612,"period":2,
 "clock_s":1440.2,"type":"TACKLE","outcome":"SUCCESS","team_id":"HWC",
 "possession_id":"228f546f","player_id":"HWC-06","related_player_id":"ASH-09",
 "start_x":58.3,"start_y":22.1,"pressure":0.81,"opponents_within_5m":3,
 "start_zone":"Z4L","score_home":1,"score_away":0}
```

Nothing is inferred. Every field was decided by the simulation.

### Step 2 — the loader writes it to the graph

`graph/loader.py` reads the mapping, sees `source_type: [TACKLE, INTERCEPTION]`
under `DISPOSSESSED`, and produces:

```cypher
MATCH (a:Player {player_id: 'HWC-06'})
MATCH (b:Player {player_id: 'ASH-09'})
MERGE (a)-[r:DISPOSSESSED {clock_s: 1440.2, sequence: 612}]->(b)
SET r += {event_id:'a4f2...', match_id:'CBM-49861', pressure:0.81,
          start_zone:'Z4L', possession_id:'228f546f', outcome:'SUCCESS'}
```

**No model. No interpretation.** The event was already structured.

### Step 3 — derived edges are recomputed

Every few seconds:

```cypher
MATCH (winner:Player)-[d:DISPOSSESSED]->(:Player)
WHERE d.match_id = $matchId AND d.start_zone IS NOT NULL
MATCH (winner)-[:PLAYS_FOR]->(t:Team)
MATCH (z:Zone {zone_id: d.start_zone})
WITH t, z, count(d) AS turnovers, avg(d.pressure) AS intensity
MERGE (t)-[p:PRESSES_IN {match_id: $matchId}]->(z)
SET p.turnovers = turnovers, p.mean_pressure = ...
```

Now the graph knows HWC have won the ball back **six times** in `Z4L`.

### Step 4 — the gate scores the window

Every 3 seconds, looking back 90:

```
3 turnovers under pressure > 0.6   →  3 × 0.8  = 2.40
1 shot, xG 0.31                    →  0.31 × 3 = 0.93
                                               ------
                                     score      3.33
```

Threshold is 0.5, so it fires. It returns the score **and the four actions
that caused it** — nothing else will be spoken about.

**Cooldown then silences the gate for 75 match seconds**, otherwise the same
passage would fire thirty times in a row.

### Step 5 — the Explainer builds a fact pack

It takes the evidence and adds at most two narrow lookups through the graph
tools:

```
Minute 24'.

What happened:
  Weber won the ball from Varela in Z4L under pressure 0.81
  Osei won the ball from Lindqvist in Z4L under pressure 0.74
  Weber won the ball from Marchetti in Z4L under pressure 0.69
  Adeyemi shot from Z6C, outcome saved

Across the match so far, that side has won the ball back 6 times in the
left opposition half.
That player receives 18% of his team's passes.

Names you may use: Weber, Varela, Osei, Lindqvist, Marchetti, Adeyemi

FALLBACK: 3 turnover(s) under pressure, 1 shot in Z4L
```

The agent **never browses**. It gets this pack and nothing more.

### Step 6 — the model writes one sentence

> "Northgate have found a seam down Castleford's left — three turnovers
> there in two minutes, and Weber has won two of them himself."

### Step 7 — the grounding check

Before anyone sees it:

- Is it a refusal? No.
- Is it anchored to a player or zone in the evidence? Yes — Weber.
- Does every mid-sentence capitalised word appear in the evidence? Yes.
- Does every number appear in the evidence, or count the evidence itself?
  Yes — "three" counts the supplied turnovers.

**Accepted.** Had it said "Marchetti broke the press superbly" — Marchetti was
dispossessed, not the presser — it would be rejected and the fallback used.

### Step 8 — the Personaliser renders it twice

| Persona | Output |
| --- | --- |
| Analyst | "Press intensity concentrated in the left opposition half — three turnovers in two minutes, Weber taking two." |
| Supporter following **Weber** | "Your man is all over them — two of the three turnovers there are his." |
| Supporter following **Lindqvist** | "Nothing for Lindqvist here — play has moved to the other side." |

The third is not a failure. **It is the personalisation working.** Inventing
relevance would be the failure.

### Step 9 — the UI renders

Both panes fill simultaneously with different text, and the chalk board draws
the four actions: circles for the players, strokes between them, the zone
marked, the raw facts listed underneath with event ids.

**The claim on the left. The evidence on the right. The same moment.**

### Step 10 — the insight is persisted

```cypher
MERGE (i:Insight {insight_id: '7c1f...'})
SET i.sentence = '...', i.clock_s = 1440.2, i.source = 'model'
MERGE (i)-[:EVIDENCED_BY]->(a)     // one per evidence action
MERGE (i)-[:ABOUT_ZONE]->(z)
MERGE (i)-[:IN_MATCH]->(m)
```

Two payoffs:

- **Audit.** At full time you can ask the graph for everything the system
  claimed and the facts behind each claim.
- **Cost.** A replay reads the sentence instead of paying for it again.

---

## 7. The user interface

One page, no build step: `src/chalkboard/app/static/index.html`.

### 7.1 Layout

```
┌──────────────────────────────────────────────────────────────────────┐
│ [logo] Chalkboard — the tactics board that answers back               │
│        ASH 1:0 HWC   24'   Ashcombe Park · high_press v low_block     │
│                            Before kick-off: ASH 40% · draw 27% ...    │
├──────────────────────────────────────────────────────────────────────┤
│ [match ▾] [follow ▾] [language ▾] [speed ▾] [Start] [Model on]  Live  │
├─────────────────────┬─────────────────────┬──────────────────────────┤
│ Analyst             │ Supporter           │ The evidence             │
│ patterns, causes    │ following Weber     │ 4 actions behind this    │
│                     │                     │                          │
│ 24' Press intensity │ 24' Your man is all │     (Weber)              │
│     concentrated... │     over them...    │      ╱   ╲               │
│                     │                     │ (Varela) (Osei)          │
│ 19' Two line-break..│ 19' Nothing for     │                          │
│                     │     Weber here...   │  [Z4L]                   │
│                     │                     │ ──────────────────────── │
│                     │                     │ dispossessed · Weber →   │
│                     │                     │ Varela · Z4L · 0.81      │
└─────────────────────┴─────────────────────┴──────────────────────────┘
```

### 7.2 What each part is for

| Element | Purpose |
| --- | --- |
| **Scoreboard strip** | Live clock, score, venue, both tactical styles, and the pre-game forecast — so the narrative can argue with the result |
| **Match dropdown** | Which fixture to play. Insights are per match |
| **Follow dropdown** | Grouped by team, starting eleven first with shirt number and position; substitutes shown greyed |
| **Language dropdown** | The supporter rendering in Spanish, Arabic or Japanese |
| **Speed dropdown** | 20× (4½ min), 40×, 90× (1 min) |
| **Model button** | The kill switch. Flips `MODEL_ENABLED` for the running process |
| **Analyst pane** | Every significant moment, numbers-dense |
| **Supporter pane** | Only moments involving the followed player; otherwise a quiet line |
| **The evidence** | The chalk board — actions behind the current sentence, plus raw facts with event ids |

### 7.3 The tags

Each line carries a tag saying where the sentence came from:

| Tag | Meaning |
| --- | --- |
| *(none)* | Written live by the model |
| **written earlier** (amber) | Read from the graph; pre-computed |
| **from the graph** (green) | Plain summary, no model involved |

That transparency is deliberate. A judge sees the system is honest about
provenance without anyone having to explain it.

### 7.4 Design

| | |
| --- | --- |
| Palette | Slate `#1b2630`, chalk `#e9e5d9`, pitch green `#5f9e70`, chalk amber `#d9a95c` |
| Green | The analyst, and anything graph-sourced |
| Amber | The supporter, and the player currently in focus |
| Type | Barlow Condensed for numerals (scoreboard feel), Source Sans 3 for prose |
| Logo | A chalk stroke arcing between two players — the pass, which is the one relationship the whole graph is built on. A dashed amber stroke curves back: the board answering |

Boldness is spent in one place — the chalk strokes. Everything else stays
quiet.

### 7.5 API endpoints

| Endpoint | Returns |
| --- | --- |
| `GET /` | The page (`no-store`, so a stale copy cannot masquerade as a broken UI) |
| `GET /api/matches` | Every match in the graph |
| `GET /api/squad?match=` | Both squads, starters flagged |
| `GET /api/stream?match=&follow=&language=&speed=` | Server-sent events: header, clock ticks, insights, full time |
| `GET /api/model` / `POST /api/model?enabled=` | Read or flip the kill switch |
| `GET /api/health` | Graph status, node count, model state |

---

## 8. Key code, explained

### 8.1 The significance gate — the hinge

`ontology/significance.cypher`. Everything the project claims rests on this
being a query rather than an agent.

```cypher
MATCH (a:Player)-[r]->(b)
WHERE r.match_id = $matchId
  AND type(r) IN ['PASSED_TO', 'CARRIED_INTO', 'DISPOSSESSED', 'PRESSED_IN']
  AND r.clock_s > $now - $window AND r.clock_s <= $now
WITH collect({
  event_id: r.event_id,
  kind:     type(r),
  actor:    a.name,
  target:   b.name,
  zone:     r.start_zone,
  // A turnover won under heavy pressure is the press working, not a
  // stray pass. That distinction is control versus chaos.
  forced:   CASE WHEN type(r) = 'DISPOSSESSED' AND coalesce(r.pressure,0) > 0.6
                 THEN 0.8 ELSE 0.0 END,
  // A hard progressive pass completed is someone breaking a line.
  breaking: CASE WHEN type(r) = 'PASSED_TO' AND r.pass_progressive
                      AND coalesce(r.pass_difficulty,0) > 0.6
                      AND r.outcome = 'SUCCESS'
                 THEN 0.5 ELSE 0.0 END
}) AS actions
...
WITH window,
     reduce(total = 0.0, x IN window |
            total + x.forced + x.breaking + coalesce(x.chance, 0.0)) AS score
WHERE score > $threshold
RETURN score, [...][..$maxEvidence] AS evidence, size(window) AS window_size
```

**Why a query:**

- **Deterministic** — the same window always scores the same
- **Free** — ~1,800 executions a match at no cost
- **Fast** — milliseconds, so it keeps up with live play
- **Arguable** — disagree with a weight and change it
- **Demonstrable** — query and evidence both go on screen

The two-part shape (relationships *and* `Shot` nodes, unioned) is the price
of the edge-centric model. An event-node model would be one `MATCH`. That
cost is paid once, here, in exchange for a graph a person can read.

### 8.2 The grounding check — the guarantee

`agents/explainer.py`. Crude on purpose: string matching, not comprehension.
**Strict in the right direction** — it can reject a true sentence, and cannot
accept an invented one.

```python
def check_grounded(self, sentence, facts) -> tuple[bool, str]:
    haystack = _fact_text(facts)
    allowed_names = {n for name in facts["names"] for n in name.split()}

    # 1. A model handed a mangled prompt replies asking for the details it
    #    was already given. Such a reply invents nothing, so the other
    #    checks pass it happily - and it would be stored as an insight.
    for tell in ("i can't", "i cannot", "provide the", "without details"):
        if tell in sentence.lower():
            return False, "is a refusal rather than an explanation"

    # 2. The sentence must be anchored to something concrete that was
    #    supplied - a player or a zone.
    anchors = [n.split()[-1] for n in facts["names"]] + list(facts["zones"])
    if anchors and not any(a in sentence for a in anchors):
        return False, "is anchored to nothing in the evidence"

    # 3. Only MID-SENTENCE capitals can be names. English capitalises the
    #    first word of every sentence, so treating those as names rejects
    #    most well-formed prose.
    for match in re.finditer(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z]{2,}\b", sentence):
        token = match.group()
        if token in _SAFE_WORDS or token in allowed_names or token in haystack:
            continue
        return False, f"names {token}, who is not in the evidence"

    # 4. No trailing boundary: an ordinal like "47th" must not slip past
    #    just because a letter follows the digits.
    for number in re.findall(r"\b\d+(?:\.\d+)?", sentence):
        if number in haystack or _is_countable(number, facts):
            continue
        return False, f"states {number}, which is not in the evidence"

    return True, ""
```

Each rule was added in response to a real failure seen during development.
See [section 13](#13-decisions-and-considerations-log).

### 8.3 The three-tier lookup — why the demo is fast

`agents/explainer.py`:

```python
def explain(self, moment: Moment) -> Insight:
    """Three tiers, in order of cost.

    A stored sentence first, a live model call if there is none, and the
    graph-only summary if that fails or is switched off. Every tier is
    correct; they differ in fluency and price.
    """
    cached = self._cached(moment)      # tier 1 - free, instant
    if cached:
        return cached

    facts = self.build_facts(moment)
    fallback = moment.summary()        # tier 3 - graph facts alone

    sentence, source, rejected = fallback, "graph", None
    candidate = self.client.complete(self._prompt(facts, fallback), system=SYSTEM)
    if candidate and candidate.strip() != fallback.strip():
        ok, reason = self.check_grounded(candidate, facts)   # tier 2
        if ok:
            sentence, source = candidate, "model"
        else:
            rejected = f"{reason}: {candidate}"
```

### 8.4 Failure never raises

`agents/llm.py`. A timeout, a missing binary, a rate limit and the kill
switch must all look the same to the caller — no sentence — so the system
falls back rather than taking the demo down.

```python
def complete(self, prompt, system=None) -> str | None:
    if not self.available():
        return None
    try:
        result = subprocess.run(
            argv, input=stdin_text, capture_output=True, text=True,
            timeout=self.timeout_s, check=False,
            # The CLI is a coding agent: started inside a repository it
            # reads files and plans before answering. For a one-sentence
            # rewrite that work is pure latency.
            cwd=self._workdir(),
        )
    except subprocess.TimeoutExpired:
        return None
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return _clean(result.stdout)
```

### 8.5 Where MCP sits, and where it does not

Eight components talk to Neo4j. Only one of them is an agent.

| Component | Reaches Neo4j via | Why |
| --- | --- | --- |
| `agents/explainer.py` | **MCP** (`mcp/client.py` → `mcp/server.py`) | It is an agent. Its access should be a declared, inspectable surface |
| `graph/loader.py` | Driver | A batch pipeline writing ~1,450 edges. Protocol overhead for nothing |
| `graph/derive.py` | Driver | A scheduled job |
| `graph/significance.py` | Driver | Must answer in milliseconds, every 3 seconds |
| `graph/insights.py` | Driver | A store |
| `app/pipeline.py`, `app/api.py` | Driver | Web plumbing |

**The principle:** *anything an AI agent touches goes through MCP; everything
deterministic uses the driver directly.*

Routing the loader or the gate through MCP would be using a protocol for
agent sandboxing as a general database layer — slower, and a sign of not
understanding what it is for. The contrast is the point.

The client presents the same four method names as the direct tools, so the
Explainer cannot tell which it holds. It tries MCP first, and falls back to
in-process tools if the server cannot start — because a boundary that breaks
the demo is a poor trade for architectural tidiness:

```python
def connect_tools(session, prefer_mcp=True) -> tuple[Any, str]:
    if prefer_mcp:
        client = MCPGraphTools()
        if client.start():
            return client, "mcp"
    return GraphTools(session), "direct"
```

`GET /api/health` reports which was used, as `agent_tools`.

### 8.6 The read-only tool surface


`mcp/tools.py`. The agent cannot write to the graph **by construction**, not
by instruction — there is no write path to reach. A test asserts no `CREATE`,
`MERGE`, `DELETE` or `SET` appears in any tool query.

```python
TOOL_SPECS = [
    {"name": "get_player",         "params": {"player_id", "match_id"}},
    {"name": "get_derived_edges",  "params": {"player_id", "match_id"}},
    {"name": "get_possession",     "params": {"possession_id"}},
    {"name": "get_zone_pressure",  "params": {"team_id", "match_id"}},
]
```

Each answers one narrow question about something **already in the evidence**.
The agent is never invited to go hunting.

### 8.7 Idempotent loading

`graph/loader.py`. A streaming loader that is not idempotent corrupts the
graph the first time anything is retried.

```python
# An action is identified by its endpoints AND its moment: two players pass
# to each other many times in a match and each pass is its own relationship
# with its own evidence value, so clock_s and sequence join the merge key.
#
# A structural edge is identified by its endpoints alone. These carry no
# timestamp, and Neo4j refuses to merge on a null property.
ident = (a, b, clean.get("clock_s"), clean.get("sequence")) if keyed else (a, b)
```

---

## 9. Key features

| Feature | What it is | Why it matters |
| --- | --- | --- |
| **Calibrated synthetic generator** | Possession Markov chain with four tactical styles, producing ~1,300 events per match | The rules mandate synthetic data, and its quality is the first scored bullet |
| **Graph-decided significance** | A scored Cypher query picks ~20 moments from ~1,300 actions | The project's central claim, and verifiable on screen |
| **Grounding check** | Every sentence verified against its evidence before display | Turns "grounded" from an assertion into a mechanism |
| **Two genuinely different viewers** | Analyst and single-player supporter, side by side | The half of the brief most entries miss |
| **Quiet is a valid rendering** | "Nothing for Lindqvist here" when their player was uninvolved | Proves the viewers diverge rather than just rephrase |
| **Multi-language** | Supporter rendering in any language | Named in the brief |
| **Graceful degradation** | Kill switch; output continues from graph facts | Explicitly scored by the chosen category |
| **Audit trail** | Insight nodes wired to the actions that justified them | Provenance is queryable, not claimed |
| **Attack-relative zones** | 18 zones, always from the attacking perspective | Removes end-swapping from every query |
| **Pre-game forecast** | Poisson model over squad strength, stored on the Match | Lets the narrative argue with the result |
| **Multi-match graph** | Three fixtures in one graph | Enables "fourth goal in three matches" and cross-match duel records |

### Generator calibration

Measured over 80 generated matches:

| Metric | Chalkboard | Professional football |
| --- | --- | --- |
| Goals per match | 2.91 | ~2.8 |
| Shots per match | 28.7 | ~25 |
| xG per shot | 0.11 | ~0.10 |
| Pass accuracy | 79% | ~80% |
| Events per match | ~1,280 | ~1,500 |

Shot quality responds correctly to style: `direct` takes the most shots at
the lowest average xG, `low_block` the fewest at the highest. That
relationship is asserted in the test suite, because it is the property that
gives the Explainer something true to say.

---

## 10. How it meets the judging criteria

Five equally weighted criteria at 20% each.

### Technological Implementation

| Bullet | Evidence |
| --- | --- |
| Creative, optimised synthetic data | Possession Markov chain, four tactical styles, calibrated against professional rates, style-responsive shot selection. `generator/` |
| Quality development practices | 120 tests all running offline, GitHub Actions CI, type hints, documented decisions |
| Hero technologies | Copilot CLI, Copilot Agent Mode, GitHub Actions, MCP. See [section 3](#3-products-and-technologies-used) for what is and is not used |
| Well-structured, documented, maintainable | The entire data model is one readable YAML file; docstrings explain *why*, not *what* |

### Agentic Design and Innovation

| Bullet | Evidence |
| --- | --- |
| Creative application of agentic patterns | A deterministic gate decides *what*; agents only decide *how to say it*. Judges will not see this elsewhere |
| Orchestration, MCP, multi-agent collaboration | Two specialist agents with distinct roles, coordinating through the graph as shared state; MCP tool surface |
| Novel or improved | The grounding check — a model that cannot name anyone it was not given |

### Real-World Impact

| Bullet | Evidence |
| --- | --- |
| Significance of the problem | Explanation does not scale; every viewer gets the same feed |
| Production readiness | Honest gap: runs locally, bicep written but not deployed |
| Potential impact | The pattern transfers directly to supply-chain exceptions, trading surveillance, asset performance — any event stream needing explanation for different audiences |

### User Experience and Presentation

| Bullet | Evidence |
| --- | --- |
| Well-designed, intuitive | Two panes plus live evidence; no explanation needed to understand it |
| Demo communicates value | One screen shows claim, evidence and two viewers simultaneously |
| Balanced frontend and backend | Substantial graph and agent backend; a purpose-built front end, not a framework default |

### Adherence to Category — Best Multi-Agent System

The category asks whether specialised agents have **distinct roles**,
coordinate through **shared state**, make effective **handoffs**, **recover
from failures**, and deliver an outcome a single agent could not.

| Requirement | How Chalkboard meets it |
| --- | --- |
| Distinct roles | Explainer establishes *why*; Personaliser decides *for whom*. Neither does the other's job |
| Shared state | Both work through the graph. The Explainer writes an `Insight` node; the Personaliser reads it. The state is durable and queryable |
| Effective handoffs | Gate → Explainer (moment + evidence) → Personaliser (sentence + subject). Each handoff carries exactly what the next stage needs |
| Failure recovery | Three tiers. Model unavailable, rejected sentence, or kill switch all degrade to correct graph-only output. Demonstrable on camera in ten seconds |
| Beyond a single agent | A single agent would have to choose subjects, gather facts, write and verify — and would be unverifiable at every step |

### The five-stage pipeline the brief requires

| Stage | Where it happens |
| --- | --- |
| (a) Ingest | Generator → loader |
| (b) Interpret | Cypher writes; derived edges aggregate |
| (c) Explain | Gate decides significance; Explainer says why |
| (d) Render | Timestamped, machine-readable output over SSE |
| (e) Personalise | Two personas, any language |

---

## 11. Testing

```bash
python -m pytest tests/ -q        # 130 tests
```

**Every test runs offline** — no Neo4j, no model, no network. CI has no
secrets, and the suite must pass without them.

| File | Tests | Covers |
| --- | --- | --- |
| `test_simulator.py` | 10 | Determinism, schema, football realism |
| `test_loader.py` | 19 | Mapping, idempotency, generated Cypher |
| `test_derive.py` | 11 | Scoping, parameters, statement hygiene |
| `test_significance.py` | 12 | Firing, staying quiet, cooldown, evidence contract |
| `test_llm.py` | 17 | Failure never raises; invocation shapes |
| `test_mcp_tools.py` | 10 | Read-only by construction |
| `test_agents.py` | 21 | The grounding check, in both directions |
| `test_insights.py` | 10 | The three tiers |
| `test_pipeline.py` | 6 | Wiring, and surviving a database blip |
| `test_mcp_client.py` | 10 | Interface parity with the direct tools; never fatal |
| `test_pipeline`, others | — | |

### How the fakes work

A `FakeSession` records the Cypher and parameters it is handed:

```python
class FakeSession:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    def run(self, query, **params):
        self.calls.append((query, params))
        return list(self.rows)
```

**Its limitation, stated plainly:** it cannot parse Cypher. A query that is
syntactically invalid passes the tests and fails against the database. Two
real bugs reached Aura this way — a null merge key, and a dotted name used as
a pattern endpoint. Both now have **text-inspection tests** that scan the
generated Cypher:

```python
def test_no_statement_uses_a_dotted_name_as_a_pattern_endpoint(statements):
    for name, body in statements:
        dotted = re.findall(r"(?:MERGE|MATCH)\s*\(\s*\w+\.\w+", body)
        assert not dotted, f"{name} uses a dotted name as a pattern endpoint"
```

**Rule of thumb:** after any edit to a `.cypher` file, run `derive --all`
before assuming it works. Syntax errors surface at runtime, not in tests.

### Tests worth reading first

| Test | Why |
| --- | --- |
| `test_rejects_a_player_who_was_not_involved` | The failure mode that matters: fluent, confident, and about someone nowhere near the ball |
| `test_stays_quiet_on_a_dull_passage` | The half of the gate's job people forget to test |
| `test_a_moment_without_your_player_is_quiet` | Proves the two viewers diverge |
| `test_is_idempotent_on_replay` | A streaming loader that is not idempotent corrupts the graph |
| `test_shot_quality_responds_to_style` | Guards the property that gives the Explainer something true to say |

---

## 12. File structure

```
chalkboard/
├── README.md                     pitch, stack, how to run, what is out of scope
├── CLAUDE.md                     project rules for Claude Code
├── CODE_OF_CONDUCT.md            Microsoft Open Source CoC
├── LICENSE                       MIT
├── pyproject.toml                packaging, dependencies, pytest config
├── .gitignore                    excludes .env, generated data, caches
├── .env.example                  every variable, no values
├── run.py                        one-command launcher
├── bench_llm.py                  times the Copilot CLI under different settings
│
├── .github/
│   ├── copilot-instructions.md   project rules Copilot reads automatically
│   └── workflows/ci.yml          runs pytest on every push
│
├── ontology/                     ◄── THE DATA MODEL. Read match.yaml first.
│   ├── match.yaml                nouns, verbs, adjectives - the whole model
│   ├── derived.cypher            the four aggregate edges
│   └── significance.cypher       the gate
│
├── src/chalkboard/
│   ├── generator/                synthetic match simulation
│   │   ├── models.py             event schema and entity dataclasses
│   │   ├── pitch.py              18 zones, adjacency, football names for space
│   │   ├── teams.py              invented clubs, squads, four tactical styles
│   │   ├── simulator.py          the possession Markov chain
│   │   └── cli.py                generate to JSONL, or stream at speed
│   │
│   ├── graph/                    everything deterministic
│   │   ├── loader.py             executes match.yaml; idempotent, batched
│   │   ├── derive.py             recomputes aggregates on a timer
│   │   ├── significance.py       the gate, cooldown, Moment
│   │   └── insights.py           persists sentences with their evidence
│   │
│   ├── mcp/                      the agent's read-only view of the graph
│   │   ├── tools.py              four queries, testable without the mcp package
│   │   ├── server.py             thin MCP binding over those four
│   │   └── client.py             sync MCP client; falls back to direct tools
│   │
│   ├── agents/                   the only place a model appears
│   │   ├── llm.py                Copilot CLI | stub | off
│   │   ├── explainer.py          subgraph → one grounded sentence
│   │   ├── personaliser.py       one sentence → two viewer renderings
│   │   └── precompute.py         write every sentence once, offline
│   │
│   └── app/
│       ├── pipeline.py           the conductor: clock, gate, agents
│       ├── api.py                FastAPI, SSE, kill switch
│       └── static/
│           ├── index.html        two panes plus the chalk board
│           ├── logo.svg
│           └── favicon.svg
│
├── tests/                        120 tests, all offline
│   ├── test_simulator.py
│   ├── test_loader.py
│   ├── test_derive.py
│   ├── test_significance.py
│   ├── test_llm.py
│   ├── test_mcp_tools.py
│   ├── test_agents.py
│   ├── test_insights.py
│   └── test_pipeline.py
│
├── data/
│   └── sample_CBM-00042.jsonl    a committed match, so the repo runs with no setup
│
├── docs/
│   └── RUNBOOK.md                this document
│
└── infra/
    └── main.bicep                the Azure path. Validated, NOT deployed.
```

### Reading order for someone new

1. `README.md` — what it is
2. `ontology/match.yaml` — the entire data model in one file
3. `ontology/significance.cypher` — the idea the project rests on
4. `agents/explainer.py` — the grounding check
5. `app/static/index.html` — what a viewer sees

---

## 13. Decisions and considerations log

Every significant choice, and why. Chronological.

### Scope and framing

| # | Decision | Reasoning |
| --- | --- | --- |
| 1 | Football, not foosball | Initial misread of the brief. The pivot changed the data model entirely — football event data is far richer |
| 2 | Category: **Best Multi-Agent System** | Its description names Agent Framework and A2A explicitly and does **not** require Azure hosting. The other categories do |
| 3 | Synthetic data generated, not sourced | Mandatory under the rules. No real Premier League data is distributed, and using real data would disqualify |
| 4 | Invented clubs, venues, players | Third-party trademarks are barred from the submission. Fictional names solve the trademark and synthetic-data requirements at once |
| 5 | Two agents, not five | The original design had Interpreter, Significance, Explainer, Personaliser and Recap. Build time forced a cut — and the cut improved the design. Interpreter became Cypher, Significance became a query, Recap was dropped |

### Platform

| # | Decision | Reasoning |
| --- | --- | --- |
| 6 | No Azure | No subscription, no spend. `infra/main.bicep` documents the path honestly rather than implying deployment |
| 7 | Neo4j Aura Free over Azure SQL graph / Fabric / Cosmos | Free permanently, hosted so it stays reachable, and Cypher reads far better on screen than T-SQL or Gremlin |
| 8 | GitHub Models → **Copilot CLI** | GitHub Models was retired 30 July 2026; its endpoint returns 410, confirmed against a live token. The Copilot CLI is GitHub's free replacement and still a Microsoft service |
| 9 | Claude not used at runtime | A chat subscription carries no API credits, and a competitor's model in a Microsoft hackathon repo is the most damaging thing that could sit in the repository at the viability gate |

### Data model

| # | Decision | Reasoning |
| --- | --- | --- |
| 10 | **Graph built deterministically, never by an LLM** | Events arrive structured. There is nothing to extract; extraction would cost a call per event, could not keep pace with live play, and could hallucinate a relationship the record already states |
| 11 | **Actions as relationships, not nodes** | Reversed an earlier decision. A pass has two participants and is natively an edge; making it a node turns a one-hop fact into a two-hop detour. Cut a match from ~1,590 nodes to ~90 |
| 12 | Shot, Foul, Insight as nodes | Arity forces it — a shot involves shooter, keeper, zone and sometimes an assister |
| 13 | `Appearance` node dropped | Shirt number and position moved onto `PLAYED_IN`. Reification with no arity justification |
| 14 | `Possession` node dropped | `possession_id` stamped on every action edge; a passage is reconstructed by traversal |
| 15 | Zones as nodes, 18 of them, attack-relative | Space has to be addressable for the narrative to talk about it. Attack-relative removes end-swapping everywhere |
| 16 | `hold_time_s` on passes | The difference between a one-touch combination and a player dwelling under pressure. Exactly what the Explainer should notice |
| 17 | `match_id` on every action edge | Zone nodes are shared across matches; without it, aggregates silently mix fixtures |
| 18 | Repeated passes stay separate relationships | Collapsing them into a weighted edge would make an insight uncitable, and the aggregate already exists as `LINKS_WITH` |
| 19 | Multi-match graph (3 fixtures) | Enables player milestones and cross-match duel records — insights impossible with one match |
| 20 | Stadium, 4 referees, pre-game prediction | Entity completeness; referees give fouls somewhere to point; the forecast lets the narrative argue with the result |

### The agents

| # | Decision | Reasoning |
| --- | --- | --- |
| 21 | **Significance as a Cypher query, not an agent** | Deterministic, free, instant, arguable and provable on screen. An agent deciding importance is unverifiable |
| 22 | Cooldown of 75 match seconds | 90-second window ticking every 3 seconds would fire thirty times for one passage. Cooldown is what makes it read like commentary |
| 23 | Grounding check, strict in one direction | It can reject a true sentence; it cannot accept an invented one. That asymmetry is the point |
| 24 | Personas differ in **what** they are told, not just tone | A casual fan is only a shorter analyst — same content, smaller words — which reads as a font-size setting, not personalisation |
| 25 | Quiet is a valid rendering | "Nothing for Lindqvist here" proves divergence. Inventing relevance would be the failure |
| 26 | Insight nodes persisted | Audit trail, and a cache that makes replays free |
| 27 | Pre-compute, with live fallback | The CLI takes ~13s and 0.35 credits per sentence. Pre-computing makes playback instant; the live path still works for anything uncached |

### Bugs found and fixed

Worth recording, because each one shaped a rule.

| Found | Cause | Fix |
| --- | --- | --- |
| Goals 3× too high | Uncalibrated xG and conversion | Recalibrated intercept and finishing multiplier against professional rates |
| Possession sides shot *least*, at *worst* quality | Shot appetite scaled magnitude uniformly, never selectivity | Made the shot-chance exponent style-dependent, so a patient side waits for a better position |
| Score inconsistent with goal events | Period boundary truncated a possession *after* its goal was counted | Yield whole possessions; play runs to the end of the passage |
| Seeds not reproducible | Kickoff came from the wall clock | Derived from the seed |
| Match node written 1,308 times | Every event carries `match_id` | Batch deduplication — 1,308 writes became 62 |
| Null merge key on `ADJACENT_TO` | Structural edges keyed on `clock_s`, which they do not have | Separate keying for temporal and structural edges |
| `row.player` rejected by Cypher | Map field used as a pattern endpoint | Unpack with `WITH` before the `MERGE` |
| Prompt arrived empty on Windows | `cmd.exe` mangles newlines in an argv element | Flatten the prompt, or pipe via stdin |
| A refusal stored as an insight | "I can't explain..." invents no names or numbers, so the check passed it | Added a refusal rule |
| "47th" slipped past the number check | Regex required a word boundary after the digits | Dropped the trailing boundary |
| "Two early shots..." rejected as a name | Sentence-initial capitals treated as names | Only mid-sentence capitals are candidates |
| Zone-only sentence rejected | Rule demanded a player name | Anchor may be a player **or** a zone |
| Follow dropdown reset on start | Header rebuilt the list after the stream began | Squad loaded when a match is picked |
| Empty UI while the API was healthy | Browser serving a cached page | `Cache-Control: no-store` on the page |

**The pattern worth noting:** every grounding rejection seen in development
was a false positive, never a genuine hallucination. Each fix narrowed the
rule without weakening the guarantee.

---

## 14. Open items

Stated plainly, because a judge who finds an unacknowledged gap reads it as a
defect, while an acknowledged one reads as scoping.

### Should be closed before submission

| # | Item | Why it matters | Effort |
| --- | --- | --- | --- |
| 1 | **Judges cannot run it** | The app needs Neo4j credentials. A judge cloning the repo has none, so the project cannot be tested as the rules contemplate | ~40 min for an offline demo mode replaying a committed JSON file |
| 2 | **Zone codes appear in prose** | Sentences say `Z4L` rather than "the left opposition half". The graph holds the human name; only the code is passed to the fact pack | One line |

### Accepted limitations

| Item | Note |
| --- | --- |
| Microsoft Agent Framework not used | Optional for the category, which accepts Copilot alone. The two agents are plain Python classes orchestrated by `app/pipeline.py` |
| The Explainer does not choose its own lookups | Which tools to call is decided by two `if` statements in `build_facts`, not by the model. Letting the model choose would be properly agentic but costs an extra call per insight — unaffordable at ~13s each |
| No substitutions | The simulator never emits `SUBSTITUTION`; the starting eleven plays 90 minutes. The UI shows substitutes greyed out rather than pretending |
| No match recaps | In the brief, but not demonstrable inside a two-minute video that must also show live play |
| No overlay renderer | Output is timestamped and machine-readable so an overlay system *could* consume it; none is built |
| No auto-eventing from video | Optional in the brief, far out of scope |
| Not deployed | Bicep is validated, never executed. The README says so |
| `neo4j+ssc://` on some networks | A TLS-inspection workaround, local only |
| Personaliser output is not cached | Only Explainer sentences are stored, so the Personaliser still makes live calls even on a pre-computed match |

### Honesty requirements for the submission

- The README and video **must** say sentences are pre-computed rather than
  generated during playback. The pipeline is identical either way, but
  claiming live inference when it is cached would be misleading.
- The bicep must be labelled as not deployed.
- The hero-technology list must match what is actually used.

---

## Appendix A — command reference

### Generate matches

```bash
# one match
python -m chalkboard.generator.cli --seed 42 generate --out data/CBM-00042.jsonl

# three, for a multi-match graph
python -m chalkboard.generator.cli generate --matches 3 --out-dir data/

# force a tactical matchup
python -m chalkboard.generator.cli --home-style high_press --away-style low_block \
    generate --out data/press_v_block.jsonl

# replay at speed to stdout
python -m chalkboard.generator.cli --seed 42 stream --speed 60
```

### Load the graph

```bash
# dry run - prints the Cypher, connects to nothing
python -m chalkboard.graph.loader --dry-run < data/CBM-00042.jsonl

# load everything
cat data/*.jsonl | python -m chalkboard.graph.loader          # macOS / Linux
type data\*.jsonl | python -m chalkboard.graph.loader         # Windows

# recompute the aggregates
python -m chalkboard.graph.derive --all
python -m chalkboard.graph.derive --match CBM-49861 --watch
```

### Tune and inspect

```bash
# sweep a match, print every moment the gate would fire on
python -m chalkboard.graph.significance --match CBM-49861

# try a different threshold
python -m chalkboard.graph.significance --match CBM-49861 --threshold 0.8

# time the Copilot CLI under different settings
python bench_llm.py
```

### Write sentences

```bash
# cheap test - three sentences
python -m chalkboard.agents.precompute --match CBM-49861 --limit 3

# the whole match
python -m chalkboard.agents.precompute --match CBM-49861
```

### Run the app

```bash
python run.py                       # then open http://localhost:8000
uvicorn chalkboard.app.api:app --reload    # with auto-reload
```

### Useful Cypher

```cypher
// what is in the graph
MATCH (n) RETURN labels(n)[0] AS label, count(*) ORDER BY count(*) DESC;

// clear everything
MATCH (n) DETACH DELETE n;

// clear only the written sentences
MATCH (i:Insight) DETACH DELETE i;

// which matches have pre-computed insights
MATCH (i:Insight) RETURN i.match_id, count(i) ORDER BY count(i) DESC;

// the passing network
MATCH (a:Player)-[l:LINKS_WITH]->(b:Player)
RETURN a.name, b.name, l.passes, l.progressive, l.mean_hold_s
ORDER BY l.passes DESC LIMIT 12;

// who the side routes through - use this to pick a player to follow
MATCH (t:Team)-[c:CIRCULATES_THROUGH {match_id: 'CBM-49861'}]->(p:Player)
RETURN p.name, c.share ORDER BY c.share DESC LIMIT 5;

// where the press works
MATCH (t:Team)-[pr:PRESSES_IN]->(z:Zone)
RETURN t.short_name, z.name, pr.turnovers, pr.mean_pressure
ORDER BY pr.turnovers DESC LIMIT 10;

// matchups across all fixtures
MATCH (a:Player)-[n:NEMESIS_OF]->(b:Player)
WHERE n.duels >= 3
RETURN a.name, b.name, n.wins, n.losses, n.win_rate ORDER BY n.win_rate DESC;

// top scorers across every match
MATCH (p:Player)-[:TOOK]->(s:Shot) WHERE s.outcome = 'GOAL'
RETURN p.name, count(s) AS goals, round(sum(s.shot_xg)*100)/100 AS xg
ORDER BY goals DESC LIMIT 10;

// the audit trail - everything the system claimed, and its evidence
MATCH (i:Insight {match_id: 'CBM-49861'})
OPTIONAL MATCH (i)-[:EVIDENCED_BY]->(e)
RETURN i.clock_s, i.sentence, i.source, count(e) AS evidence
ORDER BY i.clock_s;
```

---

## Appendix B — troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `ServiceUnavailable: Failed to DNS resolve` | Typo in `NEO4J_URI` — often extra text appended | Check the URI ends at `.io` |
| `SSLCertVerificationError: self-signed certificate` | Network TLS inspection | Use `neo4j+ssc://` locally |
| `AuthError: client is unauthorized` | `NEO4J_USER` is not `neo4j`, or the password is stale | Username is always `neo4j` on Aura Free |
| Instance unreachable, was working yesterday | Aura Free pauses after a few days idle | Resume from console.neo4j.io |
| `ModuleNotFoundError: No module named 'pytest'` / `uvicorn` | Virtual environment not active | `.venv\Scripts\activate` — the prompt should show `(.venv)` |
| `copilot failed to start: [WinError 2]` | npm installs a `.cmd` shim | `COPILOT_CMD=copilot.cmd` |
| `no platform package found` | Broken npm install | `npm uninstall -g @github/copilot && npm install -g @github/copilot` |
| CLI replies "without details of the event" | `cmd.exe` mangled the newlines | `COPILOT_STDIN=true` |
| `a value is required for '--prompt'` | stdin mode left `-p` behind | Handled in code now; or `COPILOT_ARGS=--allow-all-tools` |
| Clock freezes at 2' | Each live CLI call takes ~13s and blocks the stream | Pre-compute the match, or drop to 20× speed |
| UI empty but `/api/health` is fine | Browser cached an old page | Hard refresh (`Ctrl+Shift+R`); the page is now `no-store` |
| Match plays in silence | No insights for that match | Pre-compute it, or select one that has them |
| `could not convert string to float: ''` | An empty value in `.env` | Give it a value or remove the line |
| `Cannot merge ... null property value` | A `.cypher` edit introduced a null merge key | Fixed in code; re-run `derive --all` after any Cypher edit |
| Supporter pane always quiet | Following an uninvolved player | Use the `CIRCULATES_THROUGH` query to pick a central one |

---

## Appendix C — references

### Hackathon

- Hackathon site — <https://aka.ms/insidethegame>
- Official rules — <https://github.com/microsoft/insidethegamehackathon/blob/main/OFFICIAL%20RULES.md>
- Resource playlists — <https://github.com/microsoft/insidethegamehackathon/blob/main/RESOURCE%20PLAYLISTS.md>
- Code of conduct — <https://opensource.microsoft.com/codeofconduct/>

### Key dates

| Event | When |
| --- | --- |
| Submission closes | 27 October 2026, 11:59pm Pacific (≈7am UK on the 28th) |
| Judging | 27 October – 10 November 2026 |
| Winners announced | by 20 November 2026 |

**The repository must stay public and the project testable until judging ends
on 10 November.**

### Technology

- GitHub Copilot CLI — <https://docs.github.com/copilot/how-tos/copilot-cli>
- GitHub Copilot pricing — <https://github.com/settings/copilot>
- Model Context Protocol — <https://modelcontextprotocol.io>
- Microsoft Agent Framework — <https://azure.microsoft.com/en-us/blog/introducing-microsoft-agent-framework/>
- Neo4j AuraDB — <https://neo4j.com/cloud/aura/>
- Cypher manual — <https://neo4j.com/docs/cypher-manual/current/>
- FastAPI — <https://fastapi.tiangolo.com/>

### Project

- Repository — <https://github.com/AbbasRepo/chalkboard>

### Football analytics background

- Expected goals (xG) — the probability a shot becomes a goal, given
  distance, angle and context. Chalkboard's model is a logistic over
  distance and shooting angle, calibrated so the mean shot is worth ~0.10.
- Passing networks — players as nodes, passes as weighted edges. Centrality
  identifies who a side routes play through.
- Half-spaces — the channels between centre and touchline. A property of a
  zone here, not a separate zone.
- Progressive passes — those moving the ball meaningfully towards goal.
  Chalkboard uses an 8-unit gain on the normalised pitch.

---

*Last updated 21 October 2026.*
