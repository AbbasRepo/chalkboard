# Bootstrap

Setup instructions for Chalkboard. Delete this file once the repo is live.

Target repository: **https://github.com/AbbasRepo/chalkboard** (must be public).
Not `AbbasRepo/AbbasRepo` — that is the GitHub profile repository.

## What you have

| File | What it is |
| --- | --- |
| `chalkboard-scaffold.zip` | The complete repository. 56 files, already in their final folders. Nothing needs creating. |
| Design pack markdown exports (optional) | Five tabs exported from the Claude Doc. They go in `docs/`. |

## What to do

Put the zip in an empty working folder, open that folder in VS Code, switch
Copilot Chat to **Agent** mode, and paste the prompt in `COPILOT_PROMPT.md`.

Copilot will extract it, verify the tree, run the tests, check for leaked
secrets and push to GitHub. You will need to be signed in to GitHub in VS Code.

## The structure it produces

```
chalkboard/
├── README.md                      pitch, stack, how to run, what is out of scope
├── BOOTSTRAP.md                   this file — delete after setup
├── COPILOT_PROMPT.md              the setup prompt — delete after setup
├── LICENSE                        MIT
├── CODE_OF_CONDUCT.md             Microsoft Open Source CoC
├── pyproject.toml                 packaging, dependencies, pytest config
├── .gitignore                     excludes .env, data/*.jsonl, caches
├── .env.example                   every variable, no values
├── run.py                         one-command launcher (WP4)
│
├── .github/
│   ├── copilot-instructions.md    project rules — Copilot reads this automatically
│   └── workflows/ci.yml           runs pytest on every push
│
├── ontology/                      THE data model
│   ├── match.yaml                 events to nodes and edges. Read this first.
│   ├── derived.cypher             derived-edge recomputation (WP2)
│   └── significance.cypher        the significance gate — written
│
├── src/chalkboard/
│   ├── generator/                 DONE — synthetic match simulation
│   │   ├── models.py              event schema
│   │   ├── teams.py               invented clubs, four tactical styles
│   │   ├── simulator.py           possession Markov chain, calibrated
│   │   └── cli.py                 generate and stream commands
│   ├── graph/                     WP2 — evening one
│   │   ├── loader.py              YAML-driven Cypher writer
│   │   ├── derive.py              derived edges on a timer
│   │   └── significance.py        runs the gate
│   ├── mcp/server.py              WP3 — read-only graph tools for agents
│   ├── agents/                    WP3 — evening two
│   │   ├── models.py              GitHub Models client + offline stub
│   │   ├── explainer.py           why the moment mattered
│   │   └── personaliser.py        two personas, two languages
│   └── app/                       WP4
│       ├── api.py                 FastAPI
│       └── static/index.html      two panes plus live subgraph
│
├── tests/                         10 passing, 2 stubs for WP2
├── data/sample_CBM-00042.jsonl    a committed match, runs with no setup
├── docs/                          design pack exports, diagrams in img/
└── infra/main.bicep               Azure path, validated, NOT deployed
```

Everything under `generator/` works today. Everything else is a documented
stub carrying a `TODO(WP...)` marker explaining what it is for.
