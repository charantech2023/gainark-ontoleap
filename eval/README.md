# Extraction evaluation

A labelled set and a runner, so "did that change help?" is a number rather than an
impression.

Three defects shipped in one afternoon and each looked plausible in the output: the
alignment matcher compared against `prefLabel` only, so a page written in industry
abbreviations scored as if it had never named the capability; `coverage_score` was
computed from ten seed concepts while ignoring an 81-concept ontology, so a graph
covering eight concepts scored 0%; and the JSON-LD export sliced the node list in
extraction order, dropping every grounded entity because grounded nodes sort last. All
three were found by reading output and noticing something odd. That does not scale.

## Files

| File | What it is |
| :--- | :--- |
| `relations.csv` | The labelled set. The only file a human edits. |
| `seed_candidates.py` | Fills `relations.csv` with candidates from real crawls. |
| `run_eval.py` | Measures the extractor against the labels. |
| `baseline.json` | Committed numbers to compare against. |

## Labelling

Open `relations.csv` and fill the `correct` column with `yes` or `no`. A blank cell means
unreviewed, and unreviewed rows are skipped rather than counted as negatives - a
half-labelled set gives honest numbers over the part that is labelled.

Two kinds of row:

**`extracted=yes`** - the extractor asserted this relationship, and `evidence` is the
sentence it came from. Judge the claim against that sentence. Marking one `no` records a
false positive.

**`extracted=no`** - a vocabulary term the page mentions that no edge accounts for. If the
page really does make that claim, set `predicate` to the right relation and mark it `yes`;
that records a false negative. If the term is merely mentioned in passing - a competitor
named in a comparison, a standard listed in a footer - mark it `no`.

That second kind matters. Without rows the extractor failed to produce, the set can only
ever flatter it.

## Running

```bash
python eval/run_eval.py                  # measure and compare to the baseline
python eval/run_eval.py --save-baseline  # record current numbers as the baseline
```

Output is precision and recall per predicate. Both, always, because they trade off: a
change that loosens matching raises recall and lowers precision, and watching recall alone
makes that look like progress.

```
predicate                precision    recall     tp     fp     fn
----------------------------------------------------------------
compliesWith                  0.92      0.85     11      1      2
integratesWith                1.00      0.70      7      0      3
```

Exit code is 2 if any metric regressed against `baseline.json`, so this can gate a change.

## Adding pages

```bash
python eval/seed_candidates.py https://example.com/product:cybersecurity
```

Existing rows are preserved and their labels kept; only genuinely new candidates are
appended. Cover more than one vertical - the fintech vocabulary is deep and the others are
not, and a set drawn only from fintech would hide that.

## What this does not measure

Entity typing, canonicalisation, and the alignment score are all unmeasured here. This set
covers relationship extraction only, which is the layer everything downstream rests on.
Extend it before trusting a coverage percentage.
