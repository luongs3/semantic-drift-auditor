# semantic-drift-auditor

**The failure mode with no error message.**

Someone edits what a metric *means* — "Order Total" now excludes shipping and tax. Nothing
breaks. No pipeline fails, no test goes red, no column disappears. Every dashboard built on
that term keeps rendering exactly as before, and quietly starts answering a different
question than it did last quarter. Nobody is told.

`semantic-drift-auditor` is an agent that walks a DataHub glossary term's revision history,
works out whether the *meaning* actually moved, traces every dashboard and chart that
inherited the change through column-level lineage, and **writes the finding back into the
graph** so the next person to open that dashboard sees it.

```
🔴 Order Total — MEANING CHANGED
   Definition changed at 2026-07-28 03:47 UTC (revision v5 → v6).

   - The total monetary value of an order, including all line items, discounts, and taxes.
   + The net monetary value of an order: the sum of all line item totals, net of
   + discounts and refunds, EXCLUDING shipping and tax.

   Blast radius — applied to 21 assets, feeding 15 consumer surfaces:
     3 dashboards   Order Entry Dashboard (looker), Order Entry Dashboard (tableau), …
     12 charts      Executive Summary (powerbi), Orders By Month (tableau), …

   > Every one of those 15 surfaces still renders without error. They now answer a
   > different question than they did before this edit, and nothing in the stack said so.
```

---

## Try it in 30 seconds — no DataHub required

```bash
git clone https://github.com/luongs3/semantic-drift-auditor
cd semantic-drift-auditor
make demo
```

That runs the real audit code against `fixtures/showcase-drift.json` — a recorded snapshot
of a live DataHub instance seeded with the official `showcase-ecommerce` datapack. The
lineage, the glossary history and the 34-asset downstream graph are all real; they were
just captured earlier so you don't have to stand up seven containers to see the tool work.

`make test` runs 58 unit tests, also with no DataHub.

Pre-generated output is in [`examples/`](examples/) if you'd rather just read it.

---

## What it actually does

| Step | How |
|---|---|
| 1. Walk the definition history | Aspect version chain via OpenAPI v3, ordered by `systemMetadata.lastObserved` |
| 2. Diff consecutive revisions | Sentence-level diff, ignoring markdown/whitespace reformatting |
| 3. Decide if *meaning* moved | Rule-based classifier over the **core definition only** → `BREAKING` / `CLARIFYING` / `COSMETIC` |
| 4. Find who inherited it | `glossaryTerms` search filter → `searchAcrossLineage` DOWNSTREAM, deduplicated across dbt/Snowflake siblings |
| 5. Write the finding back | Context **Document** + **Incident** on the affected datasets + **tag** |

### The classifier is deliberately not an LLM

A judge can read [`src/drift.py`](src/drift.py), disagree with a specific weight, and still
trust the output is reproducible. An LLM verdict on a definition diff is neither auditable
nor stable across runs — and this tool's entire value proposition is that you can believe
it when it says a number changed meaning.

The rule that matters most: **breaking signals are only counted inside the core statement
of meaning**, not in the SQL examples underneath it.

```
The net monetary value of an order, excluding shipping.   ← core meaning
SQL Calculation:                                          ← supporting material
- SUM(order_total - shipping_amount)
```

Rewriting the examples is a documentation change. Rewriting the first sentence is a
semantic one. Treating them identically produces false alarms on doc cleanups — and a drift
detector that cries wolf on doc cleanups is a detector people mute. Both directions are
pinned down in [`tests/test_drift.py`](tests/test_drift.py).

### It only alerts when someone is downstream

`should_alert = meaning moved AND consumers exist`. A term nobody uses can be redefined
freely. That's why the demo shows two terms and only one of them alarms.

---

## Against a live DataHub

```bash
make quickstart          # DataHub OSS via docker (~8GB RAM)
make seed                # load the showcase-ecommerce datapack
make drift               # author real definition changes through DataHub's API
make audit               # detect them
make verify              # audit + write findings back into the graph
```

### On the demo's revision history

`showcase-ecommerce` ships 10 glossary terms but **no revision history** — nothing in the
datapack authors a second version of anything. So `make drift` creates the history it
detects, by calling `updateDescription` through DataHub's own GraphQL API.

That is a real entity, a real aspect write, a real version chain and a real timeline event —
but it is **authored by this repo**, not pre-existing catalog state, and we'd rather say so
than let you assume otherwise. `make drift` applies two changes on purpose: one that
genuinely redefines a metric, one that only rewrites its examples. The tool is supposed to
tell them apart, and the report shows it doing so.

### What gets written back

Verified against DataHub OSS Core v1.5.0.6:

| Artifact | Where it lands | Verify with |
|---|---|---|
| **Document** (2,482 chars, 20 related assets) | `urn:li:document:…` — full before/after, blast radius, owners | `GET /openapi/v3/entity/document/<urn>` |
| **Incident** (19 entities, ACTIVE) | Raised on every affected dataset | `dataset.incidents` in GraphQL |
| **Tag** `semantic-drift` | Visible in the DataHub UI on each affected dataset | `dataset.tags` in GraphQL |

The Document is the part that matters: a tag says "something happened here", a document
says *what the definition used to be*, *what it is now*, *who changed it* and *which 15
dashboards inherited it* — so the analysis is inherited rather than recomputed.

---

## Notes on DataHub OSS, learned the hard way

Four things that cost us real time, written down in case they save you some:

1. **There is no glossary-term-version API on OSS.** `getGlossaryTermVersions` and
   `compareGlossaryTermVersions` do not exist in the shipped v1.5.0.6 GraphQL schema. The
   generic aspect version chain does, and it works fine — see `term_revisions()`.
2. **Aspect version numbers are not chronological.** `version=0` is a *copy* of the current
   value, `version=1` is the **oldest**, and the highest number is the newest. Diffing v0
   against v1 silently compares "now" to "the beginning of time". We sort on
   `systemMetadata.lastObserved` instead, which is correct regardless.
3. **The Timeline API is `/openapi/v2/timeline/v1/<urn>`.** The v3 path 404s.
4. **Glossary terms accept neither incidents nor tags** — `raiseIncident` returns *"Entity
   type … is not a valid destination"*, `addTag` returns *"Unknown aspect globalTags"*.
   Both attach to data assets. That's the correct shape anyway: the term is fine, it's the
   tables inheriting the redefinition that need triage.

Also: `searchAcrossLineage`'s `degree` filter accepts `"1"`, `"2"` and `"3+"` — passing
`"3"` is a 400.

---

## Repo layout

```
src/datahub_client.py   read + write surface (GraphQL, OpenAPI v3, Timeline)
src/drift.py            diffing, classification, blast-radius aggregation
src/report.py           markdown report + the Document written into DataHub
src/fixtures.py         record/replay, so judges and CI need no DataHub
src/cli.py              audit / drift / record
tests/                  28 tests, no DataHub required
examples/               real generated output
fixtures/               recorded snapshot of a live seeded instance
```

## Disclosure

Built during the submission period for **Build with DataHub: The Agent Hackathon**
(Challenge 4 — Open/Wildcard).

This project shares its HTTP transport layer (auth, GraphQL POST, aspect GET, error
wrapping — roughly 120 lines of `src/datahub_client.py`) with our other submission,
[`lineage-guard`](https://github.com/luongs3/lineage-guard), which was written during the
same period for Challenge 2. Everything else is specific to this project: `lineage-guard`
reacts to a Git diff and traces *structural* lineage; this reacts to glossary history and
traces *semantic* drift. Different input, different DataHub surface, different output.

We also opened a documentation correction upstream at
[`datahub-project/datahub-skills#60`](https://github.com/datahub-project/datahub-skills/pull/60),
fixing an incorrect OSS/Cloud capability boundary we hit while building.

## License

Apache-2.0 — see [LICENSE](LICENSE).
