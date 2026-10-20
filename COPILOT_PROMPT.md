# Setup prompt

Paste everything between the lines into GitHub Copilot Chat in **Agent** mode,
with this folder open in VS Code. Delete this file once setup is done.

---

Set up this project. Work through the steps in order and stop if any step
fails rather than continuing.

**1. Extract**

There is a file `chalkboard-scaffold.zip` in this folder. Extract it here. It
contains a single top-level folder `chalkboard/` holding the complete
repository — do not create any folders or files yourself, everything is
already in the archive. Move into `chalkboard/` once extracted.

**2. Read before acting**

Read these three files and follow their rules for everything that follows:
- `.github/copilot-instructions.md` — the project's hard rules
- `README.md` — what the project is
- `ontology/match.yaml` — the data model

**3. Verify the structure**

Confirm these exist, and report anything missing:
- `ontology/` with match.yaml, derived.cypher, significance.cypher
- `src/chalkboard/` with generator, graph, mcp, agents, app
- `tests/` with test_simulator.py, test_loader.py, test_significance.py
- `docs/` with README.md and img/
- `infra/main.bicep`, `data/sample_CBM-00042.jsonl`
- `.github/copilot-instructions.md` and `.github/workflows/ci.yml`
- root: README.md, LICENSE, CODE_OF_CONDUCT.md, pyproject.toml,
  .gitignore, .env.example, run.py

**4. Set up Python and prove it works**

Create a virtual environment, activate it, install with
`pip install -e ".[dev]"`, then run `python -m pytest tests/ -q`.
Expect 10 passing tests. Report the actual result.

**5. Local config**

Copy `.env.example` to `.env`. Leave the values empty — I will fill them in.
Confirm `.env` is matched by `.gitignore` and will not be committed.

**6. Security check before anything is published**

Search the working tree for the strings `github_pat`, `neo4j+s://` and
`NEO4J_PASSWORD=` followed by a value. Report every hit. Do not push if any
real credential is found anywhere.

**7. Publish**

Initialise git, commit everything with the message
"Chalkboard: synthetic event generator, ontology, project scaffold",
set the branch to `main`, add the remote
`https://github.com/AbbasRepo/chalkboard.git` and push.

Then confirm the repository is public. A private repository is not accepted
for this hackathon.

**8. Report**

Tell me: the test result, anything missing from step 3, any security hits,
and the list of files that are stubs with their TODO work package.

Do not implement any of the stubs in this pass. Do not modify
`src/chalkboard/generator/` — it is complete and its tests must keep passing.

---
