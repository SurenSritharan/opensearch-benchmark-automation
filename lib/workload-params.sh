#!/bin/bash
# Workload parameter builders for OpenSearch Benchmark

build_index_params() {
  local engine="$1"
  
  local engine_index_name="${engine}_index"
  local engine_index_body="indices/${engine}-index.json"
  
  # Build index creation and bulk ingestion parameters
  cat <<EOF
{
  "target_index_name": "$engine_index_name",
  "target_field_name": "target_field",
  "target_index_body": "$engine_index_body",
  "target_index_primary_shards": 5,
  "target_index_replica_shards": 0,
  "target_index_dimension": 768,
  "target_index_space_type": "l2",
  "target_index_bulk_size": 100,
  "target_index_bulk_index_data_set_format": "hdf5",
  "target_index_bulk_index_data_set_corpus": "cohere-1m",
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
  "query_data_set_format": "hdf5",
  "query_data_set_corpus": "cohere-1m",
  "query_count": 10000
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
