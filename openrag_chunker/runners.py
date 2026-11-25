# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

import datetime
import json
import logging

from opensearchpy.exceptions import NotFoundError, OpenSearchException, RequestError
from osbenchmark import exceptions
from osbenchmark.client import RequestContextHolder
from osbenchmark.worker_coordinator.runner import BulkIndex, Runner

from . import bench_identity
from . import openrag_queries as oq

request_context_holder = RequestContextHolder()


def register(registry):
    registry.register_runner(OpenRagSearchRunner.RUNNER_NAME, OpenRagSearchRunner(), async_runner=True)
    registry.register_runner(OpenRagBulkRunner.RUNNER_NAME, OpenRagBulkRunner(), async_runner=True)
    registry.register_runner(OpenRagSetupDlsRunner.RUNNER_NAME, OpenRagSetupDlsRunner(), async_runner=True)


class OpenRagSearchRunner(Runner):
    """Replays every OpenSearch request of one OpenRAG search.

    api path (SearchService._search):
        discovery pages (qualified, then legacy) -> hybrid search
    langflow path (OpenSearchVectorStoreComponent.search):
        discovery pages (qualified, then legacy) -> GET _mapping -> hybrid search

    Service time spans the first request to the last, including the client-side
    work between them, which is the OpenSearch share of one OpenRAG search.
    Query embedding is precomputed and therefore excluded.
    """

    RUNNER_NAME = "openrag-search"

    async def __call__(self, opensearch, params):
        o = params["openrag"]
        index = params["index"]
        headers = params.get("headers")
        request_params = dict(params.get("request-params", {}))
        if params.get("request-timeout") is not None:
            request_params["request_timeout"] = params["request-timeout"]
        stats = {"requests": 0, "num_candidates_retry": False, "mapping_unavailable": False}

        request_context_holder.on_client_request_start()
        try:
            spaces = await self._discover_spaces(opensearch, index, o["filter-clauses"], request_params, headers, stats)
            properties = None
            if o["path"] == "langflow":
                properties = await self._mapping_properties(opensearch, index, headers, stats)

            vectors_by_field = {} if o["variant"] == "wildcard" else self._vectors_by_field(o, spaces, properties)

            if o["path"] == "api":
                body = oq.api_search_body(
                    o["query"], vectors_by_field, o["filter-clauses"],
                    limit=o["limit"], score_threshold=o["score-threshold"], num_candidates=o["num-candidates"],
                )
            else:
                body = oq.langflow_search_body(
                    o["query"], vectors_by_field, o["filter-clauses"],
                    limit=o["limit"],
                    score_threshold=o["score-threshold"] if o["score-threshold"] > 0 else None,
                    num_candidates=o["num-candidates"],
                    filename_agg_field=oq.langflow_filename_agg_field(properties),
                )

            response = await self._hybrid_search(opensearch, index, body, o, vectors_by_field,
                                                 request_params, headers, stats)
        finally:
            request_context_holder.on_client_request_end()

        result = {
            "weight": 1,
            "unit": "ops",
            "success": True,
            "requests": stats["requests"],
            "embedding_spaces": len(spaces),
            "knn_fields": len(vectors_by_field),
            "num_candidates_retry": stats["num_candidates_retry"],
            "mapping_unavailable": stats["mapping_unavailable"],
        }
        hits = response.get("hits", {}).get("hits", [])
        if o.get("relevant-filename"):
            # Each query has one paper that answers it. OSB reports the mean of
            # these as recall@k (that paper is among the results, k = limit) and
            # recall@1 (it is the top result). The results parsed here arrived
            # after the last request ended, so service time does not include this.
            filenames = [hit.get("_source", {}).get("filename") for hit in hits]
            result["recall@k"] = 1.0 if o["relevant-filename"] in filenames else 0.0
            result["recall@1"] = 1.0 if filenames[:1] == [o["relevant-filename"]] else 0.0
        if params.get("detailed-results"):
            result.update({
                "hits": len(hits),
                "took": response.get("took"),
                "timed_out": response.get("timed_out", False),
            })
        return result

    async def _search(self, opensearch, index, body, request_params, headers, stats):
        stats["requests"] += 1
        return await opensearch.search(index=index, body=body, params=request_params, headers=headers)

    async def _discover_spaces(self, opensearch, index, filter_clauses, request_params, headers, stats):
        """The composite-aggregation pagination loop both OpenRAG paths run."""
        spaces = []
        seen = set()
        for legacy in (False, True):
            after = None
            while True:
                body = oq.discovery_body(filter_clauses, legacy=legacy, after=after)
                response = await self._search(opensearch, index, body, request_params, headers, stats)
                for space_id, field_identity in oq.discovered_field_identities(response):
                    if space_id not in seen:
                        seen.add(space_id)
                        spaces.append((space_id, field_identity))
                qualified_after, legacy_after = oq.discovery_after_keys(response)
                next_after = legacy_after if legacy else qualified_after
                if not next_after or next_after == after:
                    break
                after = next_after
        return spaces

    async def _mapping_properties(self, opensearch, index, headers, stats):
        stats["requests"] += 1
        try:
            mapping = await opensearch.indices.get_mapping(index=index, headers=headers)
        except OpenSearchException:
            # _get_index_properties logs and continues without mapping metadata.
            # DLS users always land here: filter-level DLS rejects GetMappings.
            stats["mapping_unavailable"] = True
            return None
        properties = {}
        for index_data in mapping.values():
            properties.update(index_data.get("mappings", {}).get("properties", {}))
        return properties

    def _vectors_by_field(self, o, spaces, properties):
        vectors = o["vectors"]
        if not spaces and o["path"] == "api" and vectors:
            # SearchService falls back to the configured model when discovery finds nothing.
            spaces = [(o["default-space-id"], o["default-space-id"])]
        vectors_by_field = {}
        for space_id, field_identity in spaces:
            # A space without a precomputed vector is what OpenRAG does when that
            # model fails to embed the query: it is left out of the knn clauses.
            vector = vectors.get(space_id)
            if vector is None:
                continue
            field = oq.embedding_field_name(field_identity)
            if properties is not None:
                # The Langflow component only queries fields mapped as knn_vector
                # with the query vector's dimension.
                field_def = properties.get(field)
                if not isinstance(field_def, dict) or field_def.get("type") != "knn_vector":
                    continue
                if field_def.get("dimension") not in (None, len(vector)):
                    continue
            vectors_by_field[field] = vector
        return vectors_by_field

    async def _hybrid_search(self, opensearch, index, body, o, vectors_by_field, request_params, headers, stats):
        try:
            return await self._search(opensearch, index, body, request_params, headers, stats)
        except RequestError as e:
            message = str(e).lower()
            marker = "unknown field [num_candidates]" if o["path"] == "api" else "num_candidates"
            retry = marker in message
            if not (vectors_by_field and o["num-candidates"] and retry):
                raise
            # OpenRAG retries every such search without num_candidates.
            stats["num_candidates_retry"] = True
            return await self._search(opensearch, index, oq.without_num_candidates(body),
                                      request_params, headers, stats)

    def __repr__(self, *args, **kwargs):
        return self.RUNNER_NAME


class OpenRagBulkRunner(Runner):
    """Bulk indexing as OpenRAG's DocumentIndexWriter does it.

    OpenRAG writes each file's chunks in bulk batches of
    OPENRAG_INGEST_BATCH_SIZE (100) with refresh=false. The benchmark has no
    concurrent search during ingestion, so refreshing after every batch is not
    needed. ``refresh-every-docs`` instead refreshes the index each time that
    many documents have been ingested since the last refresh (0 disables it).
    The count is kept per runner instance, i.e. per worker process, and a
    separate refresh operation after ingestion makes the remaining documents
    visible.
    """

    RUNNER_NAME = "openrag-bulk"

    def __init__(self):
        super().__init__()
        self._bulk = BulkIndex()
        self._docs_since_refresh = 0

    async def __call__(self, opensearch, params):
        refresh_index = params.pop("refresh-index", None)
        refresh_every_docs = int(params.pop("refresh-every-docs", 0) or 0)
        result = await self._bulk(opensearch, params)
        if refresh_every_docs > 0 and refresh_index:
            self._docs_since_refresh += int(result.get("weight", 0))
            if self._docs_since_refresh >= refresh_every_docs:
                # Reset before awaiting so concurrent clients do not refresh twice.
                self._docs_since_refresh = 0
                await opensearch.indices.refresh(index=refresh_index)
                request_context_holder.on_client_request_end()
        return result

    def __repr__(self, *args, **kwargs):
        return self.RUNNER_NAME


# OpenRAG securityconfig/roles.yml, limited to the indices the benchmark touches.
OPENRAG_ROLES = {
    "openrag_user_role": {
        "description": "DLS: user can read docs they own or unowned docs",
        "cluster_permissions": ["indices:data/read/scroll", "indices:data/read/scroll/clear", "cluster:monitor/*"],
        "index_permissions": [
            {
                "index_patterns": ["documents", "*documents*", "knowledge_filters", "knowledge_filters*"],
                "allowed_actions": ["read"],
                "dls": json.dumps({"bool": {"should": [
                    {"term": {"owner": "${user.name}"}},
                    {"term": {"owner": "${attr.jwt.email}"}},
                    {"bool": {"must_not": {"exists": {"field": "owner"}}}},
                ], "minimum_should_match": 1}}),
            },
            {
                "index_patterns": ["documents", "*documents*", "knowledge_filters", "knowledge_filters*"],
                "allowed_actions": ["indices:admin/mappings/get", "indices:admin/exists", "indices:admin/get"],
            },
            {
                "index_patterns": ["openrag_dls_principals", "openrag_dls_principals*"],
                "allowed_actions": ["read", "indices:data/read/get", "indices:data/read/search"],
                "dls": json.dumps({"term": {"user_name": "${user.name}"}}),
            },
            {
                "index_patterns": ["openrag_dls_principals", "openrag_dls_principals*"],
                "allowed_actions": ["indices:admin/exists"],
            },
        ],
    },
    "openrag_user_acl_role": {
        "description": "DLS: user can read docs they are allowed on via ACL",
        "cluster_permissions": [],
        "index_permissions": [
            {
                "index_patterns": ["documents", "*documents*", "knowledge_filters", "knowledge_filters*"],
                "allowed_actions": ["read"],
                "dls": json.dumps({"bool": {"should": [
                    {"term": {"allowed_users": "${user.name}"}},
                    {"term": {"allowed_users": "${attr.jwt.email}"}},
                    {"terms": {"allowed_principals": {
                        "index": "openrag_dls_principals", "id": "${user.name}", "path": "principals"}}},
                ], "minimum_should_match": 1}}),
            },
        ],
    },
}

# OpenRAG config.settings.DLS_PRINCIPAL_INDEX_BODY
ACL_PRINCIPAL_LABELS_MAPPING = {
    "type": "object",
    "properties": {
        "principal": {"type": "keyword"},
        "kind": {"type": "keyword"},
        "provider": {"type": "keyword"},
        "display_name": {"type": "keyword"},
        "email": {"type": "keyword"},
        "external_id": {"type": "keyword"},
    },
}
DLS_PRINCIPAL_INDEX_BODY = {
    "settings": {"index": {"number_of_replicas": 0, "number_of_shards": 1}},
    "mappings": {
        "properties": {
            "user_name": {"type": "keyword"},
            "auth_user_id": {"type": "keyword"},
            "auth_email": {"type": "keyword"},
            "provider": {"type": "keyword"},
            "principals": {"type": "keyword"},
            "principal_labels": ACL_PRINCIPAL_LABELS_MAPPING,
            "updated_at": {"type": "date"},
        }
    },
}

SECURITY_API = "/_plugins/_security/api"


class OpenRagSetupDlsRunner(Runner):
    """Prepares the cluster for DLS-mode searches.

    DLS-mode requests carry JWTs whose ``roles`` claim is ``openrag_user``, so
    OpenRAG's own role mappings attach openrag_user_role and
    openrag_user_acl_role, exactly as for a real OpenRAG user. This runner
    checks those roles exist (creating them only with ``create-missing-roles``,
    so an OpenRAG cluster keeps the roles it shipped with) and writes each
    synthetic user's group principals to openrag_dls_principals, which the ACL
    role's terms lookup reads.
    """

    RUNNER_NAME = "openrag-setup-dls"

    async def __call__(self, opensearch, params):
        create_missing_roles = params.get("create-missing-roles", False)
        users = int(params.get("users", bench_identity.NUM_USERS))
        principals_index = bench_identity.PRINCIPALS_INDEX
        transport = opensearch.transport

        for role, definition in OPENRAG_ROLES.items():
            try:
                await transport.perform_request("GET", f"{SECURITY_API}/roles/{role}")
            except NotFoundError:
                if not create_missing_roles:
                    raise exceptions.SystemSetupError(
                        f"Role [{role}] does not exist. Run against OpenRAG's OpenSearch image or set "
                        f"create_missing_openrag_roles:true.") from None
                logging.getLogger(__name__).info("Creating missing OpenRAG role [%s].", role)
                await transport.perform_request("PUT", f"{SECURITY_API}/roles/{role}", body=definition)
                await transport.perform_request("PUT", f"{SECURITY_API}/rolesmapping/{role}",
                                                body={"backend_roles": [bench_identity.OPENRAG_BACKEND_ROLE]})

        if not await opensearch.indices.exists(index=principals_index):
            await opensearch.indices.create(index=principals_index, body=DLS_PRINCIPAL_INDEX_BODY)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        for i in range(users):
            name = bench_identity.user_name(i)
            # services/dls_principal_service.py document shape. Refreshed once below:
            # refresh=wait_for per document waits a refresh interval each and exceeds
            # the client timeout.
            await opensearch.index(
                index=principals_index, id=name,
                body={
                    "user_name": name,
                    "auth_user_id": name,
                    "auth_email": bench_identity.user_email(i),
                    "provider": "benchidp",
                    "principals": bench_identity.user_principals(i),
                    "principal_labels": [],
                    "updated_at": now,
                },
            )
        await opensearch.indices.refresh(index=principals_index)
        return {"weight": users, "unit": "users", "success": True}

    def __repr__(self, *args, **kwargs):
        return self.RUNNER_NAME
