pipeline {
    agent {
        kubernetes {
            yaml """
apiVersion: v1
kind: Pod
metadata:
  labels:
    jenkins: agent
spec:
  serviceAccountName: jenkins
  containers:
  - name: python
    image: python:3.11-slim
    command:
    - cat
    tty: true
    resources:
      requests:
        memory: "2Gi"
        cpu: "1000m"
      limits:
        memory: "4Gi"
        cpu: "2000m"
  - name: kubectl
    image: bitnami/kubectl:latest
    command:
    - cat
    tty: true
    resources:
      requests:
        memory: "512Mi"
        cpu: "250m"
      limits:
        memory: "1Gi"
        cpu: "500m"
"""
        }
    }
    
    parameters {
        choice(
            name: 'DATASET',
            choices: ['cohere-1m-768-dp', 'msmarco'],
            description: 'Dataset to benchmark'
        )
        choice(
            name: 'ENGINES',
            choices: ['all', 'jvector', 'faiss', 'lucene', 'jvector,faiss', 'jvector,lucene', 'faiss,lucene'],
            description: 'Vector search engines to test'
        )
        choice(
            name: 'SCENARIOS',
            choices: ['all', 'index', 'merge', 'search', 'index,search', 'index,merge,search'],
            description: 'Benchmark scenarios to run'
        )
        string(
            name: 'SEARCH_CLIENTS',
            defaultValue: '1,2,4,8',
            description: 'Comma-separated list of concurrent search clients'
        )
        string(
            name: 'QUERY_COUNT',
            defaultValue: '10000',
            description: 'Number of queries to execute per search scenario'
        )
        booleanParam(
            name: 'ENABLE_PROFILING',
            defaultValue: true,
            description: 'Enable CPU profiling with async-profiler'
        )
        booleanParam(
            name: 'ENABLE_METRICS',
            defaultValue: true,
            description: 'Enable GKE metrics collection'
        )
        string(
            name: 'METRICS_INTERVAL',
            defaultValue: '10',
            description: 'Metrics collection interval in seconds'
        )
        string(
            name: 'METRICS_DURATION',
            defaultValue: '3600',
            description: 'Maximum metrics collection duration in seconds'
        )
    }
    
    environment {
        RESULTS_DIR = "results/${BUILD_ID}"
        PYTHON_VENV = "${WORKSPACE}/venv"
        KUBECONFIG = credentials('gke-kubeconfig')
    }
    
    options {
        buildDiscarder(logRotator(numToKeepStr: '30', artifactNumToKeepStr: '10'))
        timestamps()
        timeout(time: 6, unit: 'HOURS')
        disableConcurrentBuilds()
    }
    
    stages {
        stage('Setup Environment') {
            steps {
                container('python') {
                    script {
                        echo "🔧 Setting up Python environment..."
                        sh '''
                            python3 -m venv ${PYTHON_VENV}
                            . ${PYTHON_VENV}/bin/activate
                            pip install --upgrade pip
                            pip install -r requirements.txt
                        '''
                    }
                }
                container('kubectl') {
                    script {
                        echo "🔍 Verifying GKE cluster connectivity..."
                        sh '''
                            kubectl cluster-info
                            kubectl get nodes
                            kubectl get namespaces | grep "^os-"
                        '''
                    }
                }
            }
        }
        
        stage('Pre-Benchmark Validation') {
            parallel {
                stage('Validate Cluster State') {
                    steps {
                        container('kubectl') {
                            script {
                                echo "✅ Validating cluster state..."
                                sh '''
                                    # Check if metrics-server is available
                                    kubectl get deployment metrics-server -n kube-system || echo "⚠️ metrics-server not found"
                                    
                                    # Verify node pools
                                    kubectl get nodes -o json | jq -r '.items[] | .metadata.labels["cloud.google.com/gke-nodepool"]' | sort | uniq
                                    
                                    # Check OpenSearch pods status
                                    for ns in os-jvector os-faiss os-lucene; do
                                        if kubectl get namespace $ns &>/dev/null; then
                                            echo "Checking namespace: $ns"
                                            kubectl get pods -n $ns
                                        fi
                                    done
                                '''
                            }
                        }
                    }
                }
                
                stage('Collect Baseline Metrics') {
                    when {
                        expression { params.ENABLE_METRICS }
                    }
                    steps {
                        container('python') {
                            script {
                                echo "📸 Collecting baseline metrics snapshots..."
                                def engines = params.ENGINES == 'all' ? ['jvector', 'faiss', 'lucene'] : params.ENGINES.split(',')
                                
                                engines.each { engine ->
                                    sh """
                                        . ${PYTHON_VENV}/bin/activate
                                        python collect_metrics.py \
                                            --namespace os-${engine} \
                                            --snapshot \
                                            --label "baseline-pre-benchmark" \
                                            --output ${RESULTS_DIR}/${engine}-metrics/baseline
                                    """
                                }
                            }
                        }
                    }
                }
            }
        }
        
        stage('Run Benchmarks with Metrics') {
            steps {
                container('python') {
                    script {
                        echo "🚀 Starting benchmark execution with metrics collection..."
                        
                        // Prepare benchmark arguments
                        def benchmarkArgs = [
                            "--dataset ${params.DATASET}",
                            "--engines ${params.ENGINES}",
                            "--scenarios ${params.SCENARIOS}",
                            "--search-clients ${params.SEARCH_CLIENTS}",
                            "--query-count ${params.QUERY_COUNT}",
                            "--results-dir ${RESULTS_DIR}"
                        ]
                        
                        if (!params.ENABLE_PROFILING) {
                            benchmarkArgs.add("--no-profiling")
                        }
                        
                        // Start metrics collection in background if enabled
                        if (params.ENABLE_METRICS) {
                            def engines = params.ENGINES == 'all' ? ['jvector', 'faiss', 'lucene'] : params.ENGINES.split(',')
                            
                            engines.each { engine ->
                                echo "📊 Starting metrics collection for ${engine}..."
                                def metricsCmd = """
                                    . ${PYTHON_VENV}/bin/activate
                                    python collect_metrics.py \
                                        --namespace os-${engine} \
                                        --duration ${params.METRICS_DURATION} \
                                        --interval ${params.METRICS_INTERVAL} \
                                        --scenario "jenkins-build-${BUILD_ID}" \
                                        --output ${RESULTS_DIR}/${engine}-metrics \
                                        > ${RESULTS_DIR}/${engine}-metrics-collection.log 2>&1 &
                                    echo \$! > ${RESULTS_DIR}/${engine}-metrics.pid
                                """
                                sh metricsCmd
                            }
                        }
                        
                        // Run the main benchmark
                        try {
                            sh """
                                . ${PYTHON_VENV}/bin/activate
                                python run_benchmark.py ${benchmarkArgs.join(' ')}
                            """
                        } finally {
                            // Stop metrics collection processes
                            if (params.ENABLE_METRICS) {
                                echo "🛑 Stopping metrics collection..."
                                sh """
                                    for pidfile in ${RESULTS_DIR}/*-metrics.pid; do
                                        if [ -f "\$pidfile" ]; then
                                            pid=\$(cat "\$pidfile")
                                            kill \$pid 2>/dev/null || true
                                            rm "\$pidfile"
                                        fi
                                    done
                                """
                            }
                        }
                    }
                }
            }
        }
        
        stage('Post-Benchmark Analysis') {
            parallel {
                stage('Collect Final Metrics') {
                    when {
                        expression { params.ENABLE_METRICS }
                    }
                    steps {
                        container('python') {
                            script {
                                echo "📸 Collecting post-benchmark metrics snapshots..."
                                def engines = params.ENGINES == 'all' ? ['jvector', 'faiss', 'lucene'] : params.ENGINES.split(',')
                                
                                engines.each { engine ->
                                    sh """
                                        . ${PYTHON_VENV}/bin/activate
                                        python collect_metrics.py \
                                            --namespace os-${engine} \
                                            --snapshot \
                                            --label "post-benchmark" \
                                            --output ${RESULTS_DIR}/${engine}-metrics/final
                                    """
                                }
                            }
                        }
                    }
                }
                
                stage('Generate Metrics Summary') {
                    when {
                        expression { params.ENABLE_METRICS }
                    }
                    steps {
                        container('python') {
                            script {
                                echo "📈 Generating metrics summary reports..."
                                sh """
                                    . ${PYTHON_VENV}/bin/activate
                                    
                                    # Generate summary for each engine
                                    for metrics_dir in ${RESULTS_DIR}/*-metrics; do
                                        if [ -d "\$metrics_dir" ]; then
                                            engine=\$(basename "\$metrics_dir" | sed 's/-metrics//')
                                            echo "Generating summary for \$engine..."
                                            
                                            # Create summary using jq if metrics files exist
                                            if [ -f "\$metrics_dir/gke_metrics.json" ]; then
                                                jq -r '
                                                    "=== Metrics Summary for " + .namespace + " ===",
                                                    "Duration: " + (.duration_seconds | tostring) + "s",
                                                    "Samples: " + (.summary.total_samples | tostring),
                                                    "",
                                                    "Node Metrics:",
                                                    (.summary.nodes | to_entries[] | 
                                                        "  " + .key + ":",
                                                        "    CPU Avg: " + (.value.cpu_avg | tostring) + "%",
                                                        "    CPU Max: " + (.value.cpu_max | tostring) + "%",
                                                        "    Memory Avg: " + (.value.memory_avg | tostring) + "%",
                                                        "    Memory Max: " + (.value.memory_max | tostring) + "%"
                                                    )
                                                ' "\$metrics_dir/gke_metrics.json" > "\$metrics_dir/summary.txt"
                                            fi
                                        fi
                                    done
                                """
                            }
                        }
                    }
                }
                
                stage('Collect Cluster Telemetry') {
                    steps {
                        container('kubectl') {
                            script {
                                echo "📊 Collecting cluster telemetry..."
                                def engines = params.ENGINES == 'all' ? ['jvector', 'faiss', 'lucene'] : params.ENGINES.split(',')
                                
                                engines.each { engine ->
                                    sh """
                                        mkdir -p ${RESULTS_DIR}/${engine}-metrics/cluster-telemetry
                                        
                                        # Collect pod status
                                        kubectl get pods -n os-${engine} -o json > ${RESULTS_DIR}/${engine}-metrics/cluster-telemetry/pods.json
                                        
                                        # Collect node information
                                        kubectl get nodes -o json > ${RESULTS_DIR}/${engine}-metrics/cluster-telemetry/nodes.json
                                        
                                        # Collect events
                                        kubectl get events -n os-${engine} --sort-by='.lastTimestamp' > ${RESULTS_DIR}/${engine}-metrics/cluster-telemetry/events.txt
                                    """
                                }
                            }
                        }
                    }
                }
            }
        }
        
        stage('Archive Results') {
            steps {
                script {
                    echo "💾 Archiving benchmark results and metrics..."
                    
                    // Archive all results
                    archiveArtifacts artifacts: "${RESULTS_DIR}/**/*", 
                                     allowEmptyArchive: false,
                                     fingerprint: true
                    
                    // Generate and archive summary report
                    container('python') {
                        sh """
                            . ${PYTHON_VENV}/bin/activate
                            
                            # Create a consolidated summary
                            cat > ${RESULTS_DIR}/BUILD_SUMMARY.txt << EOF
========================================
OpenSearch Benchmark Build Summary
========================================
Build ID: ${BUILD_ID}
Build URL: ${BUILD_URL}
Date: \$(date -u +"%Y-%m-%d %H:%M:%S UTC")

Parameters:
- Dataset: ${params.DATASET}
- Engines: ${params.ENGINES}
- Scenarios: ${params.SCENARIOS}
- Search Clients: ${params.SEARCH_CLIENTS}
- Query Count: ${params.QUERY_COUNT}
- Profiling: ${params.ENABLE_PROFILING}
- Metrics Collection: ${params.ENABLE_METRICS}

Results Directory: ${RESULTS_DIR}
========================================
EOF
                            
                            # Append metrics summaries if they exist
                            for summary in ${RESULTS_DIR}/*-metrics/summary.txt; do
                                if [ -f "\$summary" ]; then
                                    echo "" >> ${RESULTS_DIR}/BUILD_SUMMARY.txt
                                    cat "\$summary" >> ${RESULTS_DIR}/BUILD_SUMMARY.txt
                                fi
                            done
                            
                            cat ${RESULTS_DIR}/BUILD_SUMMARY.txt
                        """
                    }
                }
            }
        }
    }
    
    post {
        always {
            script {
                echo "🧹 Cleaning up..."
                
                // Display final summary
                container('python') {
                    sh """
                        if [ -f ${RESULTS_DIR}/BUILD_SUMMARY.txt ]; then
                            cat ${RESULTS_DIR}/BUILD_SUMMARY.txt
                        fi
                    """
                }
            }
        }
        
        success {
            echo "✅ Benchmark pipeline completed successfully!"
        }
        
        failure {
            echo "❌ Benchmark pipeline failed. Check logs for details."
        }
        
        cleanup {
            // Clean up workspace but preserve results
            cleanWs(
                deleteDirs: true,
                patterns: [
                    [pattern: 'venv/**', type: 'INCLUDE'],
                    [pattern: '.pytest_cache/**', type: 'INCLUDE'],
                    [pattern: '__pycache__/**', type: 'INCLUDE']
                ]
            )
        }
    }
}

// Made with Bob
