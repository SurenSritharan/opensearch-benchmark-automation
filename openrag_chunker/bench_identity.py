# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""Synthetic OpenRAG users and document ACLs shared by the corpus and DLS setup.

The corpus builder stamps each paper with an owner and ACL fields derived from
its position in the corpus. DLS-mode requests carry JWTs for the same users,
so OpenRAG's DLS roles resolve ``${user.name}`` and ``${attr.jwt.email}`` to
values that actually occur in the index. Changing anything here requires
rebuilding the corpus.
"""

import hashlib

NUM_USERS = 10
NUM_GROUPS = 3
# Fraction of papers simulated as shared through a connector (for example
# Google Drive) instead of uploaded locally.
CONNECTOR_SHARED_FRACTION = 0.3

USER_PREFIX = "openrag_bench_user_"
# OpenRAG backend role that maps users onto openrag_user_role and
# openrag_user_acl_role (securityconfig/roles_mapping.yml).
OPENRAG_BACKEND_ROLE = "openrag_user"
PRINCIPALS_INDEX = "openrag_dls_principals"
CONNECTOR_TYPE = "google_drive"


def user_name(i):
    return f"{USER_PREFIX}{i}"


def all_user_names():
    return [user_name(i) for i in range(NUM_USERS)]


def user_display_name(i):
    return f"OpenRAG Bench User {i}"


def user_email(i):
    return f"{user_name(i)}@openrag-bench.invalid"


def group_principal(g):
    """utils.group_acl.canonical_group_role format: g:<provider>:<tenant>:<group>"""
    return f"g:benchidp:global:team{g}"


def user_group(i):
    return i % NUM_GROUPS


def user_principals(i):
    return [group_principal(user_group(i))]


def _unit_hash(value):
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2 ** 64


def paper_acl(paper_index, paper_id):
    """Ownership and ACL fields for every chunk of one paper.

    Local uploads carry only an owner, like OpenRAG's upload path. Connector
    papers additionally grant one other user and one group, which exercises
    the allowed_users term and the allowed_principals terms lookup in
    openrag_user_acl_role.
    """
    owner_index = paper_index % NUM_USERS
    owner = user_name(owner_index)
    acl = {
        "owner": owner,
        "owner_name": user_display_name(owner_index),
        "owner_email": user_email(owner_index),
        "connector_type": "local",
        "allowed_users": [],
        "allowed_groups": [],
        "allowed_principals": [],
        "allowed_principal_labels": [],
    }
    if _unit_hash(paper_id) < CONNECTOR_SHARED_FRACTION:
        group = (paper_index // NUM_USERS) % NUM_GROUPS
        acl.update(
            {
                "connector_type": CONNECTOR_TYPE,
                "allowed_users": [owner, user_name((owner_index + 1) % NUM_USERS)],
                "allowed_principals": [group_principal(group)],
                "allowed_principal_labels": [
                    {
                        "principal": group_principal(group),
                        "kind": "group",
                        "provider": "benchidp",
                        "display_name": f"Team {group}",
                    }
                ],
            }
        )
    return acl


def can_see(i, doc):
    """OpenRAG's two DLS roles (securityconfig/roles.yml) evaluated for synthetic user i."""
    name, email = user_name(i), user_email(i)
    owner = doc.get("owner")
    if owner is None or owner in (name, email):
        return True
    allowed_users = doc.get("allowed_users") or []
    if name in allowed_users or email in allowed_users:
        return True
    return bool(set(doc.get("allowed_principals") or []) & set(user_principals(i)))


def visible_user_names(doc):
    return [user_name(i) for i in range(NUM_USERS) if can_see(i, doc)]


def owner_scope_digest(owner):
    """DocumentIndexWriter._scoped_chunk_id: the owner part of an OpenRAG chunk _id."""
    scope = "shared" if owner is None else f"owner:{owner}"
    return hashlib.sha256(scope.encode("utf-8")).hexdigest()[:24]


def scoped_chunk_id(owner, document_id, batch_id, index):
    """DocumentIndexWriter._scoped_chunk_id over langflow_ingest._authorized_chunk_id."""
    return f"{owner_scope_digest(owner)}_{document_id}_{batch_id}_{index}"
