#!/bin/bash
# Dataset download utility for custom workloads

download_dataset_files() {
  local dataset="$1"
  local namespace="$2"
  
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "📦 Checking Dataset Files for [$dataset]"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  
  # Get data directory and files from config
  local data_dir=$(get_data_dir "$dataset")
  local data_files=$(get_data_files "$dataset")
  
  # Check if there are any files to download
  local file_count=$(echo "$data_files" | jq 'length')
  if [ "$file_count" -eq 0 ]; then
    echo "ℹ️  No custom data files configured for $dataset"
    return 0
  fi
  
  echo "📂 Data directory: $data_dir"
  echo "📊 Files to check: $file_count"
  echo ""
  
  # Create data directory in the pod if it doesn't exist
  kubectl exec -n "$namespace" -c benchmark opensearch-benchmark-client -- \
    mkdir -p "$data_dir" 2>/dev/null || true
  
  # Download each file
  local i=0
  while [ $i -lt $file_count ]; do
    local file_name=$(echo "$data_files" | jq -r ".[$i].name")
    local file_url=$(echo "$data_files" | jq -r ".[$i].url")
    local file_range=$(echo "$data_files" | jq -r ".[$i].range // \"\"")
    local file_size=$(echo "$data_files" | jq -r ".[$i].size // \"\"")
    
    local file_path="$data_dir/$file_name"
    
    echo "  ▶ Checking: $file_name"
    if [ -n "$file_size" ]; then
      echo "    Size: $file_size"
    fi
    
    # Check if file already exists in the pod
    local file_exists=$(kubectl exec -n "$namespace" -c benchmark opensearch-benchmark-client -- \
      test -f "$file_path" 2>/dev/null && echo "yes" || echo "no")
    
    if [ "$file_exists" = "yes" ]; then
      echo "    ✅ Already present. Skipping download."
    else
      echo "    📥 Downloading..."
      
      # Build curl command
      local curl_cmd="curl -L -o \"$file_path\" \"$file_url\""
      if [ -n "$file_range" ] && [ "$file_range" != "null" ]; then
        curl_cmd="$curl_cmd -r $file_range"
      fi
      
      # Execute download in the pod
      kubectl exec -n "$namespace" -c benchmark opensearch-benchmark-client -- \
        bash -c "$curl_cmd"
      
      if [ $? -eq 0 ]; then
        echo "    ✅ Download complete"
      else
        echo "    ❌ Download failed"
        return 1
      fi
    fi
    
    echo ""
    i=$((i + 1))
  done
  
  echo "✅ All dataset files ready"
  return 0
}

# Made with Bob