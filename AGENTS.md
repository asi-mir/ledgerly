# AGENTS.md

The contract for every coding agent in this repo (Claude Code, Codex, Copilot, Cursor).
It is a map, not a manual: follow the links for detail.

## What this is

**Ledgerly** is an open-source Telegram bot for people who are too lazy to track
their spending. You send a photo of a receipt. An open-weight model (Gemma) reads it and
files every line in a ledger. You tell the bot about upcoming birthdays and family
dinners in plain chat. TabPFN reads the ledger and those events and tells you the day
you will run out of budget, before it happens. Everyone deploys their own copy.

**Deadline: Mon 2026-10-26 09:59 Istanbul (+03).**

## Map (planned layout)

| You need | Go to |
|---|---|
| What the bot does, how to deploy it | `README.md` |
| The bot (FastAPI webhook, Python 3.12) | `app/` |
| Receipt rules given to the model | `prompts/receipt_rules.md` |
| Forecast backtest and import scripts | `scripts/` |
| Synthetic sample data | `samples/` |
| Environment variables (names only) | `.env.example` |
| Render Blueprint | `render.yaml` |

## Commands (planned)

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest                         # must pass before every PR
.venv/bin/uvicorn app.main:app --reload  # http://localhost:8000
```

## Hard rules

1. **Open core.** Reading receipts and understanding chat runs on Gemma only (Google AI
   Studio or a local Ollama). Forecasting runs on TabPFN. Never add a closed LLM (GPT,
   Claude, Gemini) to the product runtime. Coding assistants that *build* the project
   are fine.
2. **Money safety.** The ledger is the truth; monthly totals and forecasts are rebuilt
   from it, never edited. A receipt is filed only when its items add up to its total.
   Never guess a date: ask the user in chat.
3. **No invented numbers.** Every accuracy number in the README comes from
   `scripts/backtest.py` output. Amounts shown in examples are made up and say so.
4. **Personal data.** Never commit real receipts, a real ledger, names, birthdays or
   bank details. `samples/` holds synthetic data only. The server stores Telegram file
   IDs, not images.
5. **Secrets.** Never commit a secret. Values live in `.env` and in the hosting dashboard.
   `.env.example` holds names only. Never print a secret value; check one exists with
   `grep -c '^NAME=.\+' .env`.
6. **Owner lock.** The bot answers only Telegram user IDs listed in
   `ALLOWED_TELEGRAM_IDS`. Every webhook call checks Telegram's secret token header.
7. **Git.** One branch per change. Merge into `main` with a fast-forward only after `pytest`
   passes, then push. Never force-push. `main` must always run.
8. **Challenge rule.** Commits made after the deadline must be listed in `README.md`.
9. **Deploys.** Agents deploy only through the Render REST API with the key in `.env`
   (`RENDER_API_KEY`), and only on **free** plans: never create or upgrade a paid resource.
   Validate `render.yaml` with the API before creating anything. No deployment CLI, and
   never create Telegram bots or other accounts.

## How to work

1. Branch: `feat/<slug>` (or `fix/`, `docs/`, `chore/`).
2. Run `pytest` before you commit.
3. Commit messages and PR titles say what changed, in plain words, in Conventional
   Commits, e.g. `feat(receipt): Gemma reader with total check`. No co-author or tool
   lines: no `Co-Authored-By:` for Claude or any AI tool, no "Generated with …".

## When unsure

Ask one clear question. Do not guess scope or names.
