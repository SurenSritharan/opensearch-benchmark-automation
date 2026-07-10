pipeline {
    // No pod agent — all stages run on the Jenkins controller (or a labelled
    // internal agent). Jenkins is on the IBM VPN which already has:
    //   - kubectl access to GKE via kubeconfig (outbound to GKE API)
    //   - HTTP access to the benchmark API LoadBalancer (outbound to GCP)
    // The GKE pod → Jenkins callback (JNLP) is not needed at all.
    //
    // Prerequisites on the Jenkins node: kubectl, curl, jq, bash
    // Kubeconfig is sourced from the gke-kubeconfig Secret file credential,
    // generated from the jenkins ServiceAccount in benchmark-api namespace.
    agent any

    parameters {
        choice(
            name: 'PIPELINE',
            choices: ['complete', 'search-only'],
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
        booleanParam(
            name: 'SKIP_SCALE_DOWN',
            defaultValue: false,
            description: 'Skip the post-run scale-down so clusters and workers stay up for investigation. Overrides SCALE_CLUSTERS teardown.'
        )
    }

    environment {
        RESULTS_DIR = "results/${BUILD_ID}"
        // Kubeconfig written from the jenkins ServiceAccount credential.
        // Scoped to the workspace so concurrent builds don't collide.
        KUBECONFIG  = "${env.WORKSPACE}/.kube/config"
    }

    options {
        buildDiscarder(logRotator(numToKeepStr: '30', artifactNumToKeepStr: '10'))
        timestamps()
        timeout(time: 12, unit: 'HOURS')
        disableConcurrentBuilds()
    }

    stages {

        // ── 1. Prepare ─────────────────────────────────────────────────────────
        // Write the kubeconfig from the Jenkins credential into the workspace.
        // KUBECONFIG env var is set pipeline-wide so every kubectl call in every
        // subsequent stage picks it up automatically.
        stage('Prepare') {
            steps {
                withCredentials([file(credentialsId: 'gke-kubeconfig', variable: 'KUBECONFIG_SRC')]) {
                    sh '''
                        mkdir -p "$(dirname "${KUBECONFIG}")"
                        cp "${KUBECONFIG_SRC}" "${KUBECONFIG}"
                        chmod 600 "${KUBECONFIG}"
                    '''
                }
                sh '''
                    chmod +x gke-manifest/*.sh cloud-service/scripts/*.sh
                '''
            }
        }

        // ── 2. Re-deploy OpenSearch clusters (optional) ────────────────────────
        stage('Re-deploy Clusters') {
            when {
                expression { params.REDEPLOY_CLUSTERS }
            }
            steps {
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

        // ── 3. Scale up OpenSearch clusters + benchmark workers ────────────────
        //    Each engine (jvector/faiss/lucene) has:
        //      - An OpenSearch cluster in its own namespace (os-<engine>)
        //      - A dedicated benchmark worker pod in the benchmark-api namespace
        //    The shared API server (benchmark-api/opensearch-benchmark-api-server)
        //    is always scaled up alongside whichever workers are needed.
        //    All readiness waits run in parallel so cluster pod startup doesn't
        //    block worker pod startup and vice versa.
        stage('Scale Up') {
            when {
                // Re-deploy already starts the pods, so skip pure scale-up in that case
                expression { params.SCALE_CLUSTERS && !params.REDEPLOY_CLUSTERS }
            }
            steps {
                script {
                    def engines = params.ENGINE_TARGET == 'all'
                        ? ['jvector', 'faiss', 'lucene']
                        : [params.ENGINE_TARGET.replace('os-', '')]

                    // Kick off OpenSearch cluster scale-up (non-blocking — we wait below)
                    echo "Scaling up OpenSearch clusters: ${engines}"
                    if (params.ENGINE_TARGET == 'all') {
                        sh "gke-manifest/scale-up-clusters.sh all"
                    } else {
                        sh "gke-manifest/scale-up-clusters.sh ${params.ENGINE_TARGET}"
                    }

                    echo "Deploying/scaling up API server in benchmark-api namespace..."
                    sh '''
                        # apply is idempotent — creates if missing, updates if changed
                        # Scale to 0 first so the pod always restarts and picks up latest code
                        kubectl apply -f gke-manifest/opensearch-benchmark-api-server.yaml -n benchmark-api
                        kubectl scale deployment opensearch-benchmark-api-server \
                            --replicas=0 -n benchmark-api
                        kubectl wait --for=delete pod \
                            -l app=opensearch-benchmark,component=api-server \
                            -n benchmark-api --timeout=120s || true
                        kubectl scale deployment opensearch-benchmark-api-server \
                            --replicas=1 -n benchmark-api
                    '''

                    echo "Deploying/scaling up benchmark worker(s) in benchmark-api namespace..."
                    engines.each { engine ->
                        sh """
                            cat gke-manifest/opensearch-benchmark-worker-template.yaml | \
                                sed 's/\\\${ENGINE}/${engine}/g' | \
                                kubectl apply -f -
                            # Bounce the worker so it always restarts and picks up latest code via git pull
                            kubectl scale statefulset opensearch-benchmark-worker-${engine} \
                                --replicas=0 -n benchmark-api
                            kubectl wait --for=delete pod \
                                -l app=opensearch-benchmark,component=worker,engine=${engine} \
                                -n benchmark-api --timeout=120s || true
                            kubectl scale statefulset opensearch-benchmark-worker-${engine} \
                                --replicas=1 -n benchmark-api
                        """
                    }

                    // Wait for all pods in parallel — opensearch-cluster and
                    // benchmark-cloud-native service startups are independent and can
                    // take very different amounts of time.
                    echo "Waiting for all pods to be ready..."
                    def waitBranches = [:]
                    engines.each { engine ->
                        def ns = "os-${engine}"
                        waitBranches["opensearch-cluster-${engine}"] = {
                            sh """
                                kubectl wait --for=condition=ready pod \
                                    -l app=opensearch-cluster-manager -n ${ns} --timeout=300s
                                kubectl wait --for=condition=ready pod \
                                    -l app=opensearch-data -n ${ns} --timeout=300s
                            """
                        }
                    }
                    waitBranches['benchmark-api-server'] = {
                        sh '''
                            kubectl wait --for=condition=available deployment/opensearch-benchmark-api-server \
                                -n benchmark-api --timeout=300s
                        '''
                    }
                    engines.each { engine ->
                        waitBranches["benchmark-worker-${engine}"] = {
                            sh """
                                kubectl wait --for=condition=ready pod \
                                    -l app=opensearch-benchmark,component=worker,engine=${engine} \
                                    -n benchmark-api --timeout=1200s
                            """
                        }
                    }
                    parallel waitBranches
                }
            }
        }

        // ── 4. Verify cluster and worker health ────────────────────────────────
        stage('Verify Health') {
            steps {
                sh '''
                    echo "=== OpenSearch Clusters ==="
                    for ns in os-jvector os-faiss os-lucene; do
                        echo "--- $ns ---"
                        kubectl get pods -n $ns 2>/dev/null || echo "(not deployed)"
                    done
                    echo ""
                    echo "=== Benchmark API (benchmark-api namespace) ==="
                    kubectl get pods -n benchmark-api
                '''
                sh """
                    curl -sf ${params.API_URL}/health || \
                        (echo "WARNING: API health check failed — service may still be starting" && true)
                """
                script {
                    // Wait for every scaled-up OpenSearch cluster to reach green or yellow
                    // before allowing the benchmark to start. Pods being Ready is not
                    // sufficient — shard recovery after a cold restart can keep the cluster
                    // red for several minutes while pods are already serving HTTP.
                    if (params.SCALE_CLUSTERS || params.REDEPLOY_CLUSTERS) {
                        def engines = params.ENGINE_TARGET == 'all'
                            ? ['jvector', 'faiss', 'lucene']
                            : [params.ENGINE_TARGET.replace('os-', '')]

                        engines.each { engine ->
                            def ns = "os-${engine}"
                            sh """
                                echo "Waiting for OpenSearch cluster in ${ns} to be green or yellow..."
                                DEADLINE=\$((SECONDS + 300))
                                while [ \$SECONDS -lt \$DEADLINE ]; do
                                    STATUS=\$(kubectl exec -n ${ns} opensearch-data-0 -- \
                                        curl -sk -u admin:admin \
                                        https://localhost:9200/_cluster/health 2>/dev/null \
                                        | grep -oP '(?<="status":")[^"]+' || true)
                                    echo "  [${ns}] cluster status: \${STATUS:-unknown}"
                                    if [ "\$STATUS" = "green" ] || [ "\$STATUS" = "yellow" ]; then
                                        echo "  ✅ ${ns} is ready (\${STATUS})"
                                        break
                                    fi
                                    sleep 10
                                done
                                if [ "\$STATUS" != "green" ] && [ "\$STATUS" != "yellow" ]; then
                                    echo "❌ ${ns} cluster did not reach green/yellow within 5 minutes"
                                    exit 1
                                fi
                            """
                        }
                    }
                }
            }
        }

        // ── 5. Run benchmark via cloud service API ─────────────────────────────
        //    run-pipeline.sh builds the correct JSON payload for any
        //    engine/dataset/corpus combination and routes it to the right worker
        //    via the "engine" field in the API request.
        //    When ENGINE_TARGET=all, all three engines run in parallel — each
        //    gets its own job ID and results file.
        stage('Run Benchmark') {
            steps {
                script {
                    def engines = params.ENGINE_TARGET == 'all'
                        ? ['jvector', 'faiss', 'lucene']
                        : [params.ENGINE_TARGET.replace('os-', '')]

                    // Jenkins auto-exports build params as env vars, so DATASET=all would
                    // leak into the shell even when datasetEnv is empty. Explicitly unset it
                    // when "all" is selected so run-pipeline.sh uses its own default (both datasets).
                    def datasetEnv = params.DATASET == 'all' ? 'DATASET=' : "DATASET=${params.DATASET}"
                    // Similarly, pass empty string for corpus "all" — the script handles that arg directly
                    def corpusSize = params.CORPUS_SIZE

                    // Run all engines in parallel; each writes its own log and results file
                    def parallelBranches = engines.collectEntries { engine ->
                        ["${engine}" : {
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
                        }]
                    }

                    parallel parallelBranches
                }
            }
        }

        // ── 6. Fetch & save results ────────────────────────────────────────────
        //    One job ID per engine — fetch each independently and write per-engine
        //    files plus a combined summary. The shared results.html on the API server
        //    can filter by job_id to view any individual engine's results.
        stage('Fetch Results') {
            steps {
                script {
                    def engines = params.ENGINE_TARGET == 'all'
                        ? ['jvector', 'faiss', 'lucene']
                        : [params.ENGINE_TARGET.replace('os-', '')]

                    sh "mkdir -p ${RESULTS_DIR}"

                    engines.each { engine ->
                        sh """
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

        // ── 7. Archive results ─────────────────────────────────────────────────
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
                if (params.SCALE_CLUSTERS && !params.SKIP_SCALE_DOWN) {
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

                // Print final summary
                sh """
                    if [ -f ${RESULTS_DIR}/BUILD_SUMMARY.txt ]; then
                        cat ${RESULTS_DIR}/BUILD_SUMMARY.txt
                    fi
                """
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
                    [pattern: '.kube/**',        type: 'INCLUDE'],
                    [pattern: '.pytest_cache/**', type: 'INCLUDE'],
                    [pattern: '__pycache__/**',   type: 'INCLUDE']
                ]
            )
        }
    }
}

// Made with Bob
