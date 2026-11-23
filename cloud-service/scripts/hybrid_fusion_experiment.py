#!/usr/bin/env python3
"""Collect JVector/text candidates and compare client-side fusion on judged queries.

This is a relevance experiment, not a concurrent latency benchmark. It runs only
when explicitly invoked by the 500k Jenkins pipeline and uses the existing index.
"""

import argparse
import json
import math
import os
import ssl
import time
from pathlib import Path
from urllib.request import Request, urlopen


def ndcg_at_10(passage_ids, qrels, separator="#"):
    seen = set()
    ranked = []
    for passage_id in passage_ids:
        doc_id = passage_id.split(separator, 1)[0] if separator else passage_id
        if doc_id not in seen:
            ranked.append(doc_id)
            seen.add(doc_id)
        if len(ranked) == 10:
            break
    gains = [max(0, int(qrels.get(doc_id, 0))) for doc_id in ranked]
    dcg = sum((2 ** grade - 1) / math.log2(rank + 2)
              for rank, grade in enumerate(gains))
    ideal = sorted((int(v) for v in qrels.values() if int(v) > 0), reverse=True)[:10]
    idcg = sum((2 ** grade - 1) / math.log2(rank + 2)
               for rank, grade in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def _normalize(hits):
    scores = [float(hit["score"]) for hit in hits]
    if not scores:
        return {}
    minimum, maximum = min(scores), max(scores)
    normalized = {}
    for hit in hits:
        value = (1.0 if maximum == minimum else
                 (float(hit["score"]) - minimum) / (maximum - minimum))
        # OpenSearch's min_max processor substitutes 0.001 for an exact zero.
        normalized[hit["pid"]] = 0.001 if value == 0.0 else value
    return normalized


def fuse(vector_hits, lexical_hits, method, lexical_weight=0.3,
         rank_constant=60, depth=100):
    """Return passage IDs ranked by one score combination over fixed candidates.

    A missing leg contributes zero. For geometric mean, this means passages
    appearing in only one leg have score zero; that behavior is reported rather
    than hidden with an arbitrary smoothing constant.
    """
    if not 0 <= lexical_weight <= 1 or rank_constant < 1 or depth < 1:
        raise ValueError("Invalid fusion parameters")
    vector_hits, lexical_hits = vector_hits[:depth], lexical_hits[:depth]
    ids = {hit["pid"] for hit in vector_hits} | {hit["pid"] for hit in lexical_hits}
    order = {pid: min(
        next((i for i, h in enumerate(vector_hits) if h["pid"] == pid), depth),
        next((i for i, h in enumerate(lexical_hits) if h["pid"] == pid), depth),
    ) for pid in ids}
    if method == "rrf":
        v = {h["pid"]: 1 / (rank_constant + i) for i, h in enumerate(vector_hits, 1)}
        l = {h["pid"]: 1 / (rank_constant + i) for i, h in enumerate(lexical_hits, 1)}
    elif method in ("arithmetic_mean", "geometric_mean"):
        v, l = _normalize(vector_hits), _normalize(lexical_hits)
    else:
        raise ValueError(f"Unsupported fusion method: {method}")
    scores = {}
    for pid in ids:
        vector_score, lexical_score = v.get(pid, 0.0), l.get(pid, 0.0)
        if method == "geometric_mean":
            scores[pid] = (vector_score ** (1 - lexical_weight)
                           * lexical_score ** lexical_weight)
        else:
            scores[pid] = ((1 - lexical_weight) * vector_score
                           + lexical_weight * lexical_score)
    return sorted(ids, key=lambda pid: (-scores[pid], order[pid], pid))


VARIANTS = (
    ("rrf_equal_k60", "rrf", 0.5, 60),
    ("rrf_lexical30_k60", "rrf", 0.3, 60),
    ("rrf_lexical30_k10", "rrf", 0.3, 10),
    ("minmax_arithmetic_lexical30", "arithmetic_mean", 0.3, 60),
    ("minmax_geometric_lexical30", "geometric_mean", 0.3, 60),
)


class SearchClient:
    def __init__(self, base_url, cert_file, key_file, ca_file):
        self.base_url = base_url
        self.context = ssl.create_default_context(cafile=ca_file)
        self.context.load_cert_chain(certfile=cert_file, keyfile=key_file)

    def search(self, index, body):
        request = Request(
            f"{self.base_url}/{index}/_search", method="GET",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, context=self.context, timeout=120) as response:
            payload = json.load(response)
        if payload.get("timed_out") or payload.get("_shards", {}).get("failed", 0):
            raise RuntimeError("OpenSearch search timed out or had shard failures")
        return [{"pid": str(h.get("_source", {}).get("pid", h["_id"])),
                 "score": float(h["_score"])}
                for h in payload["hits"]["hits"]]


def _load_job(job_dir):
    matches = list(job_dir.glob("*openrag-hybrid-search*/workload-params.json"))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one OpenRAG search params file, found {len(matches)}")
    params = json.loads(matches[0].read_text())
    query_path = Path(params["queries_file"])
    if not query_path.is_absolute():
        query_path = Path(os.environ.get("BENCHMARK_HOME", "/datasets/opensearch-benchmark")) / query_path
    queries = json.loads(query_path.read_text())["queries"]
    judged = [q for q in queries if q.get("qrels")]
    if not judged:
        raise RuntimeError("No judged queries in ground-truth file")
    return params, judged


def run(job_dir, client, output_dir):
    params, judged = _load_job(job_dir)
    index = params["target_index_name"]
    ef_search = int(params.get("ef_search", 128))
    separator = params.get("qrel_id_separator", "#")
    output_dir.mkdir(parents=True, exist_ok=True)
    sums = {f"{name}_depth{depth}": {} for depth in (50, 100)
            for name, _, _, _ in VARIANTS}
    started = time.monotonic()
    with (output_dir / "candidates.jsonl").open("w") as candidate_file:
        for query in judged:
            text = query.get("text") or query.get("title", "")
            size = 100
            common = {"size": size, "_source": ["pid"]}
            vector_body = {**common, "query": {"knn": {"emb": {
                "vector": query["vector"], "k": size,
                "method_parameters": {"ef_search": ef_search},
            }}}}
            lexical_body = {**common, "query": {"bool": {
                "should": [
                    {"multi_match": {"query": text, "fields": ["text^2", "title^1.5"],
                                     "type": "best_fields", "operator": "or",
                                     "fuzziness": "AUTO:4,7", "boost": 0.3}},
                    {"match_phrase_prefix": {"text": {"query": text,
                                                       "max_expansions": 50, "boost": 0.25}}},
                ], "minimum_should_match": 1,
            }}}
            vector_hits = client.search(index, vector_body)
            lexical_hits = client.search(index, lexical_body)
            record = {"query_id": query["query_id"], "vector": vector_hits,
                      "lexical": lexical_hits}
            candidate_file.write(json.dumps(record) + "\n")
            for depth in (50, 100):
                for name, method, lexical_weight, rank_constant in VARIANTS:
                    ranked = fuse(vector_hits, lexical_hits, method,
                                  lexical_weight, rank_constant, depth)
                    sums[f"{name}_depth{depth}"][query["query_id"]] = ndcg_at_10(
                        ranked, query["qrels"], separator)
    summary = {
        "index": index, "engine": "jvector", "queries_scored": len(judged),
        "candidate_depths": [50, 100], "ef_search": ef_search,
        "collection_seconds": round(time.monotonic() - started, 3),
        "latency_note": "Sequential candidate collection; not a concurrent end-to-end latency benchmark",
        "geometric_missing_leg": "zero",
        "variants": {name: {"ndcg@10_mean": sum(scores.values()) / len(scores),
                            "per_query_ndcg@10": scores}
                     for name, scores in sums.items()},
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", type=Path, required=True)
    args = parser.parse_args()
    namespace = os.environ["OS_NAMESPACE"]
    base_url = f"https://opensearch-cluster.{namespace}.svc.cluster.local:9200"
    client = SearchClient(base_url, "/certs/admin.pem", "/certs/admin-key.pem",
                          "/certs/root-ca.pem")
    summary = run(args.job_dir, client, args.job_dir / "fusion-experiment")
    print(f"Scored {summary['queries_scored']} judged queries; "
          f"results: {args.job_dir / 'fusion-experiment' / 'summary.json'}")


if __name__ == "__main__":
    main()
