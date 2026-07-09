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
  - name: curl
    image: alpine/curl:latest
    command:
    - cat
    tty: true
    resources:
      requests:
        memory: "128Mi"
        cpu: "100m"
      limits:
        memory: "256Mi"
        cpu: "250m"
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
            name: 'PIPELINE',
            choices: [
                'complete',
                'search-only'
            ],
            description: '"complete" runs create-index → bulk-ingest → refresh → force-merge → search for every selected engine. "search-only" runs search sweeps only (indexes must already exist).'
        )
        choice(
            name: 'CORPUS_SIZE',
            choices: ['all', '1m', '5m'],
            description: 'Vector corpus size to benchmark. "all" runs 1m then 5m in a single job per engine.'
        )
        choice(
            name: 'DATASET',
            choices: ['all', 'cohere-wiki-en-768', 'cohere-msmarco-1024'],
            description: 'Dataset to benchmark. "all" runs both datasets sequentially within each engine job.'
        )
        string(
            name: 'API_URL',
            defaultValue: 'http://34.132.114.18',
            description: 'Base URL of the benchmark cloud service API (single endpoint — the shared API server in benchmark-api namespace)'
        )
        choice(
            name: 'ENGINE_TARGET',
            choices: ['all', 'os-jvector', 'os-faiss', 'os-lucene'],
            description: 'Which engine cluster(s) to run against. "all" scales up all three OS clusters and all three benchmark workers.'
        )
        booleanParam(
            name: 'SCALE_CLUSTERS',
            defaultValue: true,
            description: 'Scale up engine clusters + benchmark workers before benchmarking and scale them down afterwards'
        )
        booleanParam(
            name: 'REDEPLOY_CLUSTERS',
            defaultValue: false,
            description: 'Re-deploy OpenSearch clusters before benchmarking (applies OPENSEARCH_VERSION). Implies scale-up.'
        )
        string(
            name: 'OPENSEARCH_VERSION',
            defaultValue: '3.6.0',
            description: 'OpenSearch version to deploy (only used when REDEPLOY_CLUSTERS is true)'
        )
        booleanParam(
            name: 'DELETE_PVCS',
            defaultValue: false,
            description: 'Delete PVCs during re-deploy — destroys all indexed data (only used when REDEPLOY_CLUSTERS is true)'
        )
    }

    environment {
        RESULTS_DIR = "results/${BUILD_ID}"
        KUBECONFIG  = credentials('gke-kubeconfig')
    }

    options {
        buildDiscarder(logRotator(numToKeepStr: '30', artifactNumToKeepStr: '10'))
        timestamps()
        timeout(time: 12, unit: 'HOURS')
        disableConcurrentBuilds()
    }

    stages {

        // ── 1. Re-deploy OpenSearch clusters (optional) ────────────────────────
        stage('Re-deploy Clusters') {
            when {
                expression { params.REDEPLOY_CLUSTERS }
            }
            steps {
                container('kubectl') {
                    script {
                        def extraArgs = "--version ${params.OPENSEARCH_VERSION} --force"
                        if (params.DELETE_PVCS) { extraArgs += " --delete-pvcs" }

                        if (params.ENGINE_TARGET == 'all') {
                            echo "Re-deploying all OpenSearch clusters (version ${params.OPENSEARCH_VERSION})..."
                            sh "gke-manifest/deploy-all-clusters.sh ${extraArgs}"
                        } else {
                            echo "Re-deploying ${params.ENGINE_TARGET} (version ${params.OPENSEARCH_VERSION})..."
                            sh "gke-manifest/deploy-namespace-cluster.sh ${params.ENGINE_TARGET} ${extraArgs}"
                        }
                    }
                }
            }
        }

        // ── 2. Scale up OpenSearch clusters + benchmark workers ────────────────
        //    Each engine (jvector/faiss/lucene) has:
        //      - An OpenSearch cluster in its own namespace (os-<engine>)
        //      - A dedicated benchmark worker pod in the benchmark-api namespace
        //    The shared API server (benchmark-api/opensearch-benchmark-api-server)
        //    is always scaled up alongside whichever workers are needed.
        stage('Scale Up') {
            when {
                // Re-deploy already starts the pods, so skip pure scale-up in that case
                expression { params.SCALE_CLUSTERS && !params.REDEPLOY_CLUSTERS }
            }
            steps {
                container('kubectl') {
                    script {
                        def engines = params.ENGINE_TARGET == 'all'
                            ? ['jvector', 'faiss', 'lucene']
                            : [params.ENGINE_TARGET.replace('os-', '')]

                        echo "Scaling up OpenSearch clusters: ${engines}"
                        if (params.ENGINE_TARGET == 'all') {
                            sh "gke-manifest/scale-up-clusters.sh all"
                        } else {
                            sh "gke-manifest/scale-up-clusters.sh ${params.ENGINE_TARGET}"
                        }

                        echo "Scaling up API server in benchmark-api namespace..."
                        sh '''
                            kubectl scale deployment opensearch-benchmark-api-server \
                                --replicas=1 -n benchmark-api
                        '''

                        echo "Scaling up benchmark worker(s) in benchmark-api namespace..."
                        engines.each { engine ->
                            sh """
                                kubectl scale statefulset opensearch-benchmark-worker-${engine} \
                                    --replicas=1 -n benchmark-api
                            """
                        }

                        echo "Waiting for API server to be ready..."
                        sh '''
                            kubectl wait --for=condition=available deployment/opensearch-benchmark-api-server \
                                -n benchmark-api --timeout=300s
                        '''

                        echo "Waiting for benchmark worker(s) to be ready..."
                        engines.each { engine ->
                            sh """
                                kubectl wait --for=condition=ready pod \
                                    -l app=opensearch-benchmark,component=worker,engine=${engine} \
                                    -n benchmark-api --timeout=600s
                            """
                        }
                    }
                }
            }
        }

        // ── 3. Verify cluster and worker health ────────────────────────────────
        stage('Verify Health') {
            steps {
                container('kubectl') {
                    script {
                        echo "Verifying OpenSearch cluster and benchmark worker health..."
                        sh '''
                            kubectl cluster-info
                            echo ""
                            echo "=== OpenSearch Clusters ==="
                            for ns in os-jvector os-faiss os-lucene; do
                                if kubectl get namespace $ns &>/dev/null; then
                                    echo "--- $ns ---"
                                    kubectl get pods -n $ns
                                fi
                            done
                            echo ""
                            echo "=== Benchmark API (benchmark-api namespace) ==="
                            kubectl get pods -n benchmark-api
                        '''

                        echo "Checking API server health endpoint..."
                        sh """
                            curl -sf ${params.API_URL}/health || \
                                (echo "WARNING: API health check failed — service may still be starting" && true)
                        """
                    }
                }
            }
        }

        // ── 4. Run benchmark via cloud service API ─────────────────────────────
        //    run-pipeline.sh is a single generic script that builds the correct
        //    JSON payload for any engine/dataset/corpus combination and routes
        //    it to the right worker via the "engine" field in the API request.
        //    When ENGINE_TARGET=all, all three engines run in parallel — each
        //    gets its own job ID and results file.
        stage('Run Benchmark') {
            steps {
                container('curl') {
                    script {
                        def engines = params.ENGINE_TARGET == 'all'
                            ? ['jvector', 'faiss', 'lucene']
                            : [params.ENGINE_TARGET.replace('os-', '')]

                        def datasetEnv = params.DATASET == 'all' ? '' : "DATASET=${params.DATASET}"
                        def corpusSize = params.CORPUS_SIZE

                        sh "chmod +x cloud-service/scripts/run-pipeline.sh"

                        // Run all engines in parallel; each writes its own log and results file
                        def parallelBranches = engines.collectEntries { engine ->
                            ["${engine}" : {
                                container('curl') {
                                    sh """
                                        ${datasetEnv} \
                                        API_URL=${params.API_URL} \
                                        TIME_PERIOD=300 \
                                        cloud-service/scripts/run-pipeline.sh \
                                            ${params.PIPELINE} \
                                            ${engine} \
                                            ${corpusSize} \
                                            2>&1 | tee benchmark-run-${engine}.log

                                        # Capture job_id for this engine
                                        JOB_ID=\$(grep -oP '(?<=Job ID: )\\S+' benchmark-run-${engine}.log | tail -1 || true)
                                        if [ -n "\$JOB_ID" ]; then
                                            echo "\$JOB_ID" > job_id_${engine}.txt
                                            echo "[${engine}] Job ID captured: \$JOB_ID"
                                        fi
                                    """
                                }
                            }]
                        }

                        parallel parallelBranches
                    }
                }
            }
        }

        // ── 5. Fetch & save results ────────────────────────────────────────────
        //    One job ID per engine — fetch each independently and write per-engine
        //    files plus a combined summary. The shared results.html on the API server
        //    can filter by job_id to view any individual engine's results.
        stage('Fetch Results') {
            steps {
                container('curl') {
                    script {
                        def engines = params.ENGINE_TARGET == 'all'
                            ? ['jvector', 'faiss', 'lucene']
                            : [params.ENGINE_TARGET.replace('os-', '')]

                        sh "mkdir -p ${RESULTS_DIR}"

                        def jobLinks = []

                        engines.each { engine ->
                            sh """
                                # Copy run log
                                cp benchmark-run-${engine}.log ${RESULTS_DIR}/ 2>/dev/null || true

                                if [ -f job_id_${engine}.txt ]; then
                                    JOB_ID=\$(cat job_id_${engine}.txt)
                                    echo "=== ${engine} (job: \$JOB_ID) ==="

                                    curl -s "${params.API_URL}/api/v1/benchmark/\$JOB_ID" \
                                        | jq '.' > ${RESULTS_DIR}/job-status-${engine}.json

                                    curl -s "${params.API_URL}/api/v1/benchmark/\$JOB_ID/results" \
                                        | jq '.' > ${RESULTS_DIR}/results-${engine}.json

                                    jq '{job_id, status, scenarios_completed, scenarios_total}' \
                                        ${RESULTS_DIR}/job-status-${engine}.json

                                    echo "  View: ${params.API_URL}/results.html?job_id=\$JOB_ID"
                                else
                                    echo "WARNING: no job_id_${engine}.txt — ${engine} may not have submitted successfully"
                                fi
                            """
                        }

                        // Write build summary
                        sh """
                            cat > ${RESULTS_DIR}/BUILD_SUMMARY.txt << EOF
========================================
OpenSearch Benchmark Build Summary
========================================
Build ID:  ${BUILD_ID}
Build URL: ${BUILD_URL}
Date:      \$(date -u +"%Y-%m-%d %H:%M:%S UTC")

Parameters:
  Pipeline:           ${params.PIPELINE}
  Engine Target:      ${params.ENGINE_TARGET}
  Dataset:            ${params.DATASET}
  Corpus Size:        ${params.CORPUS_SIZE}
  API URL:            ${params.API_URL}
  Scale Clusters:     ${params.SCALE_CLUSTERS}
  Redeploy Clusters:  ${params.REDEPLOY_CLUSTERS}
  OpenSearch Version: ${params.OPENSEARCH_VERSION}

Results per engine:
EOF
                            for engine in ${engines.join(' ')}; do
                                if [ -f job_id_\${engine}.txt ]; then
                                    JOB_ID=\$(cat job_id_\${engine}.txt)
                                    echo "  \${engine}: ${params.API_URL}/results.html?job_id=\${JOB_ID}" \
                                        >> ${RESULTS_DIR}/BUILD_SUMMARY.txt
                                else
                                    echo "  \${engine}: no job submitted" \
                                        >> ${RESULTS_DIR}/BUILD_SUMMARY.txt
                                fi
                            done
                            echo "========================================" >> ${RESULTS_DIR}/BUILD_SUMMARY.txt
                            cat ${RESULTS_DIR}/BUILD_SUMMARY.txt
                        """
                    }
                }
            }
        }

        // ── 6. Archive results ─────────────────────────────────────────────────
        stage('Archive Results') {
            steps {
                archiveArtifacts artifacts: "${RESULTS_DIR}/**/*",
                                 allowEmptyArchive: true,
                                 fingerprint: true
            }
        }
    }

    post {
        always {
            script {
                if (params.SCALE_CLUSTERS) {
                    container('kubectl') {
                        script {
                            def engines = params.ENGINE_TARGET == 'all'
                                ? ['jvector', 'faiss', 'lucene']
                                : [params.ENGINE_TARGET.replace('os-', '')]

                            // Scale down benchmark workers first
                            echo "Scaling down benchmark worker(s)..."
                            engines.each { engine ->
                                sh """
                                    kubectl scale statefulset opensearch-benchmark-worker-${engine} \
                                        --replicas=0 -n benchmark-api || true
                                """
                            }

                            // Scale down API server only when all workers are going down
                            if (params.ENGINE_TARGET == 'all') {
                                echo "Scaling down API server..."
                                sh '''
                                    kubectl scale deployment opensearch-benchmark-api-server \
                                        --replicas=0 -n benchmark-api || true
                                '''
                            }

                            // Scale down OpenSearch clusters
                            echo "Scaling down OpenSearch cluster(s)..."
                            if (params.ENGINE_TARGET == 'all') {
                                sh "gke-manifest/scale-down-clusters.sh all || true"
                            } else {
                                sh "gke-manifest/scale-down-clusters.sh ${params.ENGINE_TARGET} || true"
                            }
                        }
                    }
                }

                // Print final summary
                container('curl') {
                    sh """
                        if [ -f ${RESULTS_DIR}/BUILD_SUMMARY.txt ]; then
                            cat ${RESULTS_DIR}/BUILD_SUMMARY.txt
                        fi
                    """
                }
            }
        }

        success {
            echo "Benchmark pipeline completed successfully."
        }

        failure {
            echo "Benchmark pipeline failed. Check logs for details."
        }

        cleanup {
            cleanWs(
                deleteDirs: true,
                patterns: [
                    [pattern: '.pytest_cache/**', type: 'INCLUDE'],
                    [pattern: '__pycache__/**',   type: 'INCLUDE']
                ]
            )
        }
    }
}

// Made with Bob
