# AI Visibility Tracker

Measures how a brand shows up in AI-generated answers. This is the core of
**Answer Engine Optimization (AEO)**. The tool:

1. sends buyer-style questions ("best coworking space in Bangalore for a 10-person team?")
   to **Perplexity, ChatGPT (OpenAI), Claude and Gemini**, each with web search turned on
2. records whether your brand is mentioned, how prominently, which competitors appear,
   and which websites the engines cite
3. produces a self-contained **HTML report** with charts, 3 key findings, 3 recommendations,
   and the list of "opportunity gap" queries where competitors appear and you don't

## Setup

Requires Python 3.11 or newer.

```bash
cd "myHQ Agent"
py -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # macOS/Linux: cp .env.example .env
```

### Adding API keys

Open `.env` and paste in the keys you have:

```
PERPLEXITY_API_KEY=pplx-...     # https://www.perplexity.ai/account/api
OPENAI_API_KEY=sk-...           # https://platform.openai.com/api-keys
ANTHROPIC_API_KEY=sk-ant-...    # https://platform.claude.com/settings/keys
GEMINI_API_KEY=...              # https://aistudio.google.com/apikey
```

Engines without a key are **skipped with a warning**, so you can add keys one at a
time. `py AI_Visibility.py status` shows which keys are picked up. `.env` is git-ignored.

Helper tasks (query generation, sentiment, report summary) use a **cheap model**. The
tool picks the first one you have a key for: Perplexity → OpenAI → Claude → Gemini
(see `utility_llm` in `config/settings.yaml`).

## Usage

```bash
# 1. Preview: how many calls and roughly what it will cost. Sends nothing.
py AI_Visibility.py run --dry-run

# 2. Try it cheaply: first 2 queries, one engine
py AI_Visibility.py run --max-queries 2 --engines perplexity

# 3. Full run. Already-answered queries come from the cache (free).
py AI_Visibility.py run

# 4. Analyse and build the HTML report (reports/output/myhq_report_<date>.html)
py AI_Visibility.py report
```

Other commands:

```bash
py AI_Visibility.py analyze                    # metrics in the terminal (+ reports/output/myhq_metrics.json)
py AI_Visibility.py generate-queries --count 18   # add 18 realistic buyer queries to the config
py AI_Visibility.py run --refresh              # force fresh answers (costs money; old ones are kept)
py AI_Visibility.py report --no-sentiment --no-llm-summary   # zero-cost report
py AI_Visibility.py status                     # keys, models, all-time spend
py AI_Visibility.py export                     # CSV of every response and citation
```

`--project myhq` is the default. Pass `--project <name>` for other configs in `config/projects/`.

### Cost tracking

Every run prints the **actual cost per engine**, and `status` shows the all-time total.

- **Perplexity** reports the exact cost of every call, and that's what we store.
- **OpenAI, Claude, Gemini** don't return a cost. We compute it from the tokens and
  search calls they report, using the prices in `config/settings.yaml` (marked `*`).

Typical cost per query (estimates in `settings.yaml`): Perplexity ~$0.005, Gemini
~$0.05, OpenAI ~$0.05, Claude Opus ~$0.11. So 20 queries × 4 engines ≈ $4. Switch
Claude to `claude-sonnet-5-5` in settings to roughly halve its cost.

## Configuring a project

Each project is one YAML file in `config/projects/`. Copy `myhq.yaml` to start a new one.

| Key           | Meaning |
|---------------|---------|
| `brand`       | Your brand `name`, `aliases` (every spelling that counts as a mention) and `domains` (your websites) |
| `category`    | What buyers are shopping for, e.g. "coworking spaces in Bangalore" |
| `location`    | Optional city/region/country/timezone/lat-long. Each engine uses the fields it supports |
| `competitors` | Known competitors, each with aliases and (optional) domains |
| `queries`     | The buyer-intent questions to ask. Edit freely |

Aliases are matched **case-insensitively as whole words**. Avoid aliases that are
ordinary words (e.g. "We Work" would match "where we work").

Engine models, endpoints and prices live in `config/settings.yaml`, because they change
often. Change a model there and the cache treats that engine's old answers as stale.

## Metrics

| Metric | Meaning |
|--------|---------|
| **Mention rate** | % of answers that name your brand (any alias) |
| **Average position** | When named, your rank among all tracked brands in that answer, by order of first mention (1 = named first) |
| **First-mention rate** | % of all answers where you're the first brand named |
| **Share of voice** | Your mentions ÷ mentions of all tracked brands (you + competitors) |
| **Sentiment** | Whether the sentences naming you are positive, neutral or negative (cheap model; only those sentences are sent) |
| **Own-site citation rate** | % of answers citing one of your `domains` as a source |
| **Top cited domains** | Websites engines cite most (each answer counts a domain once). These are the sites to get featured on |
| **Opportunity gaps** | Queries where at least one engine names competitors but not you. These are the highest-priority fixes |

Analysis uses the **latest answer** for each (engine, query) currently in the config.
Mentions are computed fresh every time (free), so alias edits apply immediately.
Sentiment scores are cached in SQLite, so they're paid for once.

## How it works

```
AI_Visibility.py            CLI (typer): run, analyze, report, generate-queries, status, export
config/
  settings.yaml             engine models, endpoints, prices, cheap utility model
  projects/myhq.yaml        brand, competitors, location, queries
engines/
  base.py                   Engine interface + EngineResponse/Citation shapes, retries
  perplexity.py             Perplexity Agent API + web_search tool
  openai_engine.py          OpenAI Responses API + web_search tool
  claude_engine.py          Anthropic SDK + web_search server tool
  gemini_engine.py          Gemini generateContent + Google Search grounding
core/
  config.py                 YAML → typed objects
  runner.py                 plans calls (cache check), runs them concurrently
  storage.py                SQLite: runs, responses (raw JSON), citations, sentiments; CSV export
  query_generator.py        cheap-model query generation
analysis/
  mentions.py               alias matching, mention order, snippets, domain ownership
  sentiment.py              cheap-model sentiment scoring with cache
  metrics.py                per-engine + overall metrics, share of voice, domains, gaps
  pipeline.py               runs the three above in order
reports/
  summary.py                3 findings + 3 recommendations (rule-based, or LLM-worded)
  html_report.py            self-contained HTML report (pure HTML/CSS, light + dark)
  output/                   generated reports (git-ignored)
data/
  myhq.db, exports/         SQLite database and CSV exports (git-ignored)
tests/                      offline tests, run with `py -m pytest -q`
```

**Design choices worth knowing:**

- **One interface, many engines.** Each provider converts its own response format into the
  same `EngineResponse` (answer text, citations, tokens, cost, raw JSON). Nothing downstream
  knows which provider an answer came from. Adding an engine = one new file + one registry line.
- **Caching.** Each answer is stored under a hash of *engine + model + query + search
  settings*. Re-runs skip what's already answered unless you pass `--refresh`. Old answers
  are never deleted, so you can track changes over time.
- **Raw responses are kept.** The full JSON from every call is stored, so analysis can
  be improved and re-run without paying for queries again.
- **Cited vs retrieved sources.** Engines often retrieve 10+ pages but cite only a few.
  We store both and flag which are actually cited (`cited_in_text`). Domain metrics use
  cited sources, falling back to retrieved ones if an engine marks none.
- **Deterministic first, LLM second.** Mention detection and all metrics are plain code
  (free, repeatable, explainable). LLMs are only used where judgement is needed
  (sentiment, the wording of the summary), and the summary falls back to rules if no key is set.
- **Failure handling.** Rate limits and server errors are retried with exponential backoff.
  Bad keys/parameters fail fast with the API's error message. A failed query is reported
  and not cached, so the next run retries it.

### Engines

| Engine     | API | Citations come from |
|------------|-----|---------------------|
| Perplexity | Agent API `/v1/agent`, model `perplexity/sonar`, `web_search` tool | `search_results` output item; `[web:N]` markers mark cited ones |
| OpenAI     | Responses API, `web_search` tool | `url_citation` annotations (cited) + `web_search_call.action.sources` (retrieved) |
| Claude     | Messages API via `anthropic` SDK, `web_search_20260209` tool | `web_search_result_location` citations on text blocks (cited) + `web_search_tool_result` (retrieved) |
| Gemini     | `generateContent` with `google_search` grounding | `groundingChunks`; `groundingSupports` mark cited ones. Google redirect links are resolved to real URLs |

Notes:
- Perplexity ended support for its older Sonar Chat Completions API (`/v1/sonar`)
  on 27 Sep 2026. This tool uses the replacement, the Agent API.
- Claude runs with server-side `fallbacks: default`: if a safety classifier declines,
  the API retries on a fallback model within the same call. Remove it in settings to disable.

## Caveat

API answers can differ from what people see in the consumer apps (ChatGPT, Perplexity,
Claude, Gemini). Consumer apps add personalisation, memory, location and sometimes
different models or system prompts. Treat these results as a consistent, repeatable
**proxy** for AI visibility, not an exact replica. The report states this too.

## Known untested points (no live keys yet)

The request and response formats follow each provider's docs as of Nov 2026 and are
covered by offline tests with documented response shapes. On the first live run, check:

- Perplexity accepts `model: perplexity/sonar` together with the `web_search` tool and `user_location`
- Gemini accepts `toolConfig.retrievalConfig.latLng` alongside `google_search` (delete
  `latitude`/`longitude` from the project config if it returns a 400)
- The "cited" count printed per answer is above 0 for each engine
- Competitor `domains` in `myhq.yaml` are best guesses. Verify them
