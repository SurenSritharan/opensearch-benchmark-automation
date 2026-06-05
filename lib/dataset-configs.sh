#!/bin/bash
# Dataset configuration loader
# Uses YAML config with jq for parsing (converts YAML to JSON via Python)

# Get absolute path to config file - compute it fresh each time to avoid subshell issues
get_config_file_path() {
  local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  echo "${script_dir}/../config/datasets.yaml"
}

# Convert YAML to JSON using Python (available as OpenSearch Benchmark dependency)
yaml_to_json() {
  local config_file=$(get_config_file_path)
  
  # Try to use Python with yaml module
  local result=$(python -c "
import sys, json
try:
    import yaml
    with open(sys.argv[1], 'r') as f:
        data = yaml.safe_load(f)
        print(json.dumps(data))
except ImportError:
    print('YAML_MODULE_MISSING', file=sys.stderr)
    sys.exit(2)
except Exception as e:
    print('{}', file=sys.stderr)
    sys.exit(1)
" "$config_file" 2>&1)
  
  local exit_code=$?
  
  # Check if PyYAML is missing and auto-install
  if [ $exit_code -eq 2 ] || echo "$result" | grep -q "YAML_MODULE_MISSING"; then
    echo "⚠️  PyYAML module not found for python3" >&2
    echo "   Current python3: $(which python3)" >&2
    echo "   Python version: $(python --version 2>&1)" >&2
    echo "" >&2
    echo "   Attempting to install PyYAML..." >&2
    
    # Try multiple installation methods
    local install_success=false
    
    # Method 1: pip3 install
    if pip3 install --user pyyaml >/dev/null 2>&1; then
      install_success=true
    # Method 2: python3 -m pip install
    elif python -m pip install --user pyyaml >/dev/null 2>&1; then
      install_success=true
    # Method 3: Try without --user flag
    elif python -m pip install pyyaml >/dev/null 2>&1; then
      install_success=true
    fi
    
    if [ "$install_success" = true ]; then
      echo "   ✅ PyYAML installed successfully" >&2
      echo "" >&2
      
      # Retry the conversion
      result=$(python -c "
import sys, json
try:
    import yaml
    with open(sys.argv[1], 'r') as f:
        data = yaml.safe_load(f)
        print(json.dumps(data))
except Exception as e:
    print('{}', file=sys.stderr)
    sys.exit(1)
" "$config_file" 2>&1)
      
      if [ $? -eq 0 ]; then
        echo "$result"
        return 0
      fi
    else
      echo "   ❌ Failed to install PyYAML automatically" >&2
      echo "" >&2
      echo "   Please install manually with one of these commands:" >&2
      echo "   pip3 install pyyaml" >&2
      echo "   python3 -m pip install pyyaml" >&2
      echo "   python3 -m pip install --user pyyaml" >&2
      echo "" >&2
      return 1
    fi
  fi
  
  echo "$result"
}

# Get default dataset from config
get_default_dataset() {
  local json=$(yaml_to_json)
  echo "$json" | jq -r '.default // "cohere-1m"'
}

# Get dataset dimension
get_dataset_dimension() {
  local dataset="${1:-$(get_default_dataset)}"
  local json=$(yaml_to_json)
  
  local dimension=$(echo "$json" | jq -r ".datasets.\"$dataset\".dimension // null")
  
  if [ "$dimension" = "null" ]; then
    echo "❌ Error: Unknown dataset '$dataset'. Run with --list-datasets to see available options." >&2
    echo "1024"  # Return default to prevent complete failure
    return 1
  fi
  
  echo "$dimension"
}

# Get dataset format
get_dataset_format() {
  local dataset="${1:-$(get_default_dataset)}"
  local json=$(yaml_to_json)
  
  local format=$(echo "$json" | jq -r ".datasets.\"$dataset\".format // null")
  
  if [ "$format" = "null" ]; then
    echo "❌ Error: Unknown dataset '$dataset'. Run with --list-datasets to see available options." >&2
    echo "hdf5"  # Return default to prevent complete failure
    return 1
  fi
  
  echo "$format"
}

# Get dataset space type
get_dataset_space_type() {
  local dataset="${1:-$(get_default_dataset)}"
  local json=$(yaml_to_json)
  
  local space_type=$(echo "$json" | jq -r ".datasets.\"$dataset\".space_type // null")
  
  if [ "$space_type" = "null" ]; then
    echo "❌ Error: Unknown dataset '$dataset'. Run with --list-datasets to see available options." >&2
    echo "cosinesimil"  # Return default to prevent complete failure
    return 1
  fi
  
  echo "$space_type"
}

# Get dataset description
get_dataset_description() {
  local dataset="${1:-$(get_default_dataset)}"
  local json=$(yaml_to_json)
  
  echo "$json" | jq -r ".datasets.\"$dataset\".description // \"\""
}

# Validate dataset exists
validate_dataset() {
  local dataset="$1"
  local json=$(yaml_to_json)
  
  local exists=$(echo "$json" | jq -r ".datasets | has(\"$dataset\")")
  
  if [ "$exists" != "true" ]; then
    echo "❌ Error: Unknown dataset '$dataset'" >&2
    list_datasets >&2
    return 1
  fi
  
  return 0
}

# List all available datasets
list_datasets() {
  local json=$(yaml_to_json)
  local default=$(get_default_dataset)
  
  echo "Available datasets:"
  echo ""
  
  local datasets=$(echo "$json" | jq -r '.datasets | keys[]')
  
  while IFS= read -r dataset; do
    local dimension=$(echo "$json" | jq -r ".datasets.\"$dataset\".dimension")
    local format=$(echo "$json" | jq -r ".datasets.\"$dataset\".format")
    local space_type=$(echo "$json" | jq -r ".datasets.\"$dataset\".space_type // \"cosinesimil\"")
    local description=$(echo "$json" | jq -r ".datasets.\"$dataset\".description // \"\"")
    
    local default_marker=""
    if [ "$dataset" = "$default" ]; then
      default_marker=" (default)"
    fi
    
    echo "  📊 $dataset$default_marker"
    echo "     Dimension: $dimension"
    echo "     Format: $format"
    echo "     Space Type: $space_type"
    if [ -n "$description" ]; then
      echo "     Description: $description"
    fi
    echo ""
  done <<< "$datasets"
}

# Check if dataset has a custom workload
has_custom_workload() {
  local dataset="${1:-$(get_default_dataset)}"
  local json=$(yaml_to_json)
  
  local workload_path=$(echo "$json" | jq -r ".datasets.\"$dataset\".custom_workload // \"\"")
  
  if [ -n "$workload_path" ] && [ "$workload_path" != "null" ]; then
    return 0
  fi
  return 1
}

# Get custom workload path
get_custom_workload_path() {
  local dataset="${1:-$(get_default_dataset)}"
  local json=$(yaml_to_json)
  
  echo "$json" | jq -r ".datasets.\"$dataset\".custom_workload // \"\""
}

# Get data directory for dataset
get_data_dir() {
  local dataset="${1:-$(get_default_dataset)}"
  local json=$(yaml_to_json)
  
  echo "$json" | jq -r ".datasets.\"$dataset\".data_dir // \"/datasets/$dataset\""
}

# Get data files for dataset
get_data_files() {
  local dataset="${1:-$(get_default_dataset)}"
  local json=$(yaml_to_json)
  
  echo "$json" | jq -c ".datasets.\"$dataset\".data_files // []"
}

# Made with Bob