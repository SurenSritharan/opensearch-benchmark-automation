#!/bin/bash
# Interactive CLI menu functions
# Note: dataset-configs.sh is sourced by run-benchmark.sh

show_menu() {
  echo "=========================================="
  echo "OpenSearch Benchmark Test Configuration"
  echo "=========================================="
  echo ""
  echo "Select target vector engine to test:"
  echo "  j) jvector (Namespace: os-jvector)"
  echo "  f) faiss   (Namespace: os-faiss)"
  echo "  l) lucene  (Namespace: os-lucene)"
  echo "  a) ALL Engines sequentially (Sweep Matrix)"
  echo ""
  read -p "Choose engine(s) [j/f/l/a]: " engine_choice
  
  case $engine_choice in
    j|jvector) TARGET_ENGINES=("jvector") ;;
    f|faiss)   TARGET_ENGINES=("faiss") ;;
    l|lucene)  TARGET_ENGINES=("lucene") ;;
    a|all)     TARGET_ENGINES=("jvector" "faiss" "lucene") ;;
    *) echo "❌ Invalid engine selection"; exit 1 ;;
  esac

  echo ""
  echo "Select dataset to use:"
  echo "  1) cohere-1m (768 dimensions) - default"
  echo "  2) msmarco (1024 dimensions)"
  echo ""
  read -p "Choose dataset [1/2] (press Enter for default): " dataset_choice
  
  case $dataset_choice in
    1|"") DATASET="cohere-1m" ;;
    2)    DATASET="msmarco" ;;
    *)    echo "❌ Invalid dataset selection"; exit 1 ;;
  esac
  
  export DATASET

  echo ""
  echo "Select scenarios to run (use numbers, comma-separated):"
  echo "  1) Index Creation & Data Ingestion"
  echo "  2) Force Merge"
  echo "  3) Search Tests (concurrency sweep)"
  echo "  4) All Scenarios (1+2+3)"
  echo ""
}

parse_cli_args() {
  RUN_INDEX_CREATION=false
  RUN_FORCE_MERGE=false
  RUN_SEARCH_TESTS=false
  INTERACTIVE_MODE=true
  TARGET_ENGINES=()
  DATASET=""
  
  # Check if any arguments provided (non-interactive mode)
  if [ $# -gt 0 ]; then
    INTERACTIVE_MODE=false
    
    while [[ $# -gt 0 ]]; do
      case $1 in
        --engine)
          if [ "$2" = "all" ]; then
            TARGET_ENGINES=("jvector" "faiss" "lucene")
          else
            IFS=',' read -ra ENG_ARR <<< "$2"
            TARGET_ENGINES=("${ENG_ARR[@]}")
          fi
          shift 2
          ;;
        --dataset)
          DATASET="$2"
          if ! validate_dataset "$DATASET"; then
            exit 1
          fi
          export DATASET
          shift 2
          ;;
        --list-datasets)
          list_datasets
          exit 0
          ;;
        --scenario)
          IFS=',' read -ra SCENARIOS <<< "$2"
          for scenario in "${SCENARIOS[@]}"; do
            case $scenario in
              index|indexing|create-index|1) RUN_INDEX_CREATION=true ;;
              merge|force-merge|2)          RUN_FORCE_MERGE=true ;;
              search|search-only|3)         RUN_SEARCH_TESTS=true ;;
              all|4)
                RUN_INDEX_CREATION=true
                RUN_FORCE_MERGE=true
                RUN_SEARCH_TESTS=true
                ;;
              *) echo "Unknown scenario: $scenario"; exit 1 ;;
            esac
          done
          shift 2
          ;;
        --help|-h)
          echo "Usage: $0 [OPTIONS]"
          echo ""
          echo "Options:"
          echo "  --engine <engine>       Specify engine(s): jvector, faiss, lucene, or all"
          echo "  --dataset <dataset>     Specify dataset: cohere-1m, msmarco (default: cohere-1m)"
          echo "  --list-datasets         List all available datasets and exit"
          echo "  --scenario <scenario>   Specify scenario(s): index, merge, search, or all"
          echo "  --help, -h              Show this help message"
          echo ""
          echo "Examples:"
          echo "  $0 --engine faiss --dataset msmarco --scenario all"
          echo "  $0 --list-datasets"
          echo "  $0 --engine all --dataset cohere-1m --scenario search"
          exit 0
          ;;
        *) echo "Unknown option: $1"; exit 1 ;;
      esac
    done
  fi
  
  if [ ${#TARGET_ENGINES[@]} -eq 0 ] && [ "$INTERACTIVE_MODE" = false ]; then
    echo "❌ Error: Non-interactive mode requires a targeted --engine parameter."
    exit 1
  fi
  
  # Interactive mode selection fallback
  if [ "$INTERACTIVE_MODE" = true ]; then
    show_menu
    read -p "Enter scenario choices (e.g., 1,3 or 4 for all): " choices
    IFS=',' read -ra SELECTED <<< "$choices"
    for choice in "${SELECTED[@]}"; do
      choice=$(echo "$choice" | xargs)
      case $choice in
        1) RUN_INDEX_CREATION=true ;;
        2) RUN_FORCE_MERGE=true ;;
        3) RUN_SEARCH_TESTS=true ;;
        4) RUN_INDEX_CREATION=true; RUN_FORCE_MERGE=true; RUN_SEARCH_TESTS=true ;;
        0) echo "Exiting..."; exit 0 ;;
        *) echo "Invalid choice: $choice"; exit 1 ;;
      esac
    done
  fi
  
  # Validate execution matrix variables
  if [ "$RUN_INDEX_CREATION" = false ] && [ "$RUN_FORCE_MERGE" = false ] && [ "$RUN_SEARCH_TESTS" = false ]; then
    echo "❌ Error: No execution scenarios selected."
    exit 1
  fi
}

display_configuration() {
  # Set default dataset if not specified
  if [ -z "$DATASET" ]; then
    DATASET=$(get_default_dataset)
    export DATASET
  fi
  
  local dimension=$(get_dataset_dimension "$DATASET")
  local format=$(get_dataset_format "$DATASET")
  local space_type=$(get_dataset_space_type "$DATASET")
  
  echo "=========================================="
  echo "📋 Selected Target Matrix Configuration:"
  echo "=========================================="
  echo "  Targeted Engines: ${TARGET_ENGINES[*]}"
  echo "  Dataset:          $DATASET (${dimension}D, format: $format, space: $space_type)"
  echo "  Index Creation:   $([ "$RUN_INDEX_CREATION" = true ] && echo "✅ YES" || echo "❌ NO")"
  echo "  Force Merge:      $([ "$RUN_FORCE_MERGE" = true ] && echo "✅ YES" || echo "❌ NO")"
  echo "  Search Tests:     $([ "$RUN_SEARCH_TESTS" = true ] && echo "✅ YES" || echo "❌ NO")"
  echo "=========================================="
  echo ""
  
  read -p "Launch baseline suite sweep? (y/n): " confirm
  if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 0
  fi
}

# Made with Bob
