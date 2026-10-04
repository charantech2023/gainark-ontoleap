# Buyer Situations — Design

Status: agreed, 3 Oct 2026; nothing built beyond a prototype extractor
(`buyer_stories.py`, branch `feat/icp-story-prompts`). The decisions are recorded in
[§12](#12-decisions).

The prompts a buyer types into an AI assistant, built from the knowledge graph rather than
from templates or from one vendor's case studies. The graph learns **buyer situations**:
who the buyer is, what they ran before, what changed, what hurt, what they needed and what
constrained them. Each part is a quote on a page, resolved to the vertical's concepts.
Prompts come from walking situations the evidence actually holds. A model only puts them
into words.

---

## 1. Why

Three attempts on 3 Oct 2026, measured with Gemini's Google Search grounding
(`webSearchQueries`, cited sources, the answer), 3 runs per prompt:

| Prompt source | Example | Answers naming Ordway |
|---|---|---|
| `prompt_generator` templates over the buyer profile | "software with ACH for Marketing SaaS Platform" | 0 of 57 |
| `buyer_stories.py` prototype: one case study is one buyer | "I'm the Controller at a background-check platform … spreadsheets and QuickBooks … What should I switch to?" | 5 of 93 |

The story prompts read like buyers and moved Gemini's searches from vendor names
(alternatives 27% → 9% of searches) to capabilities (revenue recognition 6% → 18%,
usage/proration/parent-child 0% → 17%). But they have four faults that more prompt
engineering will not fix:

1. **Survivorship.** Every story is a customer who chose Ordway, from a page Ordway's
   marketing picked because Ordway wins there. Useful as a *should-win* set; not the
   market. Buyers who chose Chargebee or Maxio are absent.
2. **Coverage.** Eight pages, eight situations. Ordway's own segments ("SaaS and AI
   companies", multinationals with multiple entities) have no story at all.
3. **Vendor voice.** The pains are the vendor's framing of the pain. Rewording does not
   change which pains were chosen.
4. **No structure to reuse.** A story is a JSON blob next to a prompt. Nothing joins
   "Gentreo needed hybrid B2C/B2B billing" to the `b2b_saas_fintech` concepts, so nothing
   learned from it reaches the next customer.

The template generator failed the other way: its inputs were **flat lists**. The ontology
has segments, replaced tools, concepts and standards, but no edge saying *this* segment
has *this* pain that needs *this* capability. Crossing unrelated lists produces sentences
nobody types. A case study is exactly an observed edge between those lists. So the stories
belong in the graph, as edges, and prompts belong downstream of the graph.

## 2. What already exists

| Piece | Where | Usable as is? |
|---|---|---|
| Concept layer: definitions, broader tree, alt labels, roles | vertical profiles, `concept_roles` | Yes — the targets situation parts resolve to |
| Segments, industries, replaced tools, competitors | `buyer_profiles.py` (`icp_evidence` with quotes) | Yes, as seeds. Flat lists, no edges between them. |
| Claims with proof in the run graph | PR #7: reified claims, `prov:value` + `prov:wasDerivedFrom` | Yes — the same shape for situation edges |
| Object types `Segment`, `Industry`, `LegacyWorkflow`, `Integration`, `ComplianceStandard` | `ontology_schema.py` | Yes |
| Vendor-side predicates (`targetsSegment`, `replacesWorkflow`, `hasCustomer`, `competesAgainst`) | `ontology_schema.py` | Yes, unchanged. Situations add the buyer side. |
| Identity of companies and products (Recurly, QuickBooks, NetSuite) | `entity_registry` | Yes |
| Vocabulary proposals and review | `vocabulary_learning` | Yes — unresolved parts become proposals |
| Competitor set and domains | `COMPETITOR_PROFILES_DESIGN.md` phase 1 | **Not built.** A dependency of §11 phase 1. |
| Story extraction with quote checks | `buyer_stories.read_story` (prototype, 17 tests) | Yes, as the extractor |
| Prompt rendering with a fact check | `buyer_stories.write_prompts`, `check_prompt` | Yes, as the renderer |
| Cross-engine measurement (ChatGPT, Claude, Gemini, Perplexity) | DFS Rank Tracker via DataForSEO, imports `/api/buyer-prompts` | Yes, once it is served the new prompts |

## 3. Principles

- **A situation edge is evidence or it is nothing.** Every part carries the sentence and
  the URL, and the sentence is checked against the page before it is stored. A part the
  page does not state is left out, never inferred.
- **One code path for every vendor's case studies.** Ordway's and Chargebee's are read,
  resolved and stored alike. The comparison only means something if the pipeline is the
  same on both sides — the rule from the competitor design.
- **Concepts, not strings.** Pains, needs and constraints resolve to the vertical's concept
  URIs (Proration, Revenue Recognition, Parent Customer). A part that resolves to
  nothing becomes a vocabulary proposal, never a new concept.
- **A model proposes; it never decides a fact.** It reads a page into parts (quotes
  checked), and it writes sentences from parts (fact check). Resolution, storage and path
  selection are deterministic.
- **What is said and how it is said are separate questions.** The graph decides what a
  situation is. How a buyer phrases it is checked against real buyer language (§7), not
  assumed because a prompt reads well.
- **Every review is data.** A rejected part, a corrected concept mapping or a merged
  trigger type is an event the next run reads.

## 4. What the graph holds

### 4.1 A situation

One buyer at one moment, read from one page. Reified, like a claim:

| Field | Value |
|---|---|
| `id` | hash of (page URL, customer) |
| `vendor` | registry entity whose site the page is on (Ordway, Chargebee) |
| `customer` | registry entity of the buyer, when named (Gentreo), kind `organization` |
| `voice` | `customer` (a named buyer's situation) or `vendor` (the vendor describing buyers) |
| `chose` | the vendor the buyer moved to. For a case study, the site's own company. |
| `source` | page URL, run id, read date |

### 4.2 Facets

Edges from the situation. Each is reified with `prov:value` (the quote) and
`prov:wasDerivedFrom` (the page), exactly as PR #7 stores claims.

| Predicate | Object | Resolves to | Example (Gentreo) |
|---|---|---|---|
| `buyerIs` | Segment / Industry | concept or segment node | digital estate planning SaaS |
| `buyerRole` | Role | small controlled list (§4.4) | — (not on the page) |
| `usedBefore` | Product / LegacyWorkflow | registry entity, or `LegacyWorkflow` concept | Recurly |
| `triggeredBy` | Trigger | small controlled list (§4.4) + free text | moving into B2B and B2B2C |
| `sufferedFrom` | Pain | free text + `painAbout` → concept | couldn't handle complex B2B billing → *Hybrid Pricing* (alt label "Hybrid Billing") |
| `needed` | Capability | concept URI | one platform for B2C and B2B billing → *Hybrid Pricing*, *Subscription Management* |
| `constrainedBy` | Integration / Standard | registry entity or standard concept | (Paytient: NetSuite) |

```jsonc
{
  "situation": "sit:ordwaylabs.com:gentreo",
  "vendor": "ent:ordway", "customer": "ent:gentreo", "voice": "customer", "chose": "ent:ordway",
  "facets": [
    {"p": "usedBefore", "o": "ent:recurly",
     "quote": "Gentreo had been using Recurly for subscription management and payment processing.",
     "url": "https://ordwaylabs.com/resources/case-studies/gentreo-saas-billing-case-study/"},
    {"p": "triggeredBy", "o": "trigger:new-business-model", "text": "moving into B2B and B2B2C",
     "quote": "<sentence on the page>", "url": "..."},
    {"p": "needed", "o": "concept:b2b_saas_fintech/hybrid-pricing", "quote": "...", "url": "..."}
  ]
}
```

### 4.3 Where it lives

In the run graph of the crawl that read the page, next to the claims. A situation is then
mirrored, diffed and restored with everything else in the shared brain, and a later run
that reads the same page differently shows up in `diff_runs`.

### 4.4 Three new small vocabularies

`Role`, `Trigger` and `Pain` are not product vocabulary, so they do not belong among the
vertical's concepts. They are situation vocabulary, shared across verticals and kept small:

- **Role**: CFO, VP Finance, Controller, Director of Accounting, Head of Finance, RevOps,
  Founder/CEO, fractional CFO. Read on the 3 Oct run: Controller ×2, Director of
  Accounting ×2, CFO, fractional CFO, Head of Finance.
- **Trigger**: growth in volume, new business model or market (B2B, usage-based, global),
  new deal types, ERP change, new entity or country, audit/funding/IPO readiness.
- **Pain type** (added 3 Oct 2026, §13): what the pain cost the buyer — manual effort,
  slow cycle, cannot scale, errors and accuracy, revenue leakage, vendor support and fit,
  missing capability. Read from the quote; a pain may be several. Its `painAbout` concept
  is kept beside it when the pain is about one. Most case-study pains are about cost, not
  about a product concept, so a pain is walked (§6) by its type.

A trigger that fits no type keeps its text and is proposed as a new type through review.

## 5. Where situations come from

1. **Pages.** Case studies, customer stories and testimonials. The crawl planner already
   ranks pages; customer-story URL patterns (`/case-studies/`, `/customers/`) are favoured
   when the run is reading situations.
2. **Extraction.** `buyer_stories.read_story`: the model returns parts with quotes, and a
   part whose quote is not on the page is dropped (prototype: 1 of 44 parts dropped on
   Ordway's 8 pages).
3. **Resolution.** Tools and companies through the registry. Pains, needs and
   constraints through the resolver against the vertical's concepts and alt labels.
   Unresolved → vocabulary proposal.
4. **Persistence.** Reified into the run graph; mirrored to the archive.
5. **Breadth.** The customer's case studies give the should-win situations. The
   competitors' case studies (Chargebee, Maxio, Zuora) give the situations the customer
   lost, and these need the competitor set from the competitor design.

## 6. Prompts from the graph

Two kinds of path, kept apart in every output:

- **Observed.** The facets of one situation, as the page states them. A situation whose
  `chose` is the customer is *should-win*; one whose `chose` is a competitor is *market*.
- **Pattern.** A combination of trigger, pain concept and need concept supported by
  several situations across vendors (threshold in §12). A pattern may be voiced by any
  segment that shares its pain concept, which is how coverage grows past the pages read
  without crossing facets that never co-occur. The prompt names the situations that
  support it.

Rendering is the prototype's `write_prompts`: the model writes words, the fact check
rejects any name or number the path lacks, and the vendor is never named. Wording draws
on alt labels and on buyer forms learned in §7 ("rev rec", "true-ups", "parent-child
billing", today an alt label of Parent Customer).

Variants per path, because one fixed shape skews what engines search:
- **length:** one line ("billing that handles proration for parent-child accounts?") and
  full situation;
- **turns:** an opening question and a follow-up carrying the constraint;
- **stage:** only where the facets support it. `switch` needs `usedBefore`; `compare`
  needs `usedBefore` or a named alternative, and compares staying against moving.

Each prompt carries: situations, quotes, concept URIs, `path` = observed | pattern, and
`set` = should-win | market.

## 7. Buyer language

The graph cannot say how buyers phrase things: all of its evidence is vendor-written.
Real phrasing comes from:

- **Google "People Also Ask"** for the fan-out searches, via the DataForSEO SERP endpoint
  DFS already calls. Cheap, and tied to the searches engines run.
- **Community threads** where finance people ask about billing and revenue tools.
- **Review sites**: competitors' buyers describing the problem they were solving.
- **The customer's own sales data** (discovery notes, demo forms, chat logs), when the
  customer provides it. The best source there is.

Used two ways: buyer forms of concepts become vocabulary proposals; real questions become
an evaluation set and, with their source, a market prompt set of their own.

## 8. How it learns

- **Review events.** A wrong part, a wrong concept mapping or a trigger type to merge is
  recorded and read by the next extraction.
- **Fan-out.** The searches and cited pages behind each prompt suggest which concepts
  engines join to a situation. Those become proposed edges and proposed competitors (the
  3 Oct run named Chargebee in 64 of 93 answers, Stripe in 48, Sage Intacct in 38), for
  review, never applied.
- **Cross-engine results.** DFS's mention and citation per engine attach to each prompt,
  so the graph records in which situations each vendor is visible.

## 9. Consumers

| Consumer | Uses |
|---|---|
| DFS Rank Tracker | should-win and market prompt sets, by engine |
| Recommendations | situation × engine × cited pages × the customer's pages: which page would change the answer, with the customer's own proof |
| Competitor matrix | which situations each vendor proves, beside which concepts it claims |
| Content briefs | a situation with no page that speaks to it, and the quotes to build one from |

## 10. Test plan and the numbers reported

1. **Extraction precision.** Read by hand on Ordway's 8 pages and 5 case studies from each
   of three competitors: share of parts whose quote states the part; share of concept
   mappings a reviewer accepts.
2. **Coverage.** Situations per vertical; patterns meeting the threshold; segments of the
   buyer profile with at least one situation.
3. **Prompt checks.** Rejections by reason; 30 prompts read by hand against the
   prototype's 31.
4. **Language.** Share of generated prompts whose concepts occur in the real-question set;
   length and first-person rate against those questions.
5. **Stability.** Mention rate across three paraphrases of one path, reported as a range.
   One value is not reported when the range is wide.

## 11. Phases

| Phase | Scope | Done when |
|---|---|---|
| 0 | Schema (§4), Role/Trigger types, persist Ordway's situations from the prototype extractor, resolve parts to concepts | Ordway's situations stored in a run with quotes and concept URIs; §10.1 for Ordway |
| 1 | Competitor case studies (needs competitor set and domains) | three competitors' situations stored; §10.1 for them |
| 2 | Path walker: observed and pattern paths, variants, rendering | prompts compared by hand with the prototype's 31; §10.2–3 |
| 3 | Buyer language: People Also Ask for the fan-out searches, a community and review sample | §10.4 reported |
| 4 | Serve both sets to DFS; four-engine run on a budget agreed first | per-engine mention and citation by set and by stage; §10.5 |

Phase 0 stands on its own: the customer's graph gains the buyer side of its own story
whether or not the rest follows.

**Out of scope:** answer-quality scoring, pricing extraction, dashboards, and any change to
how DFS calls DataForSEO.

## 12. Decisions

Agreed 3 Oct 2026, as recommended.

1. **Role and Trigger are controlled types**, small and shared across verticals, each
   value keeping its free text. Free text alone could not be counted or walked.
   **Pain type was added on the same terms later that day**, after concept proposals for
   pains came out as noise (§13).
2. **Situations live in the run graph**, reified like claims (the same answer as the
   competitor design's §11.1), not in a document per run.
3. **Pattern threshold: at least three situations from at least two vendors.** Lower, and
   one case study's wording becomes a market pattern.
4. **Competitor case-study budget: up to 10 customer-story pages per competitor**, on top
   of the 25 the competitor design proposes.
5. **Should-win and market are reported separately, never pooled**; vendor-voice
   situations are excluded from both.
6. **Real-language sources: People Also Ask** (through DataForSEO's SERP endpoint), **and a
   sample of review sites and community threads**, each question stored with its source.
   Asking Ordway for discovery or demo data is Sree's call, as it means asking a client.
7. **`buyer_stories.py` is merged as the phase 0 extractor and renderer**, behind the
   graph, not as a separate prompt path.

## 13. Phase 0 as built

`buyer_situations.py` (situations into the graph) on top of `buyer_stories.py` (the
reader and the prompt writer). Read on Ordway's 8 evidence pages on 3 Oct 2026 and stored
in the **dev** shared brain (`gs://gainark-ontoleap-graph/dev/ontoleap/situations/`), not
production's.

### Where it departs from the proposal, and why

- **Situations are their own graph kind, `situations`, not part of a crawl's run graph**
  (§4.3). The reader fetches the case-study pages itself, so writing what it found into
  an earlier crawl's run would change that crawl's record. A separate kind keeps
  `list_runs`, `diff_runs` and `vendors_covering` meaning crawls, and is still mirrored
  and restored by the archive sync like every other graph (`graph_store.persist_graph`
  now accepts the kind).
- **Names are found inside phrases, by exact key.** A part is a phrase ("could not
  automatically perform proration calculations"), so word runs of up to six are looked
  up with the resolver's `normalise_key` against the vertical's concepts and the
  registry, longest first, without overlap. A key two concepts share names neither.
- **A name counts only when the quote says it too, and a trigger is read from the
  quote.** The first read linked Yardstik's need to Metered Billing on "usage-based
  billing", a phrase the model added and the quote did not contain.
- **Two more checks on each part (`buyer_stories.read_story`):** half of the part's
  content words must be in its quote ("text says more than its quote"), and a trigger,
  pain or need whose quote names the vendor is an outcome, not the situation before.
- **Unresolved needs and pains go to the synonym queue, not the vocabulary queue** (§3
  said vocabulary proposals). The question is "does this phrase mean an existing concept",
  which is what the synonym queue decides; the vocabulary queue proposes new concepts.
  They are filed only when `propose=True`, into whichever queue the archive configuration
  names (dev on 3 Oct 2026). Tools the registry does not know (Recurly, SaaSOptics, Zuora)
  are listed as registry candidates, never added.
- **No customer entity yet.** The reader keeps the customer's name out of the parts, so
  `customer` (§4.1) is empty and a situation is identified by its page.
- **LegacyWorkflow values are a cue list** (spreadsheets, manual process), matched in the
  part and its quote.

### Measured (§10.1, read by hand)

| | First read | With the checks above |
|---|---|---|
| Situations / parts | 8 / 43 | 8 / 36 |
| Parts whose quote states the part | ~36 of 43 (84%) | ~33 of 36 (92%) |
| Parts dropped, with a reason | 1 | 7 |
| Concept links | 9, of which 1 on words the quote lacked | 8, all correct |
| Roles classified | 7 of 7 | 6 of 6 |

Still wrong after the checks: an outcome that does not name the vendor ("The result is a
flexible billing foundation", Paytient, filed as a need), a need filed as a pain
(Paytient), and a vague company ("a company with complex needs", the Zuora page). The
support check also drops some fair paraphrases ("fitting into the solution" for "fit into
their solution").

Concept coverage was the open problem: 2 of 7 pains and 1 of 5 needs reached a concept,
because exact keys miss paraphrases. That is the review queue's job, not similarity's.

### Concept coverage

Trying the encoder (`semantic_match.find_candidates`, usual thresholds) on every word run
of the unmatched pains and needs showed two things:

1. **Most unmatched pains are not about a product concept.** "Tracking everything in Excel
   became unwieldy", "two to three weeks to complete the bill run", "manual effort" are
   about what the problem cost. Forced onto concepts they gave "billing foundation that
   can" → Multi-Entity and "contract" → Contract Management. Hence **pain types** (§4.4),
   classified from the quote: 6 of 7 pains classified on Ordway; the seventh is the need
   the reader filed as a pain.
2. **Needs propose well once the terms are clean.** `term_spans` keeps runs of two to four
   words that do not start or end on a function word and whose words are all in the
   quote; single words are never candidates. `propose_concepts` files the strongest
   candidate per unmatched need or pain in the shared synonym queue
   (`method: "situation"`, with the quote and page), where a person approves it through
   `/api/ontology/approve-synonym` and exact keys find it from then on.

On Ordway, into the **dev** queue: "pricing models" → Monetization Model (0.73, margin
0.098), "managing billing and revenue" → Revenue Operations (0.735, margin 0.055),
"technology platform" → Platform Capabilities (0.685; too generic to approve). Nothing is
proposed for a facet that already names a concept.

Reviewed the same day in dev (the dev verticals mirror was seeded with a copy of
production's `b2b_saas_fintech` for it): the first two approved, the third rejected. Read
again, with no model involved, Ordway's needs reaching a concept went from 1 of 5 to 3 of 5.
Production's vertical was not changed.

## 14. The first cold run: bamboohr.com

4 Oct 2026, in the dev brain. Discovery routed bamboohr.com to `hr_payroll_benefits`
(27 reviewed concepts) and found 5 evidence pages: four customer stories and the Hibob
comparison page, which reads as vendor voice and is kept out of fan-out. The reader, the
part checks, the prompt writer and the competitor set carried over unchanged. What did not
carry over was everything tuned on Ordway's pages:

| | Ordway | bamboohr.com, first read | after the fixes below |
|---|---|---|---|
| Roles classified | 6 / 6 | **0 / 3** | 3 / 3 |
| Triggers classified | 3 / 3 | **0 / 1** | 1 / 1 |
| Pains typed | 6 / 7 | **0 / 1** | 1 / 1 |
| Needs reaching a concept | 3 / 5 | 0 / 2 | 0 / 2 |

Measured on the same stored stories before and after, so only the code changed.

- **Roles are function × level** (`finance/c-level`, `people/vp`, `accounting/director`),
  read from the quote first - usually the sign-off line - and then the summary. The first
  list was finance titles only.
- **Trigger and pain cues use the buyer's own counts and losses**: workforce, headcount,
  branches and "quadrupled" are growth; "an entire day a month" is a slow cycle; paper is
  manual effort and a LegacyWorkflow value. Legacy values may now be several.
- **Proposal terms come from the quote as well as the summary**, and a pain proposes a
  concept only when it is a missing capability; growth pains proposed noise ("grew and
  added customers" -> Cohort Analysis).
- **Function acronyms are not companies** ("HR" was a registry candidate).

Open: Civtec's need ("apply for and calculate leave, clock in and clock out") ranks the
right concepts first - Time and attendance tracking, then PTO management - but at 0.35 to
0.50, under `semantic_match.MIN_SCORE` (0.60), which was set on `b2b_saas_fintech`. The
encoder's thresholds do not transfer between verticals; they need calibrating per vertical
rather than lowering for one example.
