# 📈 Agentic Stock Monitor: Nifty Large & Mid Cap

A Python application that monitors the **Nifty 100 + Nifty Midcap 150** (~250 stocks)
every trading day. It screens them on fundamentals (EPS, P/E, quarterly results, ROE,
debt and more), has a team of **AI agents** research the best candidates, and **emails**
you a long-term investment report.

It doubles as a hands-on course in **agentic AI** and **Python**. The agent loop is
written from scratch (no framework) so you can see exactly how it works.

> ⚠️ This is software for personal research and learning, not investment advice.

---

## How it works

```
                        ┌───────────────────────── plain Python (free, fast) ─────────────────────────┐
  NSE index lists ──►   │ ① Universe ──► ② Data (Yahoo Finance) ──► ③ Screener: score 0-100 ──► ④ Shortlist │
                        └──────────────────────────────────────────────────────────────┬──────────────┘
                                                                                       │ top scorers + your
                                                                                       │ holdings + big movers
                        ┌──────────────────────── AI agents (Claude) ─────────────────▼──────────────┐
                        │ ⑤ Research Analyst ── uses 7 tools ──► verdict (BUY/HOLD/AVOID…)             │
                        │ ⑥ Risk Reviewer   ── re-checks data ──► challenges + FINAL decision         │
                        │ ⑦ Chief Strategist ── sees everything ──► executive brief                   │
                        └──────────────────────────────────────────────────────────────┬──────────────┘
                                                                                       ▼
                         SQLite memory (history of scores & verdicts)     ⑧ HTML report + CSV ──► 📧 Gmail
```

**Screener pillars** (`analysis/scoring.py`): Quality 25 (ROE, margins) · Growth 25
(revenue, earnings, quarterly EPS YoY, EPS streak) · Valuation 20 (P/E vs sector median,
PEG, P/B for banks) · Financial health 15 (debt/equity, current ratio, free cash flow; ROA
for banks) · Momentum 15 (200-DMA, 6-month return, RSI). Red flags such as loss-making
results, falling EPS, shrinking margins, high debt or an extreme P/E each cost 3 points.

**Agent tools** (`agents/tools.py`): `get_fundamentals`, `get_quarterly_results`,
`get_price_technicals`, `get_screening_score`, `compare_with_sector_peers`,
`get_history_from_memory`, `get_recent_news`.

---

## Setup (one time)

```bash
cd stock_agent
python3 -m venv .venv                     # already done for you
.venv/bin/pip install -r requirements.txt # already done for you
cp .env.example .env                      # then edit .env
```

In `.env` set:
1. `ANTHROPIC_API_KEY`: create one at https://console.anthropic.com → API Keys.
2. `EMAIL_SENDER`, `EMAIL_APP_PASSWORD`, `EMAIL_RECIPIENT`: turn on 2-Step Verification
   for Gmail, then create an App Password at https://myaccount.google.com/apppasswords.
3. `MY_HOLDINGS` (optional): stocks you own, e.g. `TCS,HDFCBANK,POLYCAB`.

## Customise how the agents think (no Python needed)

The agents' instructions are plain markdown files in `stock_agent/prompts/`:

| File | Used by | What it controls |
|------|---------|------------------|
| `investment_policy.md` | **all agents** | Your horizon, risk appetite, quality bars, hard avoids, portfolio limits, buy/sell rules |
| `analyst.md` | Research Analyst | Research checklist and decision scale |
| `reviewer.md` | Risk Reviewer | What to verify and challenge |
| `strategist.md` | Chief Strategist | What the daily brief covers |

Each agent's system prompt is its role file plus the shared policy
(`agents/prompts.py`). Edits take effect on the next run. Start with
`investment_policy.md`: the defaults suit a moderate-risk investor and are meant to be changed.

## Running

```bash
source .venv/bin/activate

python main.py --no-llm --no-email        # free smoke test, no API key needed
python main.py --symbols TCS --no-email   # watch the agents research one stock (~$0.30)
python main.py --limit 40 --top 2         # cheap trial on 40 stocks, emails you
python main.py                            # the full daily run
```

Open `reports/report_<date>.html` in a browser to see the report.

## Daily schedule

```bash
./scripts/install_schedule.sh     # start
./scripts/uninstall_schedule.sh   # stop
```

macOS `launchd` wakes the app every hour. The app runs only on weekdays after
**18:30 IST** (after market close and once results are out) and only once per day.
This works from any timezone and catches up if your Mac was asleep. The Mac must be
on, but it can be locked.

## Cost

Screening is free. Each researched stock costs about 2 agent loops of 3-8 Claude calls.
With the defaults (Claude Opus 5.5, effort `medium`, ~8-11 stocks/day) a run costs roughly
**$1-4** and the email shows the actual cost. To cut cost, lower `TOP_N_TO_RESEARCH`, set
`AGENT_EFFORT=low`, or use `ANTHROPIC_MODEL=claude-sonnet-5-5`.

---

## 🎓 Learning path: read the code in this order

| # | File | What you learn |
|---|------|----------------|
| 1 | `stock_agent/agents/base_agent.py` | **The agent loop.** Model → tool calls → results → repeat |
| 2 | `stock_agent/agents/tools.py` | How tools are described (JSON Schema) and executed |
| 3 | `stock_agent/agents/llm.py` | The Claude API call: system prompt, thinking, effort, caching |
| 4 | `stock_agent/prompts/*.md` + `agents/prompts.py` | Grounding files: instructions kept outside the code |
| 4b | `stock_agent/agents/research_agent.py` | The output schema (finish tool) and the task message |
| 5 | `stock_agent/agents/risk_agent.py` | Multi-agent pattern: a critic checks the analyst |
| 6 | `stock_agent/agents/strategist_agent.py` | An "agent" with no tools ≈ a structured LLM call |
| 7 | `stock_agent/orchestrator.py` | Orchestrator-workers: code controls flow, agents judge |
| 8 | `stock_agent/analysis/scoring.py` | Rules-based screening, and why not everything needs AI |
| 9 | `stock_agent/storage/db.py` | Giving agents long-term memory with SQLite |
| 10 | `stock_agent/data/market_data.py` | Fetching data, caching, threads for parallel downloads |

**Watch an agent think:** after a run, open `logs/<date>/research_analyst_<SYMBOL>.json`.
It is the full conversation: every tool the agent chose to call, the data it got back,
and its final verdict.

## Project layout

```
main.py                     CLI entry point (+ schedule gate)
stock_agent/
  config.py                 all settings (.env overrides)
  orchestrator.py           the daily pipeline
  data/universe.py          Nifty 100 + Midcap 150 lists from NSE
  data/market_data.py       Yahoo Finance download + daily cache
  analysis/metrics.py       technicals (RSI, DMA, returns) + quarterly growth
  analysis/scoring.py       0-100 screener + red flags
  prompts/                  investment_policy.md + one .md per agent role (edit these!)
  agents/                   llm.py · base_agent.py · tools.py · prompts.py · 3 agents
  storage/db.py             SQLite memory
  reporting/report_builder.py   HTML report + CSV
  notify/emailer.py         Gmail SMTP
scripts/                    launchd install / uninstall
```

## Known data limitations
- Yahoo Finance data is free but imperfect. Some companies report in USD (e.g. Infosys),
  and sometimes quarterly results stop updating. The app detects both and warns the agents.
- "Promoter holding" uses Yahoo's insider-holding estimate. Check pledging and
  shareholding patterns on NSE or BSE before investing.
