# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

from osbenchmark.workload.params import BulkIndexParamSource

from .param_sources import EmbeddingSpaceDiscoveryParamSource, OpenRagSearchParamSource
from .runners import register as register_runners


def register(registry):
    register_runners(registry)
    registry.register_param_source("openrag-search-source", OpenRagSearchParamSource)
    registry.register_param_source("openrag-discovery-source", EmbeddingSpaceDiscoveryParamSource)
    # The openrag-bulk operation reads the corpus like the built-in bulk operation.
    registry.register_param_source("openrag-bulk-source", BulkIndexParamSource)
