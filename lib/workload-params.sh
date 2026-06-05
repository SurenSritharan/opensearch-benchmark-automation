#!/bin/bash
# Workload parameter builders for OpenSearch Benchmark
# Note: dataset-configs.sh is sourced by run-benchmark.sh

build_index_params() {
  local engine="$1"
  
  local engine_index_name="${engine}_index"
  local engine_index_body="indices/${engine}-index.json"
  
  # Get dataset-specific parameters
  local dataset="${DATASET:-$(get_default_dataset)}"
  local dimension=$(get_dataset_dimension "$dataset")
  local format=$(get_dataset_format "$dataset")
  local space_type=$(get_dataset_space_type "$dataset")
  
  # Validate dimension is not empty
  if [ -z "$dimension" ] || [ "$dimension" = "null" ]; then
    echo "❌ Error: Failed to get dimension for dataset '$dataset'" >&2
    return 1
  fi
  
  # Validate space_type is not empty
  if [ -z "$space_type" ] || [ "$space_type" = "null" ]; then
    echo "❌ Error: Failed to get space_type for dataset '$dataset'" >&2
    return 1
  fi
  
  # Build index creation and bulk ingestion parameters
  cat <<EOF
{
  "target_index_name": "$engine_index_name",
  "target_field_name": "target_field",
  "target_index_body": "$engine_index_body",
  "target_index_primary_shards": 5,
  "target_index_replica_shards": 0,
  "target_index_dimension": $dimension,
  "target_index_space_type": "$space_type",
  "target_index_bulk_size": 100,
  "target_index_bulk_index_data_set_format": "$format",
  "target_index_bulk_index_data_set_corpus": "$dataset",
  "target_index_bulk_indexing_clients": 4,
  "target_index_max_num_segments": 30,
  "hnsw_ef_construction": 128,
  "hnsw_m": 16,
  "translog_flush_threshold_size": "1g",
  "refresh_interval": -1,
  "max_merged_segment": "5g"
}
EOF
}

build_search_params() {
  local engine="$1"
  local search_clients="$2"
  
  local engine_index_name="${engine}_index"
  local dynamic_repetitions=$search_clients
  
  # Get dataset-specific parameters
  local dataset="${DATASET:-$(get_default_dataset)}"
  local format=$(get_dataset_format "$dataset")
  
  # Validate format is not empty
  if [ -z "$format" ] || [ "$format" = "null" ]; then
    echo "❌ Error: Failed to get format for dataset '$dataset'" >&2
    return 1
  fi

  # Build search-specific parameters
  local params=$(cat <<EOF
{
  "target_index_name": "$engine_index_name",
  "target_field_name": "target_field",
  "query_k": 100,
  "hnsw_ef_search": 128,
  "query_body": {
    "docvalue_fields": ["_id"],
    "stored_fields": "_none_",
    "_source": false
  },
  "query_data_set_format": "$format",
  "query_data_set_corpus": "$dataset",
  "query_count": 5
}
EOF
)
  
  if [ -n "$search_clients" ]; then
    echo "$params" | jq ". + {search_clients: $search_clients}"
  else
    echo "$params"
  fi
}

# Made with Bob
