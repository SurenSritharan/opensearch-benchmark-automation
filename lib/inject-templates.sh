#!/bin/bash
# Index template injection functions

inject_jvector_template() {
  local namespace="$1"
  
  kubectl exec -i opensearch-benchmark-client -n "$namespace" -c benchmark -- bash -c 'cat > /root/opensearch-benchmark-workloads/vectorsearch/indices/jvector-index.json' << 'EOF'
{
  "settings": {
    "index": {
      {%- if target_index_primary_shards is defined and target_index_primary_shards %}
      "number_of_shards": {{ target_index_primary_shards }},
      {%- endif %}
      {%- if target_index_replica_shards is defined %}
      "number_of_replicas": {{ target_index_replica_shards }},
      {%- endif %}
      {%- if translog_flush_threshold_size is defined %}
      "translog.flush_threshold_size": "{{ translog_flush_threshold_size }}",
      {%- endif %}
      {%- if refresh_interval is defined %}
      "refresh_interval": "{{ refresh_interval }}",
      {%- endif %}
      {%- if max_merged_segment is defined %}
      "merge": {
        "policy": {
          "max_merged_segment": "{{ max_merged_segment }}"
        }
      },
      {%- endif %}
      {%- if hnsw_ef_search is defined and hnsw_ef_search %}
      "knn.algo_param.ef_search": {{ hnsw_ef_search }},
      {%- endif %}
      {%- if derived_source_enabled is defined and derived_source_enabled %}
      "knn.derived_source.enabled": true,
      {%- endif %}
      "knn": true
    }
  },
  "mappings": {
    "dynamic": "strict",
    "properties": {
      {% if id_field_name is defined and id_field_name != "_id" %}
        "{{id_field_name}}": {
          "type": "keyword"
        },
      {%- endif %}
      {% if target_index_partition_metadata is defined and target_index_partition_metadata %}
        "partition_id": {
          "type": "integer"
        },
      {%- endif %}
      "{{ target_field_name | default('target_field') }}": {
        "type": "knn_vector",
        "dimension": {{ target_index_dimension | default(768) }},
        {%- if mode is defined %}
        "mode": "{{ mode }}",
        {%- endif %}
        {%- if compression_level is defined %}
        "compression_level": "{{ compression_level }}",
        {%- endif %}
        "method": {
          "name": "disk_ann",
          "space_type": "{{ target_index_space_type | default('l2') }}",
          "engine": "jvector",
          "parameters": {
            {%- set comma = joiner(",") %}
            {%- if hnsw_ef_search is defined and hnsw_ef_search %}
              {{ comma() }} "ef_search": {{ hnsw_ef_search }}
            {%- endif %}
            {%- if hnsw_ef_construction is defined and hnsw_ef_construction %}
              {{ comma() }} "ef_construction": {{ hnsw_ef_construction }}
            {%- endif %}
            {%- if hnsw_m is defined and hnsw_m %}
              {{ comma() }} "m": {{ hnsw_m }}
            {%- endif %}
          }
        }
      }
    }
  }
}
EOF
}

inject_faiss_template() {
  local namespace="$1"
  
  kubectl exec -i opensearch-benchmark-client -n "$namespace" -c benchmark -- bash -c 'cat > /root/opensearch-benchmark-workloads/vectorsearch/indices/faiss-index.json' << 'EOF'
{
  "settings": {
    "index": {
      "knn": true
      {%- set settings_comma = joiner(",") %}
      {%- if memory_optimized_search_enabled is defined and memory_optimized_search_enabled %}
        {{ settings_comma() }} "knn.memory_optimized_search": true
      {%- endif %}
      {%- if target_index_primary_shards is defined and target_index_primary_shards %}
        {{ settings_comma() }} "number_of_shards": {{ target_index_primary_shards }}
      {%- endif %}
      {%- if target_index_replica_shards is defined %}
        {{ settings_comma() }} "number_of_replicas": {{ target_index_replica_shards }}
      {%- endif %}
      {%- if translog_flush_threshold_size is defined %}
        {{ settings_comma() }} "translog.flush_threshold_size": "{{ translog_flush_threshold_size }}"
      {%- endif %}
      {%- if refresh_interval is defined %}
        {{ settings_comma() }} "refresh_interval": "{{ refresh_interval }}"
      {%- endif %}
      {%- if max_merged_segment is defined %}
        {{ settings_comma() }} "merge": {
          "policy": {
            "max_merged_segment": "{{ max_merged_segment }}"
          }
        }
      {%- endif %}
      {%- if hnsw_ef_search is defined and hnsw_ef_search %}
        {{ settings_comma() }} "knn.algo_param.ef_search": {{ hnsw_ef_search }}
      {%- endif %}
      {%- if derived_source_enabled is defined and derived_source_enabled %}
        {{ settings_comma() }} "knn.derived_source.enabled": true
      {%- endif %}
      {%- if remote_index_build_enabled is defined and remote_index_build_enabled %}
        {{ settings_comma() }} "knn.remote_index_build.enabled": true
      {%- endif %}
      {%- if remote_index_build_enabled is defined and remote_index_build_enabled and remote_index_build_size_threshold is defined %}
        {{ settings_comma() }} "knn.remote_index_build.size_threshold": "{{ remote_index_build_size_threshold }}"
      {%- endif %}
      {%- if approximate_graph_build_threshold is defined%}
        {{ settings_comma() }} "knn.advanced.approximate_threshold": {{ approximate_graph_build_threshold }}
      {%- endif %}
    }
  },
  "mappings": {
    "dynamic": "strict",
    "properties": {
      {% if id_field_name is defined and id_field_name != "_id" %}
        "{{id_field_name}}": {
          "type": "keyword"
        },
      {%- endif %}
      {% if target_index_partition_metadata is defined and target_index_partition_metadata %}
      "partition_id": {
        "type": "integer"
      },
      {%- endif %}
      "{{ target_field_name | default('target_field') }}": {
        "type": "knn_vector",
        "dimension": {{ target_index_dimension | default(768) }},
        {%- if mode is defined %}
        "mode": "{{ mode }}",
        {%- endif %}
        {%- if compression_level is defined %}
        "compression_level": "{{ compression_level }}",
        {%- endif %}
        {%- if train_model_id is defined %}
        "model_id": "{{ train_model_id }}"
        {%- else %}
        "method": {
          "name": "hnsw",
          "space_type": "{{ target_index_space_type | default('l2') }}",
          "engine": "faiss",
          "parameters": {
            {%- set comma = joiner(",") %}
            {%- if hnsw_ef_search is defined and hnsw_ef_search %}
              {{ comma() }} "ef_search": {{ hnsw_ef_search }}
            {%- endif %}
            {%- if hnsw_ef_construction is defined and hnsw_ef_construction %}
              {{ comma() }} "ef_construction": {{ hnsw_ef_construction }}
            {%- endif %}
            {%- if hnsw_m is defined and hnsw_m %}
              {{ comma() }} "m": {{ hnsw_m }}
            {%- endif %}
            {%- if encoder is defined and encoder %}
              {{ comma() }} "encoder": { "name": "{{ encoder }}" }
            {%- endif %}
          }
        }
        {%- endif %}
      }
    }
  }
}
EOF
}

inject_lucene_template() {
  local namespace="$1"
  
  kubectl exec -i opensearch-benchmark-client -n "$namespace" -c benchmark -- bash -c 'cat > /root/opensearch-benchmark-workloads/vectorsearch/indices/lucene-index.json' << 'EOF'
{
  "settings": {
    "index": {
      "knn": true
      {%- set settings_comma = joiner(",") %}
      {%- if target_index_primary_shards is defined and target_index_primary_shards %}
        {{ settings_comma() }} "number_of_shards": {{ target_index_primary_shards }}
      {%- endif %}
      {%- if target_index_replica_shards is defined %}
        {{ settings_comma() }} "number_of_replicas": {{ target_index_replica_shards }}
      {%- endif %}
      {%- if translog_flush_threshold_size is defined %}
        {{ settings_comma() }} "translog.flush_threshold_size": "{{ translog_flush_threshold_size }}"
      {%- endif %}
      {%- if refresh_interval is defined %}
        {{ settings_comma() }} "refresh_interval": "{{ refresh_interval }}"
      {%- endif %}
      {%- if max_merged_segment is defined %}
        {{ settings_comma() }} "merge": {
          "policy": {
            "max_merged_segment": "{{ max_merged_segment }}"
          }
        }
      {%- endif %}
      {%- if hnsw_ef_search is defined and hnsw_ef_search %}
        {{ settings_comma() }} "knn.algo_param.ef_search": {{ hnsw_ef_search }}
      {%- endif %}
      {%- if derived_source_enabled is defined and derived_source_enabled %}
        {{ settings_comma() }} "knn.derived_source.enabled": true
      {%- endif %}
    }
  },
  "mappings": {
    "dynamic": "strict",
    "properties": {
      {% if id_field_name is defined and id_field_name != "_id" %}
        "{{id_field_name}}": {
          "type": "keyword"
        },
      {%- endif %}
      {% if target_index_partition_metadata is defined and target_index_partition_metadata %}
        "partition_id": {
          "type": "integer"
        },
      {%- endif %}
      "{{ target_field_name | default('target_field') }}": {
        "type": "knn_vector",
        "dimension": {{ target_index_dimension | default(768) }},
        {%- if mode is defined %}
        "mode": "{{ mode }}",
        {%- endif %}
        {%- if compression_level is defined %}
        "compression_level": "{{ compression_level }}",
        {%- endif %}
        "method": {
          "name": "hnsw",
          "space_type": "{{ target_index_space_type | default('l2') }}",
          "engine": "lucene",
          "parameters": {
            {%- set comma = joiner(",") %}
            {%- if hnsw_ef_construction is defined and hnsw_ef_construction %}
              {{ comma() }} "ef_construction": {{ hnsw_ef_construction }}
            {%- endif %}
            {%- if hnsw_m is defined and hnsw_m %}
              {{ comma() }} "m": {{ hnsw_m }}
            {%- endif %}
          }
        }
      }
    }
  }
}
EOF
}

inject_all_templates() {
  local namespace="$1"
  
  echo "⚙️  Injecting index templates for all engines..."
  inject_jvector_template "$namespace"
  inject_faiss_template "$namespace"
  inject_lucene_template "$namespace"
}

# Made with Bob
