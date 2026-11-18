#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Builds the openrag_hybrid_search corpus from arXiv PDFs the way OpenRAG ingests them.

Stages, each resumable and cached under --work-dir:

  download   arXiv PDFs: by default the 1000 papers of Vectara's Open RAG
             Benchmark with its labelled queries; or one month of the
             requester-pays bulk bucket s3://arxiv/pdf (--source s3)
  parse      each PDF through docling-serve with OpenRAG's ingestion-flow options,
             exported to markdown one page at a time
  embed      each page split with OpenRAG's splitter settings, each chunk embedded
             with OpenAI text-embedding-3-small, stored as OpenRAG chunk documents
  queries    the query set with precomputed query embeddings: Open RAG Benchmark
             questions when downloaded, otherwise LLM-generated questions,
             keyword queries and token lookups
  package    documents.json.bz2, documents-1k.json.bz2, queries.json.bz2 and the
             document counts in ../corpora.json

Instead of parse and embed, the PDFs can go through a real OpenRAG
(scripts/openrag_ingest.py) and be exported from its index
(scripts/openrag_export.py); queries and package then run on the export.

Example:
  python build_corpus.py all --work-dir ~/openrag-arxiv --docling-url http://localhost:5001
"""

import argparse
import base64
import bz2
import datetime
import hashlib
import json
import random
import re
import sys
import tarfile
import time
from pathlib import Path

WORKLOAD_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKLOAD_DIR))

import bench_identity  # noqa: E402
import openrag_queries as oq  # noqa: E402

INDEX_NAME = "openrag-bench-documents"
EMBEDDING_PROVIDER = "openai"
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_SPACE_ID = oq.embedding_space_id(EMBEDDING_PROVIDER, EMBEDDING_MODEL)
EMBEDDING_FIELD = oq.embedding_field_name(EMBEDDING_SPACE_ID)
EMBEDDING_DIMENSIONS = 1536

# flows/ingestion_flow.json: Split Text and Docling Serve nodes
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
SPLIT_SEPARATOR = "\n"
FALLBACK_SEPARATORS = ("\n\n", "\n", "。", ". ", " ", "")
DOCLING_OPTIONS = {
    "to_formats": ["json"],
    "image_export_mode": "placeholder",
    "do_ocr": False,
    "do_picture_classification": False,
    "do_picture_description": False,
    "do_table_structure": True,
    "ocr_engine": "ocrmac",
}
MD_IMAGE_PLACEHOLDER = "<!-- image -->"
# DocumentIndexWriter: parser label and OPENRAG_INGEST_BATCH_SIZE
PARSER = "Docling Serve 1.20.0"
INGEST_BATCH_SIZE = 100

ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?\.pdf$")
USER_AGENT = "openrag-osb-corpus-builder (OpenSearch Benchmark workload)"
# arXiv's guidance for harvesting is bursts of at most 4 requests per second
# with a 1 second sleep; one request per second stays well inside it.
ARXIV_REQUEST_INTERVAL = 1.0
OPENRAGBENCH_URL = "https://huggingface.co/datasets/vectara/open_ragbench/resolve/main/pdf/arxiv"
OPENRAGBENCH_FILES = ("pdf_urls.json", "queries.json", "qrels.json")


def log(msg):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)


def hash_id(data, length=24):
    """OpenRAG utils.hash_utils.hash_id: the document_id of an uploaded file."""
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("utf-8")[:length]


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------


def download_s3(pdf_dir, count, month):
    import xml.etree.ElementTree as ET

    import boto3

    s3 = boto3.client("s3", region_name="us-east-1")
    tar_dir = pdf_dir.parent / "tars"
    tar_dir.mkdir(parents=True, exist_ok=True)
    manifest = tar_dir / "arXiv_pdf_manifest.xml"
    if not manifest.exists():
        log("Fetching arXiv PDF manifest (requester pays)")
        s3.download_file("arxiv", "pdf/arXiv_pdf_manifest.xml", str(manifest), ExtraArgs={"RequestPayer": "requester"})
    tars = [
        (f.findtext("filename"), int(f.findtext("num_items")), int(f.findtext("size")))
        for f in ET.parse(manifest).getroot().iter("file")
        if f.findtext("yymm") == month
    ]
    if not tars:
        raise SystemExit(f"No PDF tars for month {month} in the manifest")

    have = {p.name for p in pdf_dir.glob("*.pdf")}
    for key, items, size in sorted(tars):
        if len(have) >= count:
            break
        local = tar_dir / Path(key).name
        if not local.exists():
            log(f"Downloading s3://arxiv/{key} ({items} PDFs, {size / 2 ** 20:.0f} MiB)")
            s3.download_file("arxiv", key, str(local), ExtraArgs={"RequestPayer": "requester"})
        # Keep the newest version of each paper, in arXiv id order.
        with tarfile.open(local) as tar:
            members = {}
            for m in tar.getmembers():
                match = ARXIV_ID.search(m.name)
                if m.isfile() and match:
                    members[match.group(1)] = m
            for paper_id in sorted(members):
                if len(have) >= count:
                    break
                name = Path(members[paper_id].name).name
                if name in have:
                    continue
                (pdf_dir / name).write_bytes(tar.extractfile(members[paper_id]).read())
                have.add(name)
    log(f"{len(have)} PDFs in {pdf_dir}")


def fetch_arxiv_pdf(client, paper_id, target):
    """One PDF from export.arxiv.org, the host arXiv asks harvesters to use (help/bulk_data)."""
    for attempt in range(5):
        r = client.get(f"https://export.arxiv.org/pdf/{paper_id}")
        if r.status_code == 200 and r.content.startswith(b"%PDF"):
            target.write_bytes(r.content)
            return True
        if r.status_code in (429, 500, 502, 503, 504):
            wait = int(r.headers.get("Retry-After", 0) or 0) or 5 * 2 ** attempt
            log(f"{paper_id}: HTTP {r.status_code}, retrying in {wait} s")
            time.sleep(wait)
            continue
        break
    log(f"Skipping {paper_id}: HTTP {r.status_code}")
    return False


def arxiv_client():
    import httpx
    return httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent": USER_AGENT})


def download_api(pdf_dir, count, category):
    """Newest papers of one category, for users without AWS access."""
    import xml.etree.ElementTree as ET

    ns = {"a": "http://www.w3.org/2005/Atom"}
    ids = []
    with arxiv_client() as client:
        start = 0
        while len(ids) < count:
            r = client.get("https://export.arxiv.org/api/query", params={
                "search_query": f"cat:{category}", "sortBy": "submittedDate", "sortOrder": "descending",
                "start": start, "max_results": 200})
            r.raise_for_status()
            entries = ET.fromstring(r.text).findall("a:entry", ns)
            if not entries:
                break
            ids += [e.findtext("a:id", namespaces=ns).rsplit("/abs/", 1)[1] for e in entries]
            start += len(entries)
            time.sleep(3)
        for paper_id in ids[:count]:
            target = pdf_dir / f"{paper_id.replace('/', '_')}.pdf"
            if not target.exists():
                fetch_arxiv_pdf(client, paper_id, target)
                time.sleep(ARXIV_REQUEST_INTERVAL)
    log(f"{len(list(pdf_dir.glob('*.pdf')))} PDFs in {pdf_dir}")


def download_openragbench(work_dir, pdf_dir, count):
    """Vectara's Open RAG Benchmark: its 1000 arXiv papers, plus the queries and relevance labels.

    https://huggingface.co/datasets/vectara/open_ragbench lists the papers in
    pdf_urls.json; queries.json and qrels.json pair 3045 questions with the
    paper that answers each. The PDFs come from export.arxiv.org, one about
    every second, instead of the arxiv.org links in pdf_urls.json.
    """
    meta_dir = work_dir / "openragbench"
    meta_dir.mkdir(parents=True, exist_ok=True)
    with arxiv_client() as client:
        for name in OPENRAGBENCH_FILES:
            target = meta_dir / name
            if not target.exists():
                r = client.get(f"{OPENRAGBENCH_URL}/{name}")
                r.raise_for_status()
                target.write_bytes(r.content)
        paper_ids = sorted(json.loads((meta_dir / "pdf_urls.json").read_text()))[:count]
        todo = [p for p in paper_ids if not (pdf_dir / f"{p}.pdf").exists()]
        log(f"Downloading {len(todo)} of {len(paper_ids)} Open RAG Benchmark papers")
        for i, paper_id in enumerate(todo, 1):
            fetch_arxiv_pdf(client, paper_id, pdf_dir / f"{paper_id}.pdf")
            if i % 50 == 0:
                log(f"downloaded {i}/{len(todo)}")
            time.sleep(ARXIV_REQUEST_INTERVAL)
    missing = [p for p in paper_ids if not (pdf_dir / f"{p}.pdf").exists()]
    log(f"{len(paper_ids) - len(missing)} of {len(paper_ids)} papers in {pdf_dir}"
        + (f"; missing: {', '.join(missing[:10])}" if missing else ""))


def cmd_download(args):
    pdf_dir = args.work_dir / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    if args.source == "openragbench":
        download_openragbench(args.work_dir, pdf_dir, args.count)
    elif args.source == "s3":
        download_s3(pdf_dir, args.count, args.month)
    else:
        download_api(pdf_dir, args.count, args.category)


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------


def docling_convert(client, base_url, pdf):
    """custom_components/openrag/docling_remote.py: async convert, poll, fetch."""
    encoded_pdf = base64.b64encode(pdf.read_bytes()).decode()
    payload = {
        "options": DOCLING_OPTIONS,
        "sources": [{"kind": "file", "base64_string": encoded_pdf, "filename": pdf.name}],
    }
    r = client.post(f"{base_url}/convert/source/async", json=payload)
    r.raise_for_status()
    task = r.json()
    while task["task_status"] not in ("success", "failure"):
        time.sleep(2)
        r = client.get(f"{base_url}/status/poll/{task['task_id']}")
        r.raise_for_status()
        task = r.json()
    r = client.get(f"{base_url}/result/{task['task_id']}")
    r.raise_for_status()
    result = r.json()
    if result.get("status") == "failure" or result.get("errors"):
        raise ValueError(f"Docling failed for {pdf.name}: {result.get('errors')}")
    return result["document"]["json_content"]


def export_pages(json_content):
    """custom_components/openrag/export_docling_document.py: per-page markdown export."""
    import inspect

    from docling_core.types.doc import DoclingDocument, ImageRefMode

    doc = DoclingDocument.model_validate(json_content)
    kwargs = {}
    if "traverse_pictures" in inspect.signature(doc.export_to_markdown).parameters:
        kwargs["traverse_pictures"] = True
    pages = []
    for page_no in sorted((doc.pages or {}).keys()):
        text = doc.export_to_markdown(image_mode=ImageRefMode.PLACEHOLDER, image_placeholder=MD_IMAGE_PLACEHOLDER,
                                      page_no=page_no, **kwargs)
        pages.append({"page": page_no, "text": text})
    if not pages:
        pages.append({"page": None, "text": doc.export_to_markdown(
            image_mode=ImageRefMode.PLACEHOLDER, image_placeholder=MD_IMAGE_PLACEHOLDER, page_break_placeholder="",
            **kwargs)})
    return pages


def cmd_parse(args):
    from concurrent.futures import ThreadPoolExecutor

    import httpx

    pdfs = sorted((args.work_dir / "pdfs").glob("*.pdf"))[: args.count]
    out_dir = args.work_dir / "pages"
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = [p for p in pdfs if not (out_dir / f"{p.stem}.json").exists()]
    base_url = f"{args.docling_url.rstrip('/')}/v1"
    log(f"Parsing {len(todo)} of {len(pdfs)} PDFs with docling-serve at {args.docling_url}")

    def work(pdf):
        try:
            with httpx.Client(timeout=3600) as client:
                pages = export_pages(docling_convert(client, base_url, pdf))
            (out_dir / f"{pdf.stem}.json").write_text(json.dumps(pages))
            return None
        except Exception as e:  # keep going; failed papers are reported and skipped
            return f"{pdf.name}: {e}"

    # OpenRAG's Docling component runs with max_concurrency 2.
    with ThreadPoolExecutor(max_workers=args.docling_concurrency) as pool:
        for i, error in enumerate(pool.map(work, todo), 1):
            if error:
                log(f"FAILED {error}")
            if i % 25 == 0:
                log(f"parsed {i}/{len(todo)}")


# ---------------------------------------------------------------------------
# embed
# ---------------------------------------------------------------------------


def splitter():
    """custom_components/openrag/split_text.py with the ingestion flow's settings."""
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    separators = [SPLIT_SEPARATOR] + [s for s in FALLBACK_SEPARATORS if s != SPLIT_SEPARATOR]
    return RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP,
                                          separators=separators, keep_separator=False)


def embed_texts(client, texts, batch=256):
    vectors = []
    for start in range(0, len(texts), batch):
        for attempt in range(6):
            try:
                resp = client.embeddings.create(model=EMBEDDING_MODEL, input=texts[start:start + batch])
                vectors += [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
                break
            except Exception as e:
                if attempt == 5:
                    raise
                log(f"embedding retry after {e}")
                time.sleep(2 ** attempt)
    return vectors


def chunk_documents(paper_index, pdf, vectors, texts_pages):
    """Chunk documents as DocumentIndexWriter._build_chunk_document writes them."""
    data = pdf.read_bytes()
    document_id = hash_id(data)
    acl = bench_identity.paper_acl(paper_index, pdf.stem)
    indexed_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
    docs = []
    for i, ((text, page), vector) in enumerate(zip(texts_pages, vectors)):
        batch_id, in_batch = i // INGEST_BATCH_SIZE + 1, i % INGEST_BATCH_SIZE
        chunk_id = bench_identity.scoped_chunk_id(acl["owner"], document_id, batch_id, in_batch)
        doc = {
            "document_id": document_id,
            "filename": pdf.name,
            "mimetype": "application/pdf",
            "page": page if page is not None else 0,
            "text": text,
            EMBEDDING_FIELD: vector,
            "embedding_model": EMBEDDING_MODEL,
            "embedding_dimensions": len(vector),
            "file_size": len(data),
            "connector_type": acl["connector_type"],
            "source_url": "",
            "allowed_users": acl["allowed_users"],
            "allowed_groups": acl["allowed_groups"],
            "allowed_principals": acl["allowed_principals"],
            "allowed_principal_labels": acl["allowed_principal_labels"],
            "indexed_time": indexed_time,
            "metadata": {},
            "embedding_provider": EMBEDDING_PROVIDER,
            "embedding_space_id": EMBEDDING_SPACE_ID,
            "parser": PARSER,
            "chunk_size": CHUNK_SIZE,
            "chunk_overlap": CHUNK_OVERLAP,
            "owner": acl["owner"],
            "owner_name": acl["owner_name"],
            "owner_email": acl["owner_email"],
            # OpenRAG appends a random uuid4 hex; a hash keeps rebuilds identical.
            "ingest_run_id": f"{document_id}-{hashlib.md5(document_id.encode()).hexdigest()}",
        }
        docs.append((chunk_id, doc))
    return docs


def cmd_embed(args):
    from openai import OpenAI

    client = OpenAI()
    split = splitter()
    pdfs = sorted((args.work_dir / "pdfs").glob("*.pdf"))[: args.count]
    out_dir = args.work_dir / "chunks"
    out_dir.mkdir(parents=True, exist_ok=True)
    for paper_index, pdf in enumerate(pdfs):
        pages_file = args.work_dir / "pages" / f"{pdf.stem}.json"
        out = out_dir / f"{pdf.stem}.jsonl"
        if out.exists() or not pages_file.exists():
            continue
        texts_pages = []
        for page in json.loads(pages_file.read_text()):
            if page["text"].strip():
                texts_pages += [(chunk, page["page"]) for chunk in split.split_text(page["text"])]
        if not texts_pages:
            log(f"No text in {pdf.name}, skipping")
            continue
        vectors = embed_texts(client, [t for t, _ in texts_pages])
        with out.open("w") as f:
            for chunk_id, doc in chunk_documents(paper_index, pdf, vectors, texts_pages):
                f.write(json.dumps({"_id": chunk_id, "doc": doc}) + "\n")
        if paper_index % 25 == 0:
            log(f"embedded {paper_index + 1}/{len(pdfs)} papers")


# ---------------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------------

QUESTION_PROMPT = """You help build a retrieval benchmark over research papers.
Here is a passage from the paper "{filename}":

<passage>
{text}
</passage>

Return a JSON object with two fields:
- "question": a question a researcher might type into a chat assistant whose answer is in this passage. Do not quote the passage verbatim.
- "search_query": the short keyword query (2 to 6 words) a retrieval agent would send to a search tool to answer that question."""

TOKEN = re.compile(r"\b(?=[A-Za-z0-9.\-_]*[A-Za-z])(?=[A-Za-z0-9.\-_]*\d)[A-Za-z][A-Za-z0-9]*(?:[-_.][A-Za-z0-9]+)+\b")


def load_chunks(work_dir):
    chunks = []
    for f in sorted((work_dir / "chunks").glob("*.jsonl")):
        with f.open() as lines:
            chunks += [json.loads(line)["doc"] for line in lines]
    if not chunks:
        raise SystemExit("No chunks; run the embed stage or scripts/openrag_export.py first")
    return chunks


def openragbench_queries(work_dir, filenames, rng, limit):
    """Vectara's questions, each labelled with the one paper that answers it (qrels.json)."""
    meta_dir = work_dir / "openragbench"
    queries = json.loads((meta_dir / "queries.json").read_text())
    qrels = json.loads((meta_dir / "qrels.json").read_text())
    selected, unanswerable = [], 0
    for query_id in sorted(queries):
        filename = f"{qrels[query_id]['doc_id']}.pdf"
        if filename not in filenames:
            # The paper that answers it is missing from the corpus (download or ingest failed).
            unanswerable += 1
            continue
        query = queries[query_id]
        selected.append({"id": query_id, "type": query["type"], "source": query["source"],
                         "text": query["query"].strip(), "filename": filename})
    if unanswerable:
        log(f"Dropped {unanswerable} queries whose paper is not in the corpus")
    if limit and len(selected) > limit:
        selected = rng.sample(selected, limit)
    return selected


def generated_queries(client, chunks, rng, limit, question_model):
    """Questions an LLM writes from random chunks, their agent-style keyword form, and token lookups."""
    long_chunks = [doc for doc in chunks if len(doc["text"]) >= 400]
    n_questions = int(limit * 0.4)
    n_tokens = int(limit * 0.2)
    queries = []
    for doc in rng.sample(long_chunks, min(n_questions, len(long_chunks))):
        resp = client.chat.completions.create(
            model=question_model, response_format={"type": "json_object"}, temperature=0.7,
            messages=[{"role": "user", "content": QUESTION_PROMPT.format(filename=doc["filename"], text=doc["text"])}])
        generated = json.loads(resp.choices[0].message.content)
        for qtype, key in (("question", "question"), ("keyword", "search_query")):
            if generated.get(key):
                queries.append({"type": qtype, "text": generated[key].strip(), "filename": doc["filename"]})

    # Identifier-like lookups (model names, versions, dataset ids) take OpenRAG's exact-token path.
    tokens = {}
    for doc in long_chunks:
        for token in TOKEN.findall(doc["text"]):
            if 4 <= len(token) <= 40:
                tokens.setdefault(token, doc["filename"])
    for token in rng.sample(sorted(tokens), min(n_tokens, len(tokens))):
        queries.append({"type": "token", "text": token, "filename": tokens[token]})
    return [{"id": str(i), **q} for i, q in enumerate(queries)]


def cmd_queries(args):
    from openai import OpenAI

    client = OpenAI()
    rng = random.Random(args.seed)
    chunks = load_chunks(args.work_dir)
    acl_by_filename = {}
    for doc in chunks:
        acl_by_filename.setdefault(doc["filename"], doc)

    source = args.queries_source
    if source is None:
        source = "openragbench" if (args.work_dir / "openragbench" / "queries.json").exists() else "generated"
    if source == "openragbench":
        queries = openragbench_queries(args.work_dir, set(acl_by_filename), rng, args.num_queries)
    else:
        queries = generated_queries(client, chunks, rng, args.num_queries or 1000, args.question_model)

    vectors = embed_texts(client, [q["text"] for q in queries])
    with (args.work_dir / "queries.jsonl").open("w") as f:
        for q, v in zip(queries, vectors):
            # The paper that answers the query, and the synthetic users DLS lets see it.
            q["relevant_filename"] = q["filename"]
            q["visible_users"] = bench_identity.visible_user_names(acl_by_filename[q["filename"]])
            f.write(json.dumps({**q, "vector": v}) + "\n")
    log(f"Wrote {len(queries)} {source} queries")


# ---------------------------------------------------------------------------
# package
# ---------------------------------------------------------------------------


def cmd_package(args):
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    docs_path = out_dir / "documents.json"
    count = 0
    with docs_path.open("w") as full, (out_dir / "documents-1k.json").open("w") as sample:
        for f in sorted((args.work_dir / "chunks").glob("*.jsonl")):
            for line in f.open():
                record = json.loads(line)
                action = json.dumps({"index": {"_index": INDEX_NAME, "_id": record["_id"]}})
                lines = f"{action}\n{json.dumps(record['doc'])}\n"
                full.write(lines)
                if count < 1000:
                    sample.write(lines)
                count += 1
    stats = {}
    for name in ("documents.json", "documents-1k.json"):
        raw = out_dir / name
        uncompressed = raw.stat().st_size
        with raw.open("rb") as src, bz2.open(out_dir / f"{name}.bz2", "wb") as dst:
            while block := src.read(1 << 24):
                dst.write(block)
        stats[name] = (uncompressed, (out_dir / f"{name}.bz2").stat().st_size)
    with (args.work_dir / "queries.jsonl").open("rb") as src, bz2.open(out_dir / "queries.json.bz2", "wb") as dst:
        dst.write(src.read())

    uncompressed, compressed = stats["documents.json"]
    corpora = {
        "name": "openrag_arxiv_1000",
        "documents": [{
            "source-file": "documents.json.bz2",
            "includes-action-and-meta-data": True,
            "document-count": count,
            "compressed-bytes": compressed,
            "uncompressed-bytes": uncompressed,
        }],
    }
    text = json.dumps(corpora, indent=2)
    # Keep the optional base-url hook in the template.
    text = text.replace('"name": "openrag_arxiv_1000",', '"name": "openrag_arxiv_1000",\n'
                        '  {%- if corpus_base_url is defined %}\n'
                        '  "base-url": "{{ corpus_base_url }}",\n'
                        '  {%- endif %}', 1)
    (WORKLOAD_DIR / "corpora.json").write_text(text + "\n")
    log(f"Packaged {count} chunks into {out_dir}; updated corpora.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=["download", "parse", "embed", "queries", "package", "all"])
    parser.add_argument("--work-dir", type=Path, default=Path("~/openrag-arxiv").expanduser())
    parser.add_argument("--out-dir", type=Path,
                        default=Path("~/.osb/benchmarks/data/openrag_arxiv_1000").expanduser(),
                        help="where OSB looks for the corpus (default: OSB's dataset cache for this corpus)")
    parser.add_argument("--count", type=int, default=1000, help="number of PDFs")
    parser.add_argument("--source", choices=["openragbench", "s3", "api"], default="openragbench",
                        help="openragbench: Vectara's 1000 papers with labelled queries (default); "
                             "s3: one month of the arXiv bulk bucket; api: newest papers of --category")
    parser.add_argument("--month", default="2401", help="arXiv yymm to take PDFs from (s3 source)")
    parser.add_argument("--category", default="cs.CL", help="arXiv category (api source)")
    parser.add_argument("--docling-url", default="http://localhost:5001")
    parser.add_argument("--docling-concurrency", type=int, default=2)
    parser.add_argument("--queries-source", choices=["openragbench", "generated"],
                        help="default: openragbench when its files were downloaded, else generated")
    parser.add_argument("--num-queries", type=int,
                        help="cap on queries (default: all Open RAG Benchmark queries, or 1000 generated)")
    parser.add_argument("--question-model", default="gpt-4o-mini")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    args.work_dir = args.work_dir.expanduser()
    args.out_dir = args.out_dir.expanduser()

    stages = ["download", "parse", "embed", "queries", "package"] if args.stage == "all" else [args.stage]
    for stage in stages:
        log(f"== {stage}")
        globals()[f"cmd_{stage}"](args)


if __name__ == "__main__":
    main()
