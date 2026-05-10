# Dimp autonomous watchlist dogfood runbook

`Dimp` is the first always-on dogfood machine for last30days watchlists.

The goal is not to run a noisy alert bot. The goal is to prove that last30days can operate as a 24/7 current-intelligence loop:

1. watch selected topics,
2. run only when topics are due,
3. preserve per-run sightings,
4. produce deltas,
5. summarize meaningful changes,
6. escalate only when a human or agent should act.

## Operating principles

- **File/JSON first.** Start with local JSON and compact briefs before any push notifications.
- **No raw secrets in output.** Never log API keys, cookies, auth tokens, or `.env` values.
- **Autonomy by small loops.** Prefer `due` and `run-due` under an external scheduler before a long-lived daemon.
- **Deltas over repeats.** A successful watchlist run should answer what changed, not merely reprint current search results.
- **Quiet by default.** Escalation should be a later explicit rule, not every run.

## Pre-flight checklist

Before running Dimp continuously:

- OpenClaw is installed and healthy on the machine.
- The last30days skill/repo is installed from the intended branch or release.
- Python runtime satisfies the project requirement (`>=3.12`).
- Source credentials are configured using env-based patterns; no credentials are checked into files.
- Database path is known. Default: `~/.local/share/last30days/research.db`.
- Log/artifact directory is chosen.
- Daily budget is configured.
- Delivery is either unset or points to a safe test endpoint.

Useful checks:

```bash
python3 --version
python skills/last30days/scripts/watchlist.py list
python skills/last30days/scripts/watchlist.py due
```

## Seed topics

Initial topics should exercise the intended use cases and known weak spots:

```bash
python skills/last30days/scripts/watchlist.py add "AI coding agents" --weekly
python skills/last30days/scripts/watchlist.py add "OpenAI Codex CLI" --weekly
python skills/last30days/scripts/watchlist.py add "Claude Code plugins" --weekly
python skills/last30days/scripts/watchlist.py add "Hermes Agent" --weekly
python skills/last30days/scripts/watchlist.py add "OpenClaw" --weekly
python skills/last30days/scripts/watchlist.py add "mvanhorn/last30days-skill" --weekly
```

Use malformed/local-path topics only as quality probes, not normal watchlist entries:

```bash
python skills/last30days/scripts/watchlist.py add "/repo/subreddit" --weekly
python skills/last30days/scripts/watchlist.py add "/Documents/Last30Days" --weekly
```

## Budget and delivery

Set a conservative budget during dogfood:

```bash
python skills/last30days/scripts/watchlist.py config budget 5.00
```

Leave delivery disabled at first:

```bash
python skills/last30days/scripts/watchlist.py config delivery ""
```

Only enable delivery after compact briefs are judged useful and non-noisy.

## Manual dogfood loop

Run this manually until the behavior is understood:

```bash
python skills/last30days/scripts/watchlist.py due
python skills/last30days/scripts/watchlist.py run-due
python skills/last30days/scripts/watchlist.py delta "AI coding agents" --emit compact
```

Expected behavior:

- `due` lists enabled topics that have never run or have passed their schedule.
- `run-due` runs only due topics and respects daily budget.
- `delta TOPIC` compares the latest two completed runs using the sightings ledger.
- `delta TOPIC --emit compact` produces a readable operator brief.

## OpenClaw scheduling pattern

Use OpenClaw or a system scheduler to invoke `run-due` periodically. The command itself decides whether work is actually due.

Recommended first cadence:

```bash
python skills/last30days/scripts/watchlist.py run-due >> ~/.local/share/last30days/dimp-run-due.log 2>&1
```

Do not start with a persistent daemon. A scheduler-driven command is easier to restart, inspect, and debug.

## Operator note format

Dimp should eventually produce a daily or weekly operator note shaped like:

```markdown
# Dimp watchlist brief

## Escalated
- Topic: ...
- Why it matters: ...
- Suggested next action: ...

## Routine deltas
- Topic: ...
- New: ... / Continued: ... / Dropped: ...

## Source/run issues
- Topic: ...
- Failure/degradation: ...

## Suggested agent handoffs
- Topic: ...
- Task bundle: ...
```

This note should come from deterministic watchlist state first. LLM synthesis and agent handoff can be layered later.

## Failure modes to track

When dogfooding, record these as issues or follow-up PRs:

- Topic is due too often or not often enough.
- `run-due` reruns a topic while a previous run is still marked `running`.
- Delta is empty but the raw run clearly found important new evidence.
- `dropped` findings are misleading because a source failed.
- Compact brief is too noisy.
- Source credentials fail silently.
- Budget skips are unclear.
- Local/path-like topics such as `/repo/subreddit` or `/Documents/Last30Days` produce overconfident output.

## Next build targets

After Dimp can run `due`, `run-due`, and compact deltas reliably:

1. topic dossier artifacts,
2. escalation scoring,
3. quality notes for deltas,
4. portable OpenClaw/Hermes handoff bundles,
5. persistent daemon mode if scheduler-driven autonomy is insufficient.
