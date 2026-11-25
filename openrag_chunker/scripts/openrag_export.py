#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Exports what OpenRAG indexed for the corpus PDFs as the workload's chunk files.

Reads OpenRAG's documents index with the OpenSearch admin login (DLS would
hide chunks from a regular user) and keeps OpenRAG's text, vectors, metadata
and _ids. OpenRAG's own sample documents and website records are skipped.
The chunks land in <work-dir>/chunks, where build_corpus.py's queries and
package stages read them:

  python openrag_export.py --work-dir ~/openrag-arxiv --opensearch-url https://localhost:9200
  python build_corpus.py queries --work-dir ~/openrag-arxiv
  python build_corpus.py package --work-dir ~/openrag-arxiv

Ownership: uploads from one account all share one owner, and in OpenRAG's
no-auth mode they have none, which DLS treats as visible to everyone. So by
default each paper's owner and sharing fields are replaced with the synthetic
users of bench_identity.py, assigned exactly as the offline builder assigns
them, and the owner part of each _id is recomputed to match.
--keep-ownership exports OpenRAG's values unchanged.
"""

import argparse
import collections
import datetime
import json
import os
import re
import shutil
import sys
from pathlib import Path

WORKLOAD_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKLOAD_DIR))

import bench_identity  # noqa: E402
import openrag_queries as oq  # noqa: E402

DEFAULT_SPACE_ID = "openai:text-embedding-3-small"
EXPECTED_DIMENSIONS = 1536
# The knn method index.json uses by default (OpenRAG's build_knn_vector_field).
EXPECTED_METHOD = {"name": "disk_ann", "engine": "jvector", "space_type": "l2"}
SAMPLE_CONNECTOR_TYPE = "openrag_docs"
OWNER_SCOPED_ID = re.compile(r"^[0-9a-f]{24}_(.+)$")
PAGE_SIZE = 200


def log(msg):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)


def check_mapping(properties, field, warnings):
    field_def = properties.get(field)
    if not isinstance(field_def, dict) or field_def.get("type") != "knn_vector":
        raise SystemExit(
            f"The index has no knn_vector field [{field}]. The workload expects OpenRAG configured with "
            f"OpenAI text-embedding-3-small; found vector fields: "
            f"{sorted(k for k, v in properties.items() if isinstance(v, dict) and v.get('type') == 'knn_vector')}")
    if field_def.get("dimension") != EXPECTED_DIMENSIONS:
        warnings.append(f"{field} has dimension {field_def.get('dimension')}, the workload maps {EXPECTED_DIMENSIONS}")
    method = field_def.get("method", {})
    for key, expected in EXPECTED_METHOD.items():
        if method.get(key) != expected:
            warnings.append(f"{field} uses {key}={method.get(key)!r}; index.json defaults to {expected!r} "
                            f"(override with the knn_* workload parameters)")
    index_json = (WORKLOAD_DIR / "index.json").read_text()
    ours = set(re.findall(r'^ {6}"([A-Za-z0-9_]+)": \{', index_json, re.M))
    dynamic = sorted(set(properties) - ours)
    if dynamic:
        log(f"Fields OpenRAG mapped that index.json leaves to dynamic mapping, as OpenRAG's own index body does: "
            f"{', '.join(dynamic)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-dir", type=Path, default=Path("~/openrag-arxiv"))
    parser.add_argument("--opensearch-url", default="https://localhost:9200")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", help="default: $OPENSEARCH_PASSWORD")
    parser.add_argument("--verify-certs", action="store_true",
                        help="verify TLS (OpenRAG's demo certificates are self-signed)")
    parser.add_argument("--index", default="documents", help="OpenRAG's index (OPENSEARCH_INDEX_NAME)")
    parser.add_argument("--embedding-space-id", default=DEFAULT_SPACE_ID)
    parser.add_argument("--all-files", action="store_true",
                        help="export every PDF in the index, not only those in <work-dir>/pdfs")
    parser.add_argument("--keep-ownership", action="store_true",
                        help="keep OpenRAG's owner and sharing fields instead of the synthetic users")
    args = parser.parse_args()
    work_dir = args.work_dir.expanduser()

    import httpx

    password = args.password or os.environ.get("OPENSEARCH_PASSWORD")
    if not password:
        raise SystemExit("Set OPENSEARCH_PASSWORD (OpenSearch admin password) or pass --password")
    field = oq.embedding_field_name(args.embedding_space_id)
    client = httpx.Client(base_url=args.opensearch_url.rstrip("/"), auth=(args.user, password),
                          verify=args.verify_certs, timeout=120)

    r = client.get(f"/{args.index}/_mapping")
    r.raise_for_status()
    mapping = r.json()
    (work_dir / "openrag_mapping.json").write_text(json.dumps(mapping, indent=1))
    properties = {}
    for index_data in mapping.values():
        properties.update(index_data.get("mappings", {}).get("properties", {}))
    warnings = []
    check_mapping(properties, field, warnings)

    query = {"bool": {
        "filter": [{"exists": {"field": field}}],
        "must_not": [
            {"term": {"connector_type": SAMPLE_CONNECTOR_TYPE}},
            {"term": {"is_sample_data": "true"}},
            {"terms": {"record_kind": oq.API_CHUNK_SEARCH_EXCLUDED_RECORD_KINDS}},
        ],
    }}
    if args.all_files:
        query["bool"]["filter"].append({"term": {"mimetype": "application/pdf"}})
        r = client.post(f"/{args.index}/_search", json={
            "size": 0, "query": query, "aggs": {"files": {"terms": {"field": "filename", "size": 65535}}}})
        r.raise_for_status()
        pdf_names = sorted(b["key"] for b in r.json()["aggregations"]["files"]["buckets"])
    else:
        pdf_names = sorted(p.name for p in (work_dir / "pdfs").glob("*.pdf"))
        if not pdf_names:
            raise SystemExit(f"No PDFs in {work_dir / 'pdfs'}; pass --all-files to export whatever the index holds")
        query["bool"]["filter"].append({"terms": {"filename": pdf_names}})
    # Ownership follows the position in the sorted PDF list, as in build_corpus.py embed,
    # so a paper OpenRAG failed to ingest does not shift the others.
    paper_index = {name: i for i, name in enumerate(pdf_names)}
    body = {"size": PAGE_SIZE, "sort": ["_doc"], "query": query}

    # Written aside and swapped in at the end, so an interrupted export never
    # leaves a partial corpus for the queries and package stages.
    chunk_dir = work_dir / "chunks"
    partial_dir = work_dir / "chunks.partial"
    shutil.rmtree(partial_dir, ignore_errors=True)
    partial_dir.mkdir(parents=True)

    chunks_per_paper = collections.Counter()
    dimensions = collections.Counter()
    spaces = collections.Counter()
    missing_vectors = 0
    r = client.post(f"/{args.index}/_search", params={"scroll": "10m"}, json=body)
    r.raise_for_status()
    page = r.json()
    scroll_id = page.get("_scroll_id")
    try:
        while page["hits"]["hits"]:
            by_paper = collections.defaultdict(list)
            for hit in page["hits"]["hits"]:
                doc, chunk_id = hit["_source"], hit["_id"]
                vector = doc.get(field)
                if not isinstance(vector, list) or not vector:
                    missing_vectors += 1
                    continue
                dimensions[len(vector)] += 1
                spaces[doc.get("embedding_space_id") or doc.get("embedding_model")] += 1
                filename = doc["filename"]
                if not args.keep_ownership:
                    doc.update(bench_identity.paper_acl(paper_index[filename], Path(filename).stem))
                    scoped = OWNER_SCOPED_ID.match(chunk_id)
                    if scoped:
                        chunk_id = f"{bench_identity.owner_scope_digest(doc['owner'])}_{scoped.group(1)}"
                by_paper[filename].append({"_id": chunk_id, "doc": doc})
            for filename, records in by_paper.items():
                with (partial_dir / f"{Path(filename).stem}.jsonl").open("a") as f:
                    for record in records:
                        f.write(json.dumps(record) + "\n")
                chunks_per_paper[filename] += len(records)
            log(f"exported {sum(chunks_per_paper.values())} chunks from {len(chunks_per_paper)} papers")
            r = client.post("/_search/scroll", json={"scroll": "10m", "scroll_id": scroll_id})
            r.raise_for_status()
            page = r.json()
            scroll_id = page.get("_scroll_id", scroll_id)
    finally:
        if scroll_id:
            client.request("DELETE", "/_search/scroll", json={"scroll_id": scroll_id})

    if missing_vectors:
        raise SystemExit(f"{missing_vectors} chunks came back without their {field} vector; "
                         f"the corpus would have no embeddings for them")
    if set(dimensions) != {EXPECTED_DIMENSIONS}:
        warnings.append(f"vector dimensions found: {dict(dimensions)}")
    if set(spaces) - {args.embedding_space_id}:
        warnings.append(f"embedding spaces found: {dict(spaces)}")
    not_ingested = [n for n in pdf_names if n not in chunks_per_paper]
    if chunk_dir.exists():
        log(f"Replacing the chunk files of an earlier build in {chunk_dir}")
        shutil.rmtree(chunk_dir)
    partial_dir.rename(chunk_dir)

    summary = {
        "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": f"{args.opensearch_url}/{args.index}",
        "papers": len(chunks_per_paper),
        "chunks": sum(chunks_per_paper.values()),
        "papers_without_chunks": not_ingested,
        "ownership": "OpenRAG" if args.keep_ownership else "synthetic (bench_identity.py)",
        "warnings": warnings,
    }
    (work_dir / "openrag_export.json").write_text(json.dumps(summary, indent=1))
    for w in warnings:
        log(f"WARNING: {w}")
    log(f"Exported {summary['chunks']} chunks from {summary['papers']} papers"
        + (f"; {len(not_ingested)} PDFs have no chunks in OpenRAG: {', '.join(not_ingested[:10])}"
           if not_ingested else ""))
    log("Next: build_corpus.py queries, then build_corpus.py package")


if __name__ == "__main__":
    main()
