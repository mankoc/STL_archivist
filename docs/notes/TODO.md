# TODO

## Ask the library in plain language

*Feasibility plan of 2026-11-15, broken down into tasks.*

### Goal

A text box where users ask the AI model about the library and get results back:

- **Finding models:** "find me models of batman from stalyn quito" gives a filtered model list in the normal table.
- **Questions about the data** (not limited to searches): "which creator has the most presupported models?", "how
  many models per year since 2022?", "largest archives without images" give a small results table and a one-line
  summary.

**Verdict: feasible.** The server already talks to an AI model (local Qwen3-VL through `llama-server`, OpenRouter, an
OpenAI-compatible URL, or a compute worker), has a query engine (filters, and field search with AND/OR/NOT), and has
a clean view of the models (`models_view`). The two problems that need care are reliable query output from a small
local model, and running model-written SQL safely.

### Design in brief

The model never answers from its own knowledge. It turns the question into a **plan**; the server checks the plan
and runs it.

```json
{"kind": "search", "query": "title=\"batman\" AND creator=\"Stalyn Quito\"", "filter": null, "summary_hint": "Batman models by Stalyn Quito"}
{"kind": "sql", "query": "SELECT creator, count(*) AS n FROM v_models WHERE presupports = 'Yes' GROUP BY creator ORDER BY n DESC LIMIT 10", "summary_hint": "Creators with the most presupported models"}
{"kind": "clarify", "question": "Do you mean the creator Stalyn Quito or the collection Quito?"}
```

| Kind | When | Runs | Result |
|---|---|---|---|
| `search` | Finding models | The existing search syntax (`queries::Search`) plus an optional filter (`FilterSpecDto`, for year, warnings, presupports and the like) | The browser table, with every usual action |
| `sql` | Counts, rankings, comparisons, anything else | One read-only `SELECT` over whitelisted views | A results table and a summary |
| `clarify` | The question is ambiguous | Nothing | The model's question back to the user |

- **Names come from the library.** Before asking the model, the server finds the creators, sculptors, collections,
  subcollections, releases, categories and tags that resemble words in the question (`ai_tagging::relevant_names`,
  `fuzzy::matches`) and lists them in the prompt. "stalyn quito" then resolves to the library's own spelling.
- **Output is constrained.** The request asks for JSON matching the plan schema (`response_format: json_schema`);
  where a provider ignores it, the reply is parsed leniently (the first JSON object in the text).
- **Asking runs as a read-only job** (`ask`, like `ai_check`): the first answer may wait for the local model to start,
  and jobs already have progress, stop and results.

### Constraints the work must keep (from CLAUDE.md)

- Logic in `stl-core`, the server converts JSON; blocking work through `Library::run` or a job.
- The model through `workers.chat`, then `ai.ask` (or the remote providers), never `tools`/llama.cpp directly.
- Every route: `#[utoipa::path]`, an entry in `api::ApiDoc`, a test in `stl-server/tests/`, then `npm run gen:api`
  and `python tools/api_doc.py`. Requests `deny_unknown_fields`.
- One library lock, one process: the read-only connection is opened by the server, never by another process.
- README, changelog (`python tools/version.py note`) and the help topic updated with each step.
- Each phase on its own branch from `development`, a PR, merged once CI passes.

---

## Phase 0: decisions (taken 2026-11-15)

- [x] **0.1 Placement:** its **own *Ask* page** (a hash route in `shell/route.ts`, in the main navigation), with room
      for results tables; `search` answers open in the Library page's table.
- [x] **0.2 Who may ask:** **every role** (read-only; viewers see the same data in the browser).
- [x] **0.3 Default:** **off**. A library setting `ask_enabled` (off) under *Settings → AI*, turned on by an admin; the
      page is hidden while it is off or no AI provider is configured.
- [x] **0.4 Model:** the user **chooses the model for each question** on the Ask page, among the ones the server
      offers (the local model, the configured OpenRouter / OpenAI-compatible model, and any others an admin lists).
      The help and the picker say which choices send the question and matched names off the machine.

## Phase 1: spike and evaluation (2–3 days, go/no-go)

Goal: measure whether the local model produces correct plans before building the feature.

- [ ] **1.1 Evaluation set.** Write `docs/notes/ask-eval.jsonl`: 40–60 real questions, each with the expected plan
      kind and the expected result (model ids or result rows) against a fixture library. Cover misspelled names,
      two-field searches, negation, years, warnings, counts, top-N, group-by, "largest", "without images",
      non-English questions, and ambiguous questions (expected `clarify`).
- [ ] **1.2 Fixture library.** A script or test helper that builds a small library with known creators, sculptors,
      collections, tags, years and warnings (reuse the server test helpers' zip writer), so expected results are
      stable.
- [ ] **1.3 Schema card draft.** List the views and columns the model may use, each with a one-line meaning and
      example values; 5–8 worked examples (question → plan). Measure its size in tokens: target ≤ 2.5k, to fit
      `N_CTX = 8192` with the question and names.
- [ ] **1.4 Name grounding draft.** For a question, the candidate names per kind (cap about 20 per kind) with
      `relevant_names` and `fuzzy::matches`; check that "stalyn quito"-style misspellings reach the right name.
- [ ] **1.5 Runner.** A throwaway binary or ignored test that sends each evaluation question to a model (local
      Qwen3-VL-4B, Qwen2.5-VL-3B, and one remote model), with and without `json_schema`, and records: valid JSON,
      correct kind, plan runs, correct result, latency.
- [ ] **1.6 Go/no-go.** Local model: ≥ 80 % correct `search` plans and ≥ 60 % correct `sql` plans, valid JSON
      ≥ 95 % with `json_schema`. Remote model: ≥ 90 % / 80 %. Below that: ship `search` only (phase 3) and
      recommend a remote model for `sql`, or stop.
- [ ] **1.7 Write down the results** in this file (scores per model, typical failures, prompt changes made), and
      adjust the schema card and examples before phase 2.

## Phase 2: core (`stl-core`, new module `ask`)

Pure logic, testable without a model. Branch `feature/ask-core`.

- [ ] **2.1 Plan types.** `ask::Plan` (`Search { query, filter }`, `Sql { query }`, `Clarify { question }`) with
      `summary_hint`; serde with `deny_unknown_fields`. `ask::plan_schema()` returns the JSON schema sent as
      `response_format`.
- [ ] **2.2 Plan parsing.** `ask::parse_plan(reply) -> Result<Plan, String>`: strict JSON first, then the first
      `{…}` object in the text (models that wrap JSON in prose or code fences). Unit tests with good, wrapped,
      truncated and invalid replies.
- [ ] **2.3 Name grounding.** `ask::candidate_names(conn, question) -> Vec<(kind, Vec<String>)>`: library names per
      kind that resemble the question's words, capped per kind. Unit tests on a sample library: misspellings,
      accents, multi-word names, no match.
- [ ] **2.4 Query views.** Curated, documented views used by `sql` plans, added to `schema_sql.rs` as views (no new
      tables, so the data directory format is unchanged and older data directories get them through `migrate`):
      - `v_models`: the user-facing columns of `models_view` (no `archive_hash`, no internal ids except `id`).
      - `v_model_tags (model_id, tag)`, `v_model_scales (model_id, scale)`, `v_model_files (model_id, filename,
        size)`, `v_model_images (model_id, image_count)`.
      Decide whether paths are shown as absolute (`archive_path`) or library-relative.
- [ ] **2.5 Schema card.** `ask::schema_card()`: built from one list of `(view, column, meaning, example)` so the
      card, the authorizer whitelist and the docs cannot drift apart. Unit test: every listed column exists
      (query `pragma_table_info`).
- [ ] **2.6 Prompt.** `ask::prompt(question, names, card, examples) -> String`, with the rules: prefer `search`,
      use the listed names' exact spelling, one statement, no `LIMIT` above the cap, `clarify` when unsure. Unit
      test on the assembled text (stable snapshot).
- [ ] **2.7 Search plan check.** Parse `query` with `search_query::parse` (unknown fields refused) and convert
      `filter` through the existing filter validation; errors come back as text for the retry.
- [ ] **2.8 Read-only SQL runner.** `ask::run_sql(db_path, sql, limits) -> Result<Rows, String>`:
      - Open a **separate** connection with `OpenFlags::SQLITE_OPEN_READ_ONLY`, then `PRAGMA query_only = ON`;
        register `path_join`/`fuzzy_match` (`add_path_functions`) so views work.
      - Exactly one statement: prepare it and refuse leftover text (`Statement` tail) and anything that is not a
        `SELECT` or `WITH … SELECT`.
      - `Connection::authorizer`: allow `Select`, `Read` on whitelisted views (and the tables those views read
        from, as SQLite reports reads through views), `Function`; deny everything else (`Attach`, `Pragma`,
        `Insert`, `Update`, `Delete`, `CreateTemp*`, `Transaction`, `Savepoint`, …).
      - `progress_handler`: abort after about 2 s (a clock check every N steps); map the interrupt to "the query
        took too long".
      - Row cap (about 500) enforced while stepping, a column cap (about 20), and a cell cap (long text cut).
      - Return column names, rows as JSON values, and whether rows were cut.
- [ ] **2.9 Security tests** for 2.8: `INSERT`, `UPDATE`, `DELETE`, `DROP`, `CREATE TEMP TABLE`, `ATTACH`,
      `PRAGMA writable_schema`, `VACUUM INTO`, two statements separated by `;`, comments hiding a second statement,
      a recursive CTE that never ends (timeout), a huge cross join (timeout or cap), reading `models` directly if it
      is not whitelisted, `load_extension`. Each must fail without changing the database (compare a checksum before
      and after).
- [ ] **2.10 Plan execution.** `ask::run(conn/db_path, plan) -> Answer` (`Models { ids }` for `search` through
      `queries::table_ids`, `Rows { columns, rows, cut }` for `sql`, `Clarify { question }`).

## Phase 3: model call and job (`stl-server`)

Branch `feature/ask-job` (or together with phase 4 for the `search`-only first release).

- [ ] **3.1 Structured output in the AI client.** Extend `ai::Prompt` with an optional JSON schema; `complete()`
      sends `response_format: {"type": "json_schema", "json_schema": {…}}` when given. Check what each provider
      accepts: `llama-server` (local and on workers), OpenRouter (per model), OpenAI-compatible URLs; keep the
      lenient parser (2.2) for providers that ignore it.
- [ ] **3.2 Compute workers.** Add the optional schema to `ToWorker::Chat` (`workers.rs`) and pass it on in
      `worker.rs`. Keep the wire compatible: an older worker ignores the field (serde default), and the lenient
      parser covers its plain replies. Test in `tests/workers.rs`.
- [ ] **3.3 Model choices.** `GET /api/ask/models`: the models a question may go to, each with an id, a label,
      its provider (`local`, `worker`, `openrouter`, `openai`) and whether it sends data off the machine. Built from
      the configured AI provider (the local model when installed or a worker offers chat, the configured OpenRouter /
      OpenAI-compatible model) plus an admin-edited list `ask_models` in the library settings (extra OpenRouter or
      OpenAI-compatible model names). The client never names a URL or key: it picks an id from this list.
- [ ] **3.4 Calling a chosen model.** Split `ai::ask_model` so the provider and model can be given per call instead
      of read from the library settings (`Provider::of`), reusing `ask_openrouter` / `ask_openai` / workers / local.
      The tagger keeps using the library's provider unchanged.
- [ ] **3.5 Job `ask`.** A `JobRequest::Ask { question, model }` variant (`model`: an id from 3.3; none: the
      library's provider): `kind()` = `"ask"`, listed in `read_only`, checks in `validate` (non-empty, length cap
      such as 500 characters, the feature enabled, the model id offered). The body: ground names, build the prompt,
      ask the chosen model, parse and check the plan, on a parse or check error ask **once more** with the error
      appended, then run it.
- [ ] **3.6 Job result.** `JobResult::Ask { plan, answer, summary }` in `jobs/result.rs`; `summary` from
      `summary_hint` plus the result size ("12 models", "10 rows"), not a second model call (keeps it fast; an
      optional second call to phrase the answer can come in phase 6).
- [ ] **3.7 Logging.** `lib.job_log` records the question, the model, the plan, retries and timing (not the rows),
      so failures can be studied and added to the evaluation set.
- [ ] **3.8 Settings.** `ask_enabled` (default **off**) and `ask_models` (extra model names) in `library_settings.rs`
      and `api/settings.rs` (admins; validated), shown in *Settings → AI*.
- [ ] **3.9 Server tests** (`stl-server/tests/ask.rs`) with a fake model (the tests' existing AI stub that returns
      canned replies): a `search` plan returns the right ids; an `sql` plan returns rows; a plan that fails the check
      triggers one retry; a write attempt is refused; a viewer may ask; the feature off (the default) refuses the
      job; a model id not offered is refused; `/api/ask/models` lists the configured choices and marks remote ones.
- [ ] **3.10** `npm run gen:api`, `python tools/api_doc.py`.

## Phase 4: web client

- [ ] **4.1 Ask page.** `web/src/ask/AskPage.tsx` on its own hash route (`shell/route.ts`), in the main navigation:
      a text area (Enter sends, Shift+Enter new line), the model picker (4.2), the job's progress (*Starting the
      model…*, *Thinking…*), Stop, and the answer below; earlier answers of the session stay listed above it.
- [ ] **4.2 Model picker.** A select filled from `/api/ask/models`, remembering the last choice per user
      (`/api/me/settings`); remote choices marked (a cloud icon and "sends the question to …").
- [ ] **4.3 `search` answers.** The summary, the number of models, the first few as a short list, and *Show in
      Library*: opens the Library page with the plan's query in the search box and its filter in the filter pane (so
      the user can adjust it).
- [ ] **4.4 `sql` answers.** A compact results table (Mantine `Table`, sticky header, scroll), a "first 500 rows"
      note when cut, and *Copy as CSV*.
- [ ] **4.5 `clarify` answers.** The model's question, with the box ready for the reply (the reply is sent as a new
      question including the previous one).
- [ ] **4.6 Show query.** A toggle that shows the plan's query (search syntax or SQL) in a code block.
- [ ] **4.7 Errors.** Clear messages for: the feature off (admins get a link to *Settings → AI*), model failed to
      start, query refused, query too slow, empty result ("No models match: try fewer words").
- [ ] **4.8 Navigation.** The *Ask* entry is shown only when `ask_enabled` is on and at least one model is offered;
      a direct link to the route says why it is unavailable otherwise.
- [ ] **4.9 Settings UI.** *Settings → AI*: the `ask_enabled` switch and the `ask_models` list (admins).
- [ ] **4.10 Pure helpers and tests.** `web/src/ask/ask.ts` (answer → view model, CSV, the Library route for a
      `search` plan, model labels) with `ask.test.ts`.
- [ ] **4.11 Help, README, changelog.** A help topic (what it can answer, examples, choosing a model and which choices
      send data off the machine, "show query"), a README line, the `## Unreleased` note.

**First release possible here** with `search` plans only (hide `sql` behind the setting until phase 5 is done), if
phase 1 showed `sql` too weak locally.

## Phase 5: harden the `sql` path

- [ ] **5.1 Run the evaluation set again** through the real job (not the spike runner) for every provider; record
      the scores in this file.
- [ ] **5.2 Prompt tuning** from the failures: more examples for the weak question types, clearer column meanings,
      the values of small enumerations (`presupports`, warning lines) in the card.
- [ ] **5.3 Model ids in `sql` results.** When a result has an `id` column, offer *Show these models in the table*
      (by ids) so the usual actions (tag, edit, open) work on them.
- [ ] **5.4 Resource limits under load.** Two users asking at once: jobs queue as usual; confirm the read-only
      connection and its timeout do not block crawls or edits (WAL readers do not block writers).
- [ ] **5.5 Security review** of the runner and the prompt path (`/security-review`), fix findings.

## Phase 6: polish (optional)

- [ ] **6.1** Recent questions kept per user in `/api/me/settings`, shown under the box.
- [ ] **6.2** *Save as filter* for `search` answers (if saved filters exist), or a shareable link (hash route with
      the query).
- [ ] **6.3** An optional second model call that phrases the answer in a sentence from the rows (off by default:
      slower).
- [ ] **6.4** Follow-up questions in context ("only the presupported ones"): send the previous plan with the new
      question.
- [ ] **6.5** Feedback buttons (👍 / 👎) that write the question and plan to the job log, to grow the evaluation set.

---

## Risks and open questions

- **Local model quality** is the main risk: phase 1 measures it before any real work. Fallback: `search` only
  locally, `sql` with a remote model.
- **Speed:** a local 4B model takes about 2–8 s per answer on CPU, plus the start on first use. The job's progress
  covers it; with no AI configured the box is hidden and the search syntax remains.
- **Providers and `json_schema`:** support varies (OpenRouter per model). The lenient parser and the one retry cover
  the gap; phase 1 measures how often it is needed.
- **Views and compatibility:** new views only (no tables or columns), so older data directories open unchanged and
  sync and backups are unaffected (views live in the schema, created by `migrate`).
- **Privacy:** with a remote provider, the question and the matched library names leave the machine. Say so in the
  setting and the help.
- **Language:** non-English questions work with Qwen; names are matched by spelling, not translated.

## Estimate

| Phase | Effort |
|---|---|
| 0. Decisions | under an hour |
| 1. Spike and evaluation | 2–3 days |
| 2. Core | 4–5 days |
| 3. Model call, model choices and job | 3–4 days |
| 4. Ask page, model picker and settings | 4–5 days |
| 5. Harden `sql` | 2–3 days |
| 6. Polish | optional, 2–4 days |

About **3.5–4.5 weeks** to phase 5; a `search`-only first release after phases 1–4 at about **2.5–3 weeks**.
