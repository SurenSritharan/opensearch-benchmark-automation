# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import bz2
import copy
import json
import logging
import os
import random
import tempfile

from osbenchmark.workload.params import ParamSource

from . import bench_identity, openrag_jwt
from . import openrag_queries as oq

LOGGER = logging.getLogger(__name__)

DEFAULT_EMBEDDING_SPACE = "openai:text-embedding-3-small"
QUERIES_FILE = "queries.json.bz2"

# Query vectors are large, so every param source in a worker process shares one copy.
_QUERY_CACHE = {}


def _queries_candidates(workload):
    """Where OSB keeps this corpus: next to the resolved corpus file, the workload directory, the dataset cache.

    The dataset cache is always last: it is where load_queries downloads to when no candidate exists.
    """
    resolved_dirs = []
    cache_dirs = []
    for corpus in workload.corpora:
        for docs in corpus.documents:
            if docs.document_file and os.path.isabs(docs.document_file):
                resolved_dirs.append(os.path.dirname(docs.document_file))
        cache_dirs.append(os.path.join(os.path.expanduser("~"), ".osb", "benchmarks", "data", corpus.name))
    workload_dir = os.path.dirname(os.path.realpath(__file__))
    return [os.path.join(d, QUERIES_FILE) for d in resolved_dirs + [workload_dir] + cache_dirs]


def load_queries(path, base_url=None):
    """Read queries.json.bz2: one {"text", "type", "filename", "vector"} object per line."""
    path = os.path.expanduser(path)
    if path in _QUERY_CACHE:
        return _QUERY_CACHE[path]
    if not os.path.exists(path):
        if not base_url:
            raise FileNotFoundError(
                f"Query file [{path}] not found. Build it with scripts/build_corpus.py or set "
                f"the corpus_base_url workload parameter."
            )
        from osbenchmark.workload.loader import Downloader
        target_dir = os.path.dirname(path)
        os.makedirs(target_dir, exist_ok=True)
        # Same file name as the final path, because OSB builds the URL from it.
        # A private temp dir on the same filesystem keeps os.replace atomic and
        # stops concurrent workers writing to the same partial file.
        with tempfile.TemporaryDirectory(dir=target_dir) as tmp_dir:
            temp_path = os.path.join(tmp_dir, os.path.basename(path))
            Downloader(False, False).download(base_url, None, temp_path, None)
            os.replace(temp_path, path)

    opener = bz2.open if path.endswith(".bz2") else open
    with opener(path, "rt", encoding="utf-8") as f:
        queries = [json.loads(line) for line in f if line.strip()]
    if not queries:
        raise ValueError(f"Query file [{path}] is empty")
    _QUERY_CACHE[path] = queries
    return queries


class OpenRagSearchParamSource(ParamSource):
    """Draws a query and builds what OpenRAG would send for it.

    Parameters (all optional):

    * ``path``: ``api`` (SearchService) or ``langflow`` (agent tool component).
    * ``variant``: ``hybrid``, ``keyword-only`` (OpenRAG's fallback when query
      embedding fails), ``wildcard`` (the ``*`` facet request, api only) or
      ``knn-only`` (baseline, not an OpenRAG request).
    * ``limit``, ``score-threshold``, ``result-mode`` (api: ``chunks`` or
      ``knowledge_sources``), ``num-candidates`` (0 omits it).
    * ``filter``: ``none``, ``own-file`` (scope to the paper that answers the
      query) or ``knowledge-filter`` (that paper and two random others), the way
      a saved knowledge filter scopes a search.
    * ``auth``: ``admin`` uses the benchmark client's credentials, ``dls`` sends
      each request with the JWT of a synthetic OpenRAG user, so the security
      plugin applies OpenRAG's DLS roles. Needs ``dls-jwt-private-key``. The
      user is one DLS lets see the paper that answers the query, so relevance
      stays comparable with admin; ``dls-user-choice: random`` picks any user.
    * ``query-types``: restrict to some query types: ``abstractive`` and
      ``extractive`` for Open RAG Benchmark queries; ``question``, ``keyword``
      and ``token`` for generated ones.
    * ``measure-relevance``: report whether the answering paper is among the
      results (``recall@k``) and first (``recall@1``); openrag-search only.

    The returned ``body`` serves the built-in ``search`` operation (one request).
    The ``openrag`` entry feeds the ``openrag-search`` runner, which replays the
    whole sequence of requests OpenRAG sends for one search.
    """

    def __init__(self, workload, params, **kwargs):
        super().__init__(workload, params, **kwargs)
        self._index = params.get("index", workload.indices[0].name)
        self._path = params.get("path", "api")
        self._variant = params.get("variant", "hybrid")
        self._limit = int(params.get("limit", 100 if params.get("result-mode") == "knowledge_sources" else 10))
        self._score_threshold = float(params.get("score-threshold", 0))
        self._result_mode = params.get("result-mode", "chunks")
        self._num_candidates = int(params.get("num-candidates", oq.KNN_NUM_CANDIDATES))
        self._filter = params.get("filter", "none")
        self._auth = params.get("auth", "admin")
        self._space_id = params.get("embedding-space-id", DEFAULT_EMBEDDING_SPACE)
        self._field = oq.embedding_field_name(self._space_id)
        self._detailed_results = params.get("detailed-results", False)
        self._request_timeout = params.get("request-timeout")
        self._seed = int(params.get("seed", 42))
        self._rng = random.Random(self._seed)

        if self._path not in ("api", "langflow"):
            raise ValueError(f"Unknown OpenRAG search path [{self._path}]")
        if self._variant == "wildcard" and self._path != "api":
            raise ValueError("The wildcard facet request only exists on the api path")

        self._workload = workload
        self._queries_path = params.get("queries-path")
        self._corpus_base_url = params.get("corpus-base-url")
        self._query_types = params.get("query-types")
        self._queries = None
        self._filenames = None

        self._measure_relevance = params.get("measure-relevance", True)
        self._dls_users = int(params.get("dls-users", bench_identity.NUM_USERS))
        self._dls_user_choice = params.get("dls-user-choice", "can-see")
        self._dls_key_path = params.get("dls-jwt-private-key")
        self._dls_issuer = params.get("dls-jwt-issuer", openrag_jwt.DEFAULT_ISSUER)
        self._dls_tokens = None
        self._warned_user_fallback = set()
        if self._auth == "dls" and not self._dls_key_path:
            raise ValueError("auth [dls] needs the dls_jwt_private_key workload parameter")

    def _load(self):
        # Deferred until the workers run: OSB builds param sources before it
        # resolves where the corpus files are.
        if self._queries_path:
            path = self._queries_path
        else:
            candidates = _queries_candidates(self._workload)
            path = next((c for c in candidates if os.path.exists(c)), candidates[-1])
        all_queries = load_queries(path, self._corpus_base_url)
        self._queries = [q for q in all_queries if not self._query_types or q.get("type") in self._query_types]
        if not self._queries:
            raise ValueError(f"No queries of types {self._query_types} in [{path}]")
        self._filenames = sorted({q["filename"] for q in all_queries if q.get("filename")})
        if self._auth == "dls":
            # One token per user, reused like an OpenRAG session reuses its JWT.
            key = openrag_jwt.load_private_key(os.path.expanduser(self._dls_key_path))
            self._dls_tokens = [
                openrag_jwt.openrag_user_token(
                    bench_identity.user_name(i), bench_identity.user_email(i),
                    bench_identity.user_display_name(i), key, issuer=self._dls_issuer)
                for i in range(self._dls_users)
            ]

    def partition(self, partition_index, total_partitions):
        if self._queries is None:
            self._load()
        # Each client draws its own reproducible sequence of queries and users.
        partition = copy.copy(self)
        partition._rng = random.Random(self._seed + partition_index)
        partition._warned_user_fallback = set()
        return partition

    def _filters(self, query):
        own = query.get("relevant_filename") or query.get("filename")
        if self._filter == "own-file" and own:
            return {"data_sources": [own]}
        if self._filter == "knowledge-filter":
            others = [f for f in self._filenames if f != own]
            picked = self._rng.sample(others, min(2 if own else 3, len(others)))
            return {"data_sources": sorted(picked + ([own] if own else []))}
        return {}

    def _dls_user(self, query):
        """Index of the synthetic user to search as."""
        visible = set(query.get("visible_users") or [])
        if self._dls_user_choice == "can-see":
            if not visible:
                self._warn_user_fallback("no-visible-users", query)
            else:
                candidates = [i for i in range(self._dls_users) if bench_identity.user_name(i) in visible]
                if candidates:
                    return self._rng.choice(candidates)
                self._warn_user_fallback("no-matching-user", query)
        return self._rng.randrange(self._dls_users)

    def _warn_user_fallback(self, reason, query):
        """dls-user-choice=can-see could not honour its promise, so a random user is used.

        Logged once per reason per client, as the fallback can hit every query.
        """
        if reason in self._warned_user_fallback:
            return
        self._warned_user_fallback.add(reason)
        if reason == "no-visible-users":
            detail = "the query has no visible_users"
        else:
            detail = f"none of the query's visible_users is among the {self._dls_users} synthetic users"
        LOGGER.warning(
            "dls-user-choice is [can-see] but %s (paper [%s]); searching as a random user, "
            "so relevance may drop. Further occurrences of this case are not logged.",
            detail, query.get("filename"))

    def params(self):
        if self._queries is None:
            self._load()

        query = self._rng.choice(self._queries)
        text = "*" if self._variant == "wildcard" else query["text"]
        vectors = {self._space_id: query["vector"]}
        filters = self._filters(query)

        if self._path == "api":
            filter_clauses = oq.api_filter_clauses(filters, self._result_mode)
        else:
            filter_clauses = oq.langflow_filter_clauses(filters)

        if self._variant == "knn-only":
            body = oq.knn_only_body(self._field, query["vector"], limit=self._limit)
        else:
            field_vectors = {} if self._variant in ("keyword-only", "wildcard") else {self._field: query["vector"]}
            body = self._search_body(text, field_vectors, filter_clauses)

        headers = None
        user = None
        if self._auth == "dls":
            user = self._dls_user(query)
            # Lower case, so it replaces the basic-auth header opensearch-py adds
            # from the client options instead of being sent next to it.
            headers = {"authorization": self._dls_tokens[user]}
            user = bench_identity.user_name(user)

        result = {
            "index": self._index,
            "body": body,
            "request-params": {"terminate_after": 0},
            "detailed-results": self._detailed_results,
            "openrag": {
                "path": self._path,
                "variant": self._variant,
                "query": text,
                "vectors": {} if self._variant == "keyword-only" else vectors,
                "default-space-id": self._space_id,
                "filter-clauses": filter_clauses,
                "limit": self._limit,
                "score-threshold": self._score_threshold,
                "num-candidates": self._num_candidates,
                "user": user,
                "relevant-filename": (query.get("relevant_filename") or query.get("filename"))
                if self._measure_relevance and self._variant != "wildcard" else None,
            },
        }
        if headers:
            result["headers"] = headers
        if self._request_timeout is not None:
            result["request-timeout"] = self._request_timeout
        return result

    def _search_body(self, text, field_vectors, filter_clauses):
        if self._path == "api":
            return oq.api_search_body(
                text, field_vectors, filter_clauses,
                limit=self._limit, score_threshold=self._score_threshold, num_candidates=self._num_candidates,
            )
        return oq.langflow_search_body(
            text, field_vectors, filter_clauses,
            limit=self._limit,
            score_threshold=self._score_threshold if self._score_threshold > 0 else None,
            num_candidates=self._num_candidates,
            # Admins read OpenRAG's mapping; DLS users cannot (see langflow_filename_agg_field).
            filename_agg_field=oq.langflow_filename_agg_field(
                None if self._auth == "dls" else {"filename": {"type": "keyword"}}),
        )


class EmbeddingSpaceDiscoveryParamSource(OpenRagSearchParamSource):
    """The first discovery page OpenRAG sends before every search, as one request."""

    def params(self):
        result = super().params()
        result["body"] = oq.discovery_body(result["openrag"]["filter-clauses"], legacy=False, after=None)
        return result
