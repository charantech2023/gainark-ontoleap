"""
GainARK OntoLeap — Semantic Content Clustering & Cosine Similarity Engine

This module provides mathematical text vectorization and semantic analysis:
1. TF-IDF Vectorization: In-memory tokenization, stopword removal, term frequency,
   and inverse document frequency weighting with L2 unit normalization.
2. Cosine Similarity Matrix: Computes exact pairwise cosine similarity (dot product of unit vectors)
   to quantify topical overlap between crawled pages and authority hubs.
3. Mathematical Cannibalization Detection: Flags pages with cosine similarity >= threshold (default 0.70)
   targeting similar entities to prevent organic rank cannibalization.
4. Semantic Topic Clustering: Groups pages into cohesive topical silos (Core Capabilities,
   Integrations & Ecosystem, Compliance & Governance, Monetization & Pricing).
"""

import math
import re
from typing import List, Dict, Any, Tuple, Set, Optional
from pydantic import BaseModel, Field

# English stopwords list for domain SEO text cleaning
STOP_WORDS: Set[str] = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "can't", "cannot", "could",
    "couldn't", "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down",
    "during", "each", "few", "for", "from", "further", "had", "hadn't", "has",
    "hasn't", "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her",
    "here", "here's", "hers", "herself", "him", "himself", "his", "how", "how's",
    "i", "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it",
    "it's", "its", "itself", "let's", "me", "more", "most", "mustn't", "my",
    "myself", "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other",
    "ought", "our", "ours", "ourselves", "out", "over", "own", "same", "shan't",
    "she", "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such",
    "than", "that", "that's", "the", "their", "theirs", "them", "themselves",
    "then", "there", "there's", "these", "they", "they'd", "they'll", "they're",
    "they've", "this", "those", "through", "to", "too", "under", "until", "up",
    "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were",
    "weren't", "what", "what's", "when", "when's", "where", "where's", "which",
    "while", "who", "who's", "whom", "why", "why's", "with", "won't", "would",
    "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours",
    "yourself", "yourselves", "will", "get", "use", "also", "com", "http", "https"
}


def tokenize(text: str) -> List[str]:
    """Tokenizes text into lowercase alphanumeric words, filtering out short tokens and stopwords."""
    tokens = re.findall(r'[a-zA-Z]{3,}', text.lower())
    return [t for t in tokens if t not in STOP_WORDS]


class TfidfVector:
    """Represents an L2-normalized sparse TF-IDF document vector."""
    def __init__(self, term_weights: Dict[str, float]):
        self.weights = term_weights

    def cosine_similarity(self, other: "TfidfVector") -> float:
        """Computes cosine similarity (dot product of L2-normalized vectors) in [0.0, 1.0]."""
        if not self.weights or not other.weights:
            return 0.0
        # Iterate over smaller dictionary for efficiency
        if len(self.weights) > len(other.weights):
            smaller, larger = other.weights, self.weights
        else:
            smaller, larger = self.weights, other.weights

        dot_product = sum(weight * larger[term] for term, weight in smaller.items() if term in larger)
        return max(0.0, min(1.0, dot_product))


def build_tfidf_model(documents: List[str]) -> List[TfidfVector]:
    """
    Computes standard TF-IDF vectors with L2 normalization across a corpus of documents.
    """
    n_docs = len(documents)
    if n_docs == 0:
        return []

    doc_tokens: List[List[str]] = [tokenize(doc) for doc in documents]
    
    # 1. Document Frequency (DF)
    doc_freq: Dict[str, int] = {}
    for tokens in doc_tokens:
        unique_tokens = set(tokens)
        for t in unique_tokens:
            doc_freq[t] = doc_freq.get(t, 0) + 1

    # 2. Inverse Document Frequency (IDF) with smoothing: idf = ln((1 + N) / (1 + df)) + 1
    idf: Dict[str, float] = {}
    for term, df in doc_freq.items():
        idf[term] = math.log((1.0 + n_docs) / (1.0 + df)) + 1.0

    # 3. TF-IDF vectors with L2 unit norm
    vectors: List[TfidfVector] = []
    for tokens in doc_tokens:
        if not tokens:
            vectors.append(TfidfVector({}))
            continue

        # Term Frequency
        tf: Dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1

        total_tokens = len(tokens)
        raw_weights: Dict[str, float] = {}
        sum_sq = 0.0

        for t, count in tf.items():
            tf_norm = count / total_tokens
            w = tf_norm * idf.get(t, 1.0)
            raw_weights[t] = w
            sum_sq += w * w

        norm = math.sqrt(sum_sq)
        if norm > 0:
            norm_weights = {t: round(w / norm, 5) for t, w in raw_weights.items()}
        else:
            norm_weights = raw_weights

        vectors.append(TfidfVector(norm_weights))

    return vectors


class SimilarityPair(BaseModel):
    url_a: str
    url_b: str
    similarity_score: float
    overlap_keywords: List[str] = Field(default_factory=list)
    risk_level: str  # High Cannibalization Risk, Moderate Overlap, Distinct Silo


class SemanticCluster(BaseModel):
    cluster_id: int
    cluster_name: str
    theme_keywords: List[str]
    pages: List[str]
    cohesion_score: float


class ClusterAnalysisResult(BaseModel):
    total_pages_analyzed: int
    clusters: List[SemanticCluster]
    high_similarity_pairs: List[SimilarityPair]
    average_inter_page_similarity: float


def analyze_semantic_clusters(
    pages_data: List[Any],
    hubs: Optional[Dict[str, str]] = None
) -> ClusterAnalysisResult:
    """
    Analyzes semantic relationships between crawled pages:
    - Computes TF-IDF vector representations for all pages.
    - Generates pairwise similarity matrix and identifies cannibalization pairs (similarity >= 0.70).
    - Clusters pages into topical silos based on semantic keyword overlap.
    """
    if not pages_data:
        return ClusterAnalysisResult(
            total_pages_analyzed=0,
            clusters=[],
            high_similarity_pairs=[],
            average_inter_page_similarity=0.0
        )

    urls = [p.url for p in pages_data]
    docs = [f"{getattr(p, 'title', '') or ''} {getattr(p, 'text', '') or ''}" for p in pages_data]
    vectors = build_tfidf_model(docs)

    n = len(pages_data)
    similarity_matrix: List[List[float]] = [[0.0] * n for _ in range(n)]
    pairs: List[SimilarityPair] = []
    total_sim = 0.0
    pair_count = 0

    for i in range(n):
        similarity_matrix[i][i] = 1.0
        for j in range(i + 1, n):
            sim = round(vectors[i].cosine_similarity(vectors[j]), 4)
            similarity_matrix[i][j] = sim
            similarity_matrix[j][i] = sim
            total_sim += sim
            pair_count += 1

            # Determine top overlapping terms
            overlap = []
            if vectors[i].weights and vectors[j].weights:
                common = set(vectors[i].weights.keys()) & set(vectors[j].weights.keys())
                scored = sorted(
                    [(t, vectors[i].weights[t] * vectors[j].weights[t]) for t in common],
                    key=lambda x: x[1],
                    reverse=True
                )
                overlap = [t[0] for t in scored[:4]]

            if sim >= 0.70:
                risk = "High Cannibalization Risk"
            elif sim >= 0.45:
                risk = "Moderate Topical Overlap"
            elif sim >= 0.25:
                risk = "Topical Proximity"
            else:
                risk = "Distinct Silo Node"

            if sim >= 0.25:
                pairs.append(SimilarityPair(
                    url_a=urls[i],
                    url_b=urls[j],
                    similarity_score=sim,
                    overlap_keywords=overlap,
                    risk_level=risk
                ))

    pairs.sort(key=lambda x: x.similarity_score, reverse=True)
    avg_sim = round(total_sim / max(1, pair_count), 4)

    # Topic Cluster Siloing
    # Assign pages to topic themes based on seed ontology taxonomy
    cluster_themes = [
        {"name": "Core Platform & Workflow Automation", "keywords": ["automat", "workflow", "process", "cycle", "platform", "software"]},
        {"name": "Ecosystem Integrations & APIs", "keywords": ["integrat", "api", "salesforce", "netsuite", "quickbooks", "connect", "sync"]},
        {"name": "Regulatory Compliance & Security", "keywords": ["complian", "asc", "soc", "ifrs", "gaap", "security", "audit"]},
        {"name": "Monetization, Pricing & Billing", "keywords": ["pricing", "bill", "subscription", "usage", "cost", "invoice"]}
    ]

    cluster_buckets: Dict[int, List[int]] = {i: [] for i in range(len(cluster_themes))}
    unassigned = []

    for idx, p in enumerate(pages_data):
        doc_lower = docs[idx].lower()
        best_theme = -1
        best_score = 0
        for t_idx, theme in enumerate(cluster_themes):
            score = sum(doc_lower.count(kw) for kw in theme["keywords"])
            if score > best_score:
                best_score = score
                best_theme = t_idx
        if best_score > 0:
            cluster_buckets[best_theme].append(idx)
        else:
            unassigned.append(idx)

    clusters: List[SemanticCluster] = []
    for t_idx, theme in enumerate(cluster_themes):
        page_indices = cluster_buckets[t_idx]
        if not page_indices:
            continue
        c_urls = [urls[i] for i in page_indices]
        
        # Calculate cluster cohesion (average pairwise similarity within cluster)
        if len(page_indices) > 1:
            c_pairs_sim = [similarity_matrix[i][j] for i in page_indices for j in page_indices if i < j]
            cohesion = round(sum(c_pairs_sim) / max(1, len(c_pairs_sim)), 4)
        else:
            cohesion = 1.0

        clusters.append(SemanticCluster(
            cluster_id=t_idx + 1,
            cluster_name=theme["name"],
            theme_keywords=theme["keywords"][:4],
            pages=c_urls,
            cohesion_score=cohesion
        ))

    # If any unassigned pages remain, add to general cluster
    if unassigned:
        clusters.append(SemanticCluster(
            cluster_id=len(clusters) + 1,
            cluster_name="General Platform Content",
            theme_keywords=["general", "overview"],
            pages=[urls[i] for i in unassigned],
            cohesion_score=0.5
        ))

    return ClusterAnalysisResult(
        total_pages_analyzed=n,
        clusters=clusters,
        high_similarity_pairs=pairs[:15],
        average_inter_page_similarity=avg_sim
    )
