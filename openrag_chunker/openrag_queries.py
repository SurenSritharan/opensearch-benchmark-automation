# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Request bodies OpenRAG sends to OpenSearch, reproduced without its runtime.

Everything here mirrors OpenRAG (https://github.com/langflow-ai/openrag) at
commit f00c103. Two code paths issue retrieval queries:

* ``api``: ``SearchService`` in ``src/services/search_service.py``. It serves
  the search API (``/search``, ``/v1/search``), the Knowledge view and the
  backend ``search_tool``.
* ``langflow``: the ``OpenSearchVectorStoreComponentMultimodalMultiEmbedding``
  component in ``custom_components/openrag/opensearch_multimodal.py``. The chat
  agent in ``flows/openrag_agent.json`` calls it as a tool.

Both paths first discover which embedding spaces the index holds, then send a
``bool`` query whose ``should`` clauses combine a ``dis_max`` of ``knn``
queries with fuzzy keyword matching. They do not use the neural-search
``hybrid`` query or a normalization search pipeline.

When OpenRAG changes, update these builders and the commit above together.
"""

import copy

OPENRAG_COMMIT = "f00c103"

EMBEDDING_SPACE_PAGE_SIZE = 100
INDEXED_EMBEDDING_ROUTE_PREFIX = "space:"
LEGACY_EMBEDDING_ROUTE_PREFIX = "legacy:"

KNN_K = 50
KNN_NUM_CANDIDATES = 1000

# search_service._SEARCH_FILTER_FIELDS
API_FILTER_FIELDS = {
    "data_sources": "filename",
    "document_types": "mimetype",
    "owners": "owner",
    "connector_types": "connector_type",
    "document_ids": "document_id",
    "web_source_ids": "web_source_id",
}
API_MAPPING_COMPATIBLE_FILTER_FIELDS = {"web_source_id"}
API_CHUNK_SEARCH_EXCLUDED_RECORD_KINDS = ["web_page_manifest", "web_source"]

# OpenSearchVectorStoreComponent._coerce_filter_clauses, context-style mapping
LANGFLOW_FILTER_FIELDS = {
    "data_sources": "filename",
    "document_types": "mimetype",
    "owners": "owner",
}

API_SOURCE_FIELDS = [
    "filename",
    "mimetype",
    "page",
    "text",
    "source_url",
    "owner",
    "owner_name",
    "owner_email",
    "file_size",
    "connector_type",
    "embedding_model",
    "embedding_provider",
    "embedding_space_id",
    "embedding_dimensions",
    "parser",
    "chunk_size",
    "chunk_overlap",
    "allowed_users",
    "allowed_groups",
    "allowed_principal_labels",
    "document_id",
    "web_source_id",
    "web_page_id",
    "web_page_depth",
    "web_child_count",
    "chunk_count",
    "canonical_url",
    "status",
    "error",
]

LANGFLOW_SOURCE_FIELDS = [
    "filename",
    "mimetype",
    "page",
    "text",
    "source_url",
    "owner",
    "embedding_model",
    "embedding_provider",
    "embedding_space_id",
    "parser",
    "chunk_size",
    "chunk_overlap",
    "allowed_users",
    "allowed_groups",
    "allowed_principals",
]


def normalize_model_name(model_name):
    """utils.embedding_fields.normalize_model_name"""
    normalized = model_name.lower()
    for separator in ("-", ":", "/", "."):
        normalized = normalized.replace(separator, "_")
    normalized = "".join(c if c.isalnum() or c == "_" else "_" for c in normalized)
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized.strip("_")


def embedding_field_name(field_identity):
    """utils.embedding_fields.get_embedding_field_name"""
    return f"chunk_embedding_{normalize_model_name(field_identity)}"


def embedding_space_id(provider, model_name):
    """utils.embedding_fields.get_embedding_space_id"""
    return f"{provider.strip().lower()}:{model_name.strip()}"


# ---------------------------------------------------------------------------
# Embedding-space discovery (identical aggregation on both paths)
# ---------------------------------------------------------------------------


def discovery_aggregation(*, size, qualified_after=None, legacy_after=None,
                          include_qualified=True, include_legacy=True):
    """utils.embedding_fields.build_embedding_space_aggregation"""
    aggregations = {}
    if include_qualified:
        composite = {
            "size": size,
            "sources": [{"space_id": {"terms": {"field": "embedding_space_id"}}}],
        }
        if qualified_after:
            composite["after"] = qualified_after
        aggregations["embedding_spaces"] = {"composite": composite}
    if include_legacy:
        composite = {
            "size": size,
            "sources": [{"model": {"terms": {"field": "embedding_model"}}}],
        }
        if legacy_after:
            composite["after"] = legacy_after
        aggregations["legacy_embedding_models"] = {"composite": composite}
    return aggregations


def discovery_body(filter_clauses, *, legacy, after):
    """One page of the discovery loop shared by both paths."""
    body = {
        "size": 0,
        "aggs": discovery_aggregation(
            size=EMBEDDING_SPACE_PAGE_SIZE,
            qualified_after=None if legacy else after,
            legacy_after=after if legacy else None,
            include_qualified=not legacy,
            include_legacy=legacy,
        ),
    }
    bool_query = {}
    if filter_clauses:
        bool_query["filter"] = filter_clauses
    if legacy:
        bool_query["must_not"] = [{"exists": {"field": "embedding_space_id"}}]
    if bool_query:
        body["query"] = {"bool": bool_query}
    return body


def discovery_after_keys(response):
    """utils.embedding_fields.embedding_space_after_keys"""
    aggregations = response.get("aggregations", {})
    return (
        aggregations.get("embedding_spaces", {}).get("after_key"),
        aggregations.get("legacy_embedding_models", {}).get("after_key"),
    )


def discovered_field_identities(response):
    """Field identities from utils.embedding_fields.embedding_spaces_from_aggregation.

    Returns ``(space_id, field_identity)`` pairs. Qualified spaces use the space
    id as their field identity, legacy spaces use the bare model name.
    """
    aggregations = response.get("aggregations", {})
    spaces = []
    for bucket in aggregations.get("embedding_spaces", {}).get("buckets", []):
        key = bucket.get("key")
        space_id = str((key.get("space_id") if isinstance(key, dict) else key) or "").strip()
        if space_id:
            spaces.append((space_id, space_id))
    for bucket in aggregations.get("legacy_embedding_models", {}).get("buckets", []):
        key = bucket.get("key")
        model = str((key.get("model") if isinstance(key, dict) else key) or "").strip()
        if model:
            spaces.append((f"{LEGACY_EMBEDDING_ROUTE_PREFIX}{model}", model))
    return spaces


# ---------------------------------------------------------------------------
# API path: SearchService._search
# ---------------------------------------------------------------------------


def api_filter_clauses(filters, result_mode="chunks"):
    """_build_search_filter_clauses followed by _add_result_mode_filter."""
    clauses = []
    for filter_key, values in (filters or {}).items():
        if not isinstance(values, list):
            continue
        field_name = API_FILTER_FIELDS.get(filter_key, filter_key)
        if not values:
            clauses.append({"term": {field_name: "__IMPOSSIBLE_VALUE__"}})
        elif field_name in API_MAPPING_COMPATIBLE_FILTER_FIELDS:
            terms_query = (
                {"term": {field_name: values[0]}}
                if len(values) == 1
                else {"terms": {field_name: values}}
            )
            keyword_field = f"{field_name}.keyword"
            keyword_query = (
                {"term": {keyword_field: values[0]}}
                if len(values) == 1
                else {"terms": {keyword_field: values}}
            )
            clauses.append({"bool": {"should": [terms_query, keyword_query], "minimum_should_match": 1}})
        elif len(values) == 1:
            clauses.append({"term": {field_name: values[0]}})
        else:
            clauses.append({"terms": {field_name: values}})
    if result_mode == "chunks":
        clauses.append(
            {"bool": {"must_not": [{"terms": {"record_kind": API_CHUNK_SEARCH_EXCLUDED_RECORD_KINDS}}]}}
        )
    return clauses


def api_facet_aggregations():
    """_build_file_facet_aggregations"""
    facet_fields = {
        "data_sources": "filename",
        "document_types": "mimetype",
        "owners": "owner",
        "connector_types": "connector_type",
        "embedding_models": "embedding_model",
    }
    aggregations = {}
    for facet_name, field_name in facet_fields.items():
        agg = {"terms": {"field": field_name, "size": 1000}}
        if facet_name == "owners":
            agg["aggs"] = {
                "files": {"cardinality": {"field": "filename"}},
                "owner_metadata": {
                    "top_hits": {
                        "size": 1,
                        "_source": {"includes": ["owner_name", "owner_email", "owner"]},
                    }
                },
            }
        elif facet_name != "data_sources":
            agg["aggs"] = {"files": {"cardinality": {"field": "filename"}}}
        aggregations[facet_name] = agg
    return aggregations


def knn_clause(field_name, vector, num_candidates):
    params = {"vector": vector, "k": KNN_K}
    if num_candidates:
        params["num_candidates"] = num_candidates
    return {"knn": {field_name: params}}


def api_search_body(query, vectors_by_field, filter_clauses, *, limit=10, score_threshold=0,
                    num_candidates=KNN_NUM_CANDIDATES):
    """The body SearchService._search sends for a query.

    ``vectors_by_field`` maps knn field names to query vectors; an empty map
    produces the keyword-only fallback OpenRAG uses when embedding fails.
    ``query == "*"`` produces the wildcard facet request of the Knowledge view.
    """
    is_wildcard = query.strip() == "*"
    if is_wildcard:
        query_block = {"bool": {"filter": filter_clauses}} if filter_clauses else {"match_all": {}}
    else:
        knn_queries = [knn_clause(f, v, num_candidates) for f, v in vectors_by_field.items()]
        should_clauses = []
        if knn_queries:
            should_clauses.append(
                {"dis_max": {"tie_breaker": 0.0, "boost": 0.7, "queries": knn_queries}}
            )
        should_clauses.extend(
            [
                {
                    "multi_match": {
                        "query": query,
                        "fields": ["text^2", "filename^1.5"],
                        "type": "best_fields",
                        "operator": "or",
                        "fuzziness": "AUTO:4,7",
                        "boost": 0.3 if knn_queries else 1.0,
                    }
                },
                {
                    "match_phrase_prefix": {
                        "text": {"query": query, "max_expansions": 50, "boost": 0.25}
                    }
                },
            ]
        )
        query_block = {
            "bool": {
                "should": should_clauses,
                "minimum_should_match": 1,
                "filter": list(filter_clauses),
            }
        }
        if score_threshold > 0:
            query_block = {"function_score": {"query": query_block, "min_score": score_threshold}}

    body = {
        "query": query_block,
        "aggs": api_facet_aggregations(),
        "_source": list(API_SOURCE_FIELDS),
        "size": limit,
    }
    if not is_wildcard:
        body["highlight"] = {
            "type": "unified",
            "fields": {
                "text": {
                    "fragment_size": 200,
                    "number_of_fragments": 3,
                    "pre_tags": ["<mark>"],
                    "post_tags": ["</mark>"],
                }
            },
            "require_field_match": False,
        }
    return body


# ---------------------------------------------------------------------------
# Langflow path: OpenSearchVectorStoreComponentMultimodalMultiEmbedding.search
# ---------------------------------------------------------------------------


def langflow_filter_clauses(filter_obj):
    """_coerce_filter_clauses for a parsed filter_expression (or None)."""
    if not filter_obj:
        return []
    if "filter" in filter_obj:
        raw = filter_obj["filter"]
        if isinstance(raw, dict):
            raw = [raw]
        clauses = []
        for f in raw or []:
            if "term" in f and isinstance(f["term"], dict):
                if not any(v == "__IMPOSSIBLE_VALUE__" for v in f["term"].values()):
                    clauses.append(f)
            elif "terms" in f and isinstance(f["terms"], dict):
                _, vals = next(iter(f["terms"].items()))
                if isinstance(vals, list) and vals:
                    clauses.append(f)
        return clauses
    clauses = []
    for key, values in filter_obj.items():
        if not isinstance(values, list):
            continue
        field = LANGFLOW_FILTER_FIELDS.get(key, key)
        if not values:
            clauses.append({"term": {field: "__IMPOSSIBLE_VALUE__"}})
        elif len(values) == 1:
            if values[0] != "__IMPOSSIBLE_VALUE__":
                clauses.append({"term": {field: values[0]}})
        else:
            clauses.append({"terms": {field: values}})
    return clauses


def langflow_filename_agg_field(properties):
    """_get_filename_agg_field for flattened mapping properties (None if unavailable).

    With OpenRAG's mapping this is "filename". DLS users cannot read the
    mapping (filter-level DLS rejects GetMappings), so for them the component
    falls back to "filename.keyword", a field that does not exist.
    """
    if not properties:
        return "filename.keyword"
    filename_def = properties.get("filename")
    if not isinstance(filename_def, dict):
        return "filename.keyword"
    fields_def = filename_def.get("fields", {})
    if filename_def.get("type") == "keyword" and not isinstance(fields_def, dict):
        return "filename"
    if isinstance(fields_def, dict) and "keyword" in fields_def:
        return "filename.keyword"
    return "filename"


def langflow_search_body(query, vectors_by_field, filter_clauses, *, limit=10, score_threshold=None,
                         num_candidates=KNN_NUM_CANDIDATES, filename_agg_field="filename"):
    """The body the Langflow OpenSearch component sends for a query.

    ``filename_agg_field`` comes from langflow_filename_agg_field.
    """
    query = (query or "").strip()
    knn_queries = [knn_clause(f, v, num_candidates) for f, v in vectors_by_field.items()]
    keyword_query = {
        "multi_match": {
            "query": query,
            "fields": ["text^2", "filename^1.5"],
            "type": "best_fields",
            "fuzziness": "AUTO",
            "boost": 0.3 if knn_queries else 1.0,
        }
    }
    should_queries = [keyword_query]
    if knn_queries:
        should_queries.insert(
            0, {"dis_max": {"tie_breaker": 0.0, "boost": 0.7, "queries": knn_queries}}
        )
    body = {
        "query": {
            "bool": {
                "should": should_queries,
                "minimum_should_match": 1,
                "filter": list(filter_clauses),
            }
        },
        "aggs": {
            "data_sources": {"terms": {"field": filename_agg_field, "size": 20}},
            "document_types": {"terms": {"field": "mimetype", "size": 10}},
            "owners": {"terms": {"field": "owner", "size": 10}},
            "embedding_models": {"terms": {"field": "embedding_model", "size": 10}},
        },
        "_source": list(LANGFLOW_SOURCE_FIELDS),
        "size": limit,
    }
    if score_threshold is not None:
        body["min_score"] = score_threshold
    return body


def without_num_candidates(body):
    """The retry body both paths send when the cluster rejects num_candidates."""
    fallback = copy.deepcopy(body)
    query = fallback["query"]
    query = query.get("function_score", {}).get("query", query)
    for clause in query["bool"]["should"]:
        for knn in clause.get("dis_max", {}).get("queries", []):
            for params in knn.get("knn", {}).values():
                params.pop("num_candidates", None)
    return fallback


# ---------------------------------------------------------------------------
# Baselines that are not OpenRAG requests
# ---------------------------------------------------------------------------


def knn_only_body(field_name, vector, *, limit=10):
    """Pure vector retrieval with OpenRAG's k, for comparison only."""
    return {
        "query": {"knn": {field_name: {"vector": vector, "k": KNN_K}}},
        "_source": list(API_SOURCE_FIELDS),
        "size": limit,
    }
