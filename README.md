# AI On-Call Engineer

An LLM agent that gets paged like a human SRE: it reads metrics and logs through tools,
forms a hypothesis about the root cause, and posts a diagnosis + suggested remediation
to Slack for a human to approve.

The whole point of this project is to exercise the **five canonical layers** of an LLM
system end-to-end on a problem with an unambiguous eval signal (did the agent name the
right root cause?).

## Two framings, both modeled

### A. Architectural layers (how the system is built)

| Layer | Concern | Where it lives |
| --- | --- | --- |
| 1. Model | API client, model selection, sampling, prompt caching | `agent/model.py` |
| 2. Prompt / context | System prompt, runbook injection, log truncation, context budget | `agent/prompts.py` |
| 3. Tools | Schemas, dispatch, retries, parallel calls | `agent/tools.py` |
| 4. Memory | Scratchpad (working) + past incidents (episodic) | `agent/memory.py` |
| 5. Orchestration | Plan → investigate → critique → propose loop with budget caps | `agent/orchestrator.py` |

### B. Action lifecycle (how a single remediation flows at runtime)

| Stage | What happens | Where it lives |
| --- | --- | --- |
| 1. Intent | Alert fires; orchestrator opens an `incident_id` and audits it | `agent/lifecycle.py::Intent` |
| 2. Propose | Agent calls `propose_action` with structured `action_type` + `action_target` | `agent/tools.py` |
| 3. Consent | A `ConsentBroker` (interactive / auto-low-risk / policy) approves or rejects | `agent/consent.py` |
| 4. Execute | `Executor` runs the action *only* after consent (revert, restart, page, …) | `agent/executor.py` |
| 5. Verify + Audit | `Verifier` re-drives synthetic traffic; every stage is appended to JSONL | `agent/executor.py`, `agent/lifecycle.py::AuditLog` |

The two framings compose: the orchestrator (architectural layer 5) is what
drives a single incident through the five lifecycle stages, and the consent
broker is the security boundary the agent cannot bypass.

Surrounding scaffolding (not part of the agent, but needed for a realistic demo):

- `app/` — the toy "orders" FastAPI service being monitored, with injectable bugs.
- `observability/` — `MetricsStore` / `LogStore` / `DeployStore` interfaces. Ships
  with in-memory implementations; real Prometheus + Loki adapters are stubbed and
  drop in without changing the agent.
- `incidents/` — scripted scenarios with ground-truth root causes.
- `evals/` — benchmark harness that runs every scenario and scores the agent.

## Running

The CLI lives at `oncall_agent.cli`. You need to install the package into your
environment first — otherwise `python -m oncall_agent ...` will fail with
`No module named oncall_agent` unless you happen to be standing in the project
root.

```bash
# From the project root (the directory that contains pyproject.toml):
pip install -e .
export ANTHROPIC_API_KEY=...

# Live demo: run a single incident; you'll be prompted to approve the action.
python -m oncall_agent run memory_leak           # via __main__.py
python -m oncall_agent.cli run memory_leak       # equivalent
oncall-agent run memory_leak                     # console script entry point

# Run unattended with an auto-approve-low-risk policy.
python -m oncall_agent run memory_leak --consent auto-low-risk

# Run the full eval suite (uses auto-low-risk consent + LLM-as-judge scoring).
python -m oncall_agent bench
```

## What an interview talking point sounds like

- "I separated the *tool interface* from the *backing store* so the same agent code
  drives in-memory fixtures during evals and real Prometheus/Loki in production —
  the agent never knows the difference."
- "The orchestrator runs a bounded plan→investigate→critique loop. The critic catches
  about a third of premature conclusions in my eval set."
- "The system prompt and tool schemas are prompt-cached, so a 12-step investigation
  only pays the full system-prompt cost once."
- "Eval is a fixed corpus of 20 incidents with known root causes. I score
  exact-root-cause match, false-cause rate, mean tool calls to resolution, and
  $/incident."

## Status

- [x] Layer interfaces + in-memory observability
- [x] FastAPI orders service with injectable bugs
- [x] Four end-to-end scenarios with ground-truth root causes AND actions
- [x] Agent loop with tool use and prompt caching
- [x] Episodic memory (keyword retrieval over past incidents)
- [x] Eval harness with LLM-as-judge scoring + action-correctness scoring
- [x] Full action lifecycle: intent → propose → consent → execute → verify → audit
- [x] Three consent brokers: interactive, auto-low-risk, policy-allowlist
- [x] Append-only JSONL audit log per incident
- [ ] Real Prometheus + Loki adapters
- [ ] Slack app (currently writes to `slack.jsonl`)
- [ ] Embedding-based episodic memory (currently keyword scoring)
- [ ] 16 more scenarios to reach N=20 for credible eval headlines
