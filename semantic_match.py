"""
semantic_match.py - propose missing alt labels by meaning, for a person to approve.

Coverage is decided by exact matching: a concept counts as claimed when the graph uses its
label or one of its curated alt labels. That misses a site that says the same thing in
other words - "failed payment retries" for Dunning, "matching payments to invoices" for
Cash Application - and reports the concept as whitespace. The site is not missing the
concept; the ontology is missing a word.

Sentence embeddings find those pairs, but they cannot be trusted to *decide* them. Scored
across the 111 defined concepts of b2b_saas_fintech with all-MiniLM-L6-v2 (16 Sep 2026):

    true matches           Dunning <-> "failed payment retries"          0.603
                           Cash Application <-> "matching payments ..."  0.791
    distinct concepts      SOC 1 Type II <-> SOC 2 Type II               0.946
                           MRR <-> ARR                                   0.876
                           Subscription Upgrades <-> Downgrades          0.803

The two ranges overlap, so no threshold admits the real matches without also admitting
"SOC 1 is SOC 2" - and 34 pairs of distinct concepts still score above 0.70. A coverage
signal built on this would report a compliance audit the site never claimed.

So this module only proposes. It follows the path sector_ontology.propose_alt_labels
already set for generated synonyms, which were demoted for the same reason (1 of 114
agreed with curation): a candidate goes into the review queue with its score, its margin
and the concept it was nearly confused with; a person approves it through
/api/ontology/approve-synonym; the approved form becomes an alt label; and from then on
exact matching finds it with no model involved. Each review makes the next run exact.

Two things carry the weight:

* A concept is embedded with its definition, never its bare label. Short jargon has almost
  no signal for a sentence model - Dunning scored -0.014 against "failed payment retries"
  as a label and 0.603 with its definition. A concept with no definition is not searched,
  which is why a cold vertical proposes nothing and says so.
* A candidate must beat the runner-up concept by a margin, computed against *every*
  defined concept. A term close to two concepts that are close to each other is
  ambiguous, and ambiguity is exactly the SOC 1 / SOC 2 case. Restricting the search to
  whitespace concepts would hide the runner-up and defeat the guard.

Set ONTOLEAP_SEMANTIC_MATCH=0 to switch it off. Uses transformers directly - the
sentence-transformers package is a wrapper over the same mean pooling, and adding it would
add a dependency for twenty lines.
"""

import logging
import os
import re
import threading
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("gainark.semantic_match")

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Below this, nothing is worth a reviewer's time. Set from the measurements above: the
# weakest true match seen was 0.603.
MIN_SCORE = 0.60
# The best concept must lead the second by this much. Measured on b2b_saas_fintech, the
# ambiguous terms all led by 0.035 or less - "SOC audit report" scored SOC 1 at 0.775 with
# SOC 2 only 0.035 behind, "subscription plan change" split Upgrades and Downgrades by
# 0.004 - while real matches led by 0.056 or more. 0.08 was tried first and threw away
# "matching payments to invoices" for Cash Application (0.791, whose definition is nearly
# that sentence). This only proposes, so erring towards a reviewer's click is the right
# side to err on; the SOC case still falls well short.
MIN_MARGIN = 0.05
# A long run of proposals is noise a reviewer will not read. The strongest come first.
MAX_PROPOSALS = 40

_BATCH = 64
_MAX_TOKENS = 96

_model_lock = threading.Lock()
_model: Optional[Tuple[Any, Any]] = None
_model_error: Optional[str] = None


def enabled() -> bool:
    return os.environ.get("ONTOLEAP_SEMANTIC_MATCH", "1").strip().lower() not in ("0", "false", "no", "off")


def model_name() -> str:
    return os.environ.get("ONTOLEAP_SEMANTIC_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _load() -> Optional[Tuple[Any, Any]]:
    """The tokenizer and encoder, loaded once per process.

    A failure is remembered rather than retried: an instance that cannot load the model
    should say so once and carry on, not pay a multi-second failed load on every audit.
    """
    global _model, _model_error
    if _model is not None or _model_error is not None:
        return _model
    with _model_lock:
        if _model is not None or _model_error is not None:
            return _model
        try:
            from transformers import AutoModel, AutoTokenizer
            name = model_name()
            tokenizer = AutoTokenizer.from_pretrained(name)
            encoder = AutoModel.from_pretrained(name)
            encoder.eval()
            _model = (tokenizer, encoder)
            logger.info("Loaded semantic match model %s", name)
        except Exception as err:
            _model_error = "%s: %s" % (type(err).__name__, err)
            logger.warning("Semantic matching unavailable, no candidates will be proposed: %s",
                           _model_error)
    return _model


def encode(texts: Sequence[str]):
    """Unit-length sentence embeddings, mean-pooled over real tokens. None when unavailable."""
    loaded = _load()
    if loaded is None or not texts:
        return None
    import torch
    tokenizer, encoder = loaded
    parts = []
    for start in range(0, len(texts), _BATCH):
        batch = tokenizer(list(texts[start:start + _BATCH]), padding=True, truncation=True,
                          max_length=_MAX_TOKENS, return_tensors="pt")
        with torch.no_grad():
            hidden = encoder(**batch).last_hidden_state
        # Padding tokens are not part of the sentence; averaging them in drags every
        # short term towards the same point.
        mask = batch["attention_mask"].unsqueeze(-1).float()
        pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
        parts.append(torch.nn.functional.normalize(pooled, p=2, dim=1))
    return torch.cat(parts)


@dataclass
class Candidate:
    """One proposed alt label, with everything a reviewer needs to judge it."""
    surface_form: str
    concept: str
    score: float
    runner_up: str
    margin: float
    closes_gap: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _concept_text(label: str, definition: str) -> str:
    return "%s: %s" % (label, definition)


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def find_candidates(terms: Sequence[str], concepts: Sequence[Any],
                    whitespace: Sequence[str] = (),
                    min_score: float = MIN_SCORE,
                    min_margin: float = MIN_MARGIN) -> List[Candidate]:
    """Terms that probably mean a defined concept, strongest first.

    `terms` are words the site uses that the ontology did not place. `concepts` are the
    vertical's IndustryConcept objects; only those with a definition take part. A term
    already written as some concept's label or alt label is skipped - exact matching
    owns it, and proposing it again could only conflict with a curated decision.
    """
    defined = [c for c in concepts if (getattr(c, "definition", "") or "").strip()
               and (getattr(c, "pref_label", "") or "").strip()]
    if not enabled() or not defined:
        return []

    known = set()
    for c in concepts:
        known.add(_normalise(c.pref_label))
        known.update(_normalise(a) for a in (c.alt_labels or []))
    wanted: List[str] = []
    seen = set()
    for term in terms:
        key = _normalise(term)
        # A two-letter fragment has nothing to embed, and a term the ontology already
        # places is not a candidate.
        if len(key) < 3 or key in known or key in seen:
            continue
        seen.add(key)
        wanted.append(term.strip())
    if not wanted:
        return []

    concept_vectors = encode([_concept_text(c.pref_label, c.definition) for c in defined])
    term_vectors = encode(wanted)
    if concept_vectors is None or term_vectors is None:
        return []

    gaps = {_normalise(w) for w in whitespace}
    scores = term_vectors @ concept_vectors.T
    top_k = min(2, len(defined))
    values, indices = scores.topk(top_k, dim=1)

    out: List[Candidate] = []
    for row, term in enumerate(wanted):
        best = float(values[row][0])
        best_concept = defined[int(indices[row][0])]
        if top_k > 1:
            second = float(values[row][1])
            runner_up = defined[int(indices[row][1])].pref_label
        else:
            second, runner_up = 0.0, ""
        margin = best - second
        if best < min_score or margin < min_margin:
            continue
        out.append(Candidate(
            surface_form=term,
            concept=best_concept.pref_label,
            score=round(best, 3),
            runner_up=runner_up,
            margin=round(margin, 3),
            closes_gap=_normalise(best_concept.pref_label) in gaps,
        ))

    # A candidate that closes a reported gap is the one worth reading first: it is the
    # difference between "the site lacks this" and "the ontology lacks this word".
    out.sort(key=lambda c: (not c.closes_gap, -c.score))
    return out[:MAX_PROPOSALS]


def propose_from_alignment(alignment: Any, industry: Any, brand: str,
                           queue_path: Optional[str] = None) -> Dict[str, Any]:
    """Queue candidates from one alignment for review. Never raises.

    The inputs are the alignment's own lists: proprietary concepts are the site's words
    the ontology does not know, and whitespace is what it says the site does not cover.
    Returns counts, so a caller can log what happened without reading the queue.
    """
    stats: Dict[str, Any] = {"proposed": 0, "already_queued": 0, "considered": 0,
                             "closes_gap": 0, "enabled": enabled()}
    if not enabled():
        return stats
    try:
        terms = list(getattr(alignment, "proprietary_concepts", None) or [])
        whitespace = list(getattr(alignment, "category_whitespace", None) or [])
        concepts = list(getattr(industry, "concepts", None) or [])
        stats["considered"] = len(terms)
        if not concepts:
            stats["skipped"] = "vertical has no concept layer"
            return stats
        if not any((c.definition or "").strip() for c in concepts):
            stats["skipped"] = "no concept carries a definition"
            return stats

        candidates = find_candidates(terms, concepts, whitespace=whitespace)
        if _model_error:
            stats["skipped"] = "model unavailable: %s" % _model_error
        if not candidates:
            return stats

        import sector_ontology
        queue_path = queue_path or sector_ontology._candidates_path()
        queue = sector_ontology._load_candidates(queue_path)
        for cand in candidates:
            # Keyed as propose_alt_labels keys its own, so an approval through
            # record_reviewer_synonym - which matches on surface_form - closes either.
            key = "%s|%s" % (cand.concept.strip().lower(), cand.surface_form.strip().lower())
            if key in queue:
                stats["already_queued"] += 1
                continue
            queue[key] = {
                "surface_form": cand.surface_form,
                "generated_canonical": cand.concept,
                "brand": brand,
                "status": "pending",
                "method": "embedding",
                "model": model_name(),
                "score": cand.score,
                "runner_up": cand.runner_up,
                "margin": cand.margin,
                "closes_gap": cand.closes_gap,
            }
            stats["proposed"] += 1
            stats["closes_gap"] += int(cand.closes_gap)
        if stats["proposed"]:
            sector_ontology._save_candidates(queue_path, queue)
        logger.info("Semantic proposals for %s: %d new (%d would close a reported gap), "
                    "%d already queued, from %d unplaced terms.",
                    brand, stats["proposed"], stats["closes_gap"],
                    stats["already_queued"], stats["considered"])
    except Exception as err:
        # An audit's result does not depend on this; losing a suggestion is not worth
        # losing the audit.
        logger.warning("Semantic proposal failed for %s: %s", brand, err)
        stats["error"] = str(err)
    return stats
