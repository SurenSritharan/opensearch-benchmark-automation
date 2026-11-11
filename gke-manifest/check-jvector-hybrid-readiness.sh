#!/usr/bin/env bash
# Read-only inventory for the JVector native-hybrid comparison.
# Run while the OpenSearch cluster is already up.
set -euo pipefail

if [[ $# -ne 0 && $# -ne 2 ]]; then
  echo "Usage: $0 [plugins.json search-pipelines.json]" >&2
  exit 2
fi

if [[ $# -eq 2 ]]; then
  plugins_file=$1
  processors_file=$2
else
  os_namespace=${OS_NAMESPACE:-os-develop-jvector}
  os_pod=${OS_POD:-opensearch-data-0}
  os_host="opensearch-cluster.${os_namespace}.svc.cluster.local:9200"
  cert_dir=/usr/share/opensearch/config/certs
  scratch_dir=$(mktemp -d)
  trap 'rm -rf "$scratch_dir"' EXIT
  plugins_file="$scratch_dir/plugins.json"
  processors_file="$scratch_dir/search-pipelines.json"

  kubectl exec -n "$os_namespace" "$os_pod" -c opensearch -- \
    curl -sSf --max-time 20 --cert "$cert_dir/admin.pem" --key "$cert_dir/admin-key.pem" \
    --cacert "$cert_dir/root-ca.pem" "https://${os_host}/_nodes/plugins" > "$plugins_file"
  kubectl exec -n "$os_namespace" "$os_pod" -c opensearch -- \
    curl -sSf --max-time 20 --cert "$cert_dir/admin.pem" --key "$cert_dir/admin-key.pem" \
    --cacert "$cert_dir/root-ca.pem" "https://${os_host}/_nodes/search_pipelines" > "$processors_file"
fi

summary=$(jq -n --slurpfile plugins "$plugins_file" --slurpfile processors "$processors_file" '
  ($plugins[0]) as $p |
  ($processors[0]) as $s |
  ($p.nodes | to_entries | map(
    . as $node |
    {
      name: $node.value.name,
      roles: $node.value.roles,
      opensearch_version: $node.value.version,
      jvector: ([$node.value.plugins[]? | select(.name | contains("jvector") and (contains("neural-search") | not)) | {name, version, opensearch_version}]),
      neural_search: ([$node.value.plugins[]? | select(.name | contains("neural-search")) | {name, version, opensearch_version}]),
      normalization_processor: (
        [$s.nodes[$node.key].search_pipelines | .. | objects | .type? // empty]
        | index("normalization-processor") != null
      )
    }
  )) as $nodes |
  {
    plugin_nodes: $p._nodes.total,
    processor_nodes: $s._nodes.total,
    nodes: $nodes,
    ready: (
      $p._nodes.failed == 0 and $s._nodes.failed == 0 and
      $p._nodes.total == $s._nodes.total and
      ($nodes | length) == $p._nodes.total and
      all($nodes[]; (.jvector | length) > 0 and (.neural_search | length) > 0 and .normalization_processor)
    )
  }
')
printf '%s\n' "$summary"
jq -e '.ready' <<< "$summary" >/dev/null
