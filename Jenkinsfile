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
            defaultValue: '3.7.0',
            description: 'OpenSearch version to deploy (used when REDEPLOY_CLUSTERS is true, or on first-time cluster deploy)'
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

    triggers {
        cron('0 7 * * *')
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

                    // Each branch scales up (or deploys) its component then waits for it to be
                    // ready — all branches run fully in parallel so no component blocks another.
                    def waitBranches = [:]

                    engines.each { engine ->
                        def ns = "os-${engine}"
                        waitBranches["opensearch-cluster-${engine}"] = {
                            sh """
                                STS_COUNT=\$(kubectl get statefulset -n ${ns} --no-headers 2>/dev/null | wc -l)
                                if [ "\$STS_COUNT" -gt 0 ]; then
                                    echo "Scaling up existing cluster in ${ns}..."
                                    gke-manifest/scale-up-clusters.sh ${ns}
                                else
                                    echo "No StatefulSets found in ${ns} — running initial deploy (version ${params.OPENSEARCH_VERSION})..."
                                    gke-manifest/deploy-namespace-cluster.sh ${ns} \
                                        --version ${params.OPENSEARCH_VERSION} --force
                                fi
                                kubectl rollout status statefulset/opensearch-cluster-manager \
                                    -n ${ns} --timeout=300s
                                kubectl rollout status statefulset/opensearch-data \
                                    -n ${ns} --timeout=300s
                            """
                        }
                    }

                    waitBranches['benchmark-api-server'] = {
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
                            kubectl wait --for=condition=available deployment/opensearch-benchmark-api-server \
                                -n benchmark-api --timeout=300s
                        '''
                    }

                    engines.each { engine ->
                        waitBranches["benchmark-worker-${engine}"] = {
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
                                # rollout status waits for the StatefulSet controller to create the pod
                                kubectl rollout status statefulset/opensearch-benchmark-worker-${engine} \
                                    -n benchmark-api --timeout=1200s
                            """
                        }
                    }

                    parallel waitBranches
                }
            }
        }

        // ── 4. Verify cluster and worker health ────────────────────────────────
        //    Non-engine-specific checks run here (pod listing, API health).
        //    Per-engine cluster health polling runs inside the Per-Engine stage.
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
            }
        }

        // ── 5. Per-Engine (parallel) ───────────────────────────────────────────
        //    One branch per engine runs all four per-engine steps in sequence:
        //      a) Wait for cluster health (green/yellow)
        //      b) Run benchmark
        //      c) Fetch & save results
        //      d) Collect server logs & telemetry
        stage('Per-Engine') {
            steps {
                script {
                    def engines = params.ENGINE_TARGET == 'all'
                        ? ['jvector', 'faiss', 'lucene']
                        : [params.ENGINE_TARGET.replace('os-', '')]

                    def datasetEnv = params.DATASET == 'all' ? 'DATASET=' : "DATASET=${params.DATASET}"
                    def corpusSize = params.CORPUS_SIZE

                    sh "mkdir -p ${RESULTS_DIR}"

                    def engineBranches = engines.collectEntries { engine ->
                        def ns = "os-${engine}"
                        [(engine): {
                            // ── a) Wait for cluster health ─────────────────────
                            if (params.SCALE_CLUSTERS || params.REDEPLOY_CLUSTERS) {
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

                            // ── b) Run benchmark ───────────────────────────────
                            sh """
                                ${datasetEnv} \
                                API_URL=${params.API_URL} \
                                TIME_PERIOD=300 \
                                cloud-service/scripts/run-pipeline.sh \
                                    ${params.PIPELINE} \
                                    ${engine} \
                                    ${corpusSize} \
                                    2>&1 | tee benchmark-run-${engine}.log

                                JOB_ID=\$(grep -oP '(?<=Job ID: )\\S+' benchmark-run-${engine}.log | tail -1 || true)
                                if [ -n "\$JOB_ID" ]; then
                                    echo "\$JOB_ID" > job_id_${engine}.txt
                                    echo "[${engine}] Job ID captured: \$JOB_ID"
                                fi
                            """

                            // ── c) Fetch & save results ────────────────────────
                            sh """
                                cp benchmark-run-${engine}.log ${RESULTS_DIR}/ 2>/dev/null || true

                                if [ -f job_id_${engine}.txt ]; then
                                    JOB_ID=\$(cat job_id_${engine}.txt)
                                    echo "=== ${engine} (job: \$JOB_ID) ==="

                                    curl -s "${params.API_URL}/api/v1/benchmark/\$JOB_ID" \
                                        | jq '.' > ${RESULTS_DIR}/job-status-${engine}.json

                                    RESULTS_JSON=\$(curl -s "${params.API_URL}/api/v1/benchmark/\$JOB_ID/results")
                                    echo "\$RESULTS_JSON" | jq '.' > ${RESULTS_DIR}/results-${engine}.json

                                    jq '{job_id, status, scenarios_completed, scenarios_total}' \
                                        ${RESULTS_DIR}/job-status-${engine}.json

                                    SWEEP_COUNT=\$(echo "\$RESULTS_JSON" | jq '.sweeps | length')
                                    echo "  Extracting artifacts for \$SWEEP_COUNT sweep(s)..."
                                    for i in \$(seq 0 \$(( SWEEP_COUNT - 1 ))); do
                                        SWEEP_NAME=\$(echo "\$RESULTS_JSON" | jq -r ".sweeps[\$i].sweep_name // \"sweep-\$(( i + 1 ))\"")
                                        LABEL=\$(echo "\$RESULTS_JSON" | jq -r ".sweeps[\$i].scenario_label // \"\"")
                                        SWEEP_DIR="${RESULTS_DIR}/test-runs/${engine}/\${LABEL}/\${SWEEP_NAME}"
                                        mkdir -p "\$SWEEP_DIR"

                                        echo "\$RESULTS_JSON" | jq ".sweeps[\$i].test_run // {}" \
                                            > "\$SWEEP_DIR/test_run.json"
                                        echo "\$RESULTS_JSON" | jq ".sweeps[\$i].workload_params // {}" \
                                            > "\$SWEEP_DIR/workload-params.json"
                                        K8S=\$(echo "\$RESULTS_JSON" | jq ".sweeps[\$i].k8s_metrics")
                                        if [ "\$K8S" != "null" ]; then
                                            echo "\$K8S" | jq '.' > "\$SWEEP_DIR/k8s_metrics.json"
                                        fi
                                        echo "\$RESULTS_JSON" | jq -r ".sweeps[\$i].benchmark_log // \"\"" \
                                            > "\$SWEEP_DIR/benchmark.log"

                                        echo "    [\$SWEEP_NAME] \${LABEL} -> \$SWEEP_DIR"
                                    done

                                    echo "  View: ${params.API_URL}/results.html?job_id=\$JOB_ID"
                                else
                                    echo "WARNING: no job_id_${engine}.txt — ${engine} may not have submitted successfully"
                                fi
                            """

                            // ── d) Collect server logs & telemetry ─────────────
                            sh """
                                LOG_DIR="${RESULTS_DIR}/server-logs/${ns}"
                                mkdir -p "\$LOG_DIR"

                                echo "Collecting logs from namespace: ${ns}"

                                PODS=\$(kubectl get pods -n ${ns} \
                                    --no-headers \
                                    -o custom-columns=':metadata.name,:spec.nodeName' 2>/dev/null \
                                    | while read POD NODE; do
                                        POOL=\$(kubectl get node "\$NODE" \
                                            -o jsonpath='{.metadata.labels.cloud\\.google\\.com/gke-nodepool}' \
                                            2>/dev/null || true)
                                        if [ "\$POOL" = "server-pool" ]; then echo "\$POD"; fi
                                    done || true)

                                if [ -z "\$PODS" ]; then
                                    echo "  No server-pool pods found in ${ns} — skipping"
                                else
                                    for POD in \$PODS; do
                                        CONTAINERS=\$(kubectl get pod "\$POD" -n ${ns} \
                                            -o jsonpath='{.spec.containers[*].name}' 2>/dev/null || true)
                                        for CONTAINER in \$CONTAINERS; do
                                            LOGFILE="\$LOG_DIR/\${POD}-\${CONTAINER}.log"
                                            kubectl logs "\$POD" -c "\$CONTAINER" -n ${ns} \
                                                --tail=5000 2>&1 > "\$LOGFILE" || true
                                            SIZE=\$(wc -l < "\$LOGFILE" 2>/dev/null || echo 0)
                                            echo "  \$POD / \$CONTAINER: \${SIZE} lines -> \$LOGFILE"
                                        done

                                        GC_LOGFILE="\$LOG_DIR/\${POD}-gc.log"
                                        kubectl exec "\$POD" -c opensearch -n ${ns} -- \
                                            cat /usr/share/opensearch/logs/gc.log \
                                            > "\$GC_LOGFILE" 2>/dev/null || true
                                        if [ -s "\$GC_LOGFILE" ]; then
                                            echo "  \$POD gc.log: \$(wc -l < \$GC_LOGFILE) lines"
                                        else
                                            rm -f "\$GC_LOGFILE"
                                            echo "  \$POD gc.log: not found (skipping)"
                                        fi

                                        HPROF_FILES=\$(kubectl exec "\$POD" -c opensearch -n ${ns} -- \
                                            sh -c 'ls /usr/share/opensearch/data/*.hprof 2>/dev/null || true')
                                        for HPROF in \$HPROF_FILES; do
                                            HPROF_NAME=\$(basename "\$HPROF")
                                            LOCAL_HPROF="\$LOG_DIR/\${POD}-\${HPROF_NAME}"
                                            echo "  Found heap dump: \$HPROF — copying..."
                                            kubectl cp "${ns}/\${POD}:\${HPROF}" "\$LOCAL_HPROF" \
                                                -c opensearch 2>/dev/null || true
                                            if [ -s "\$LOCAL_HPROF" ]; then
                                                echo "  Saved: \$LOCAL_HPROF (\$(du -sh \$LOCAL_HPROF | cut -f1))"
                                            fi
                                        done
                                    done

                                    {
                                        echo "Server Log Collection Summary"
                                        echo "============================================================"
                                        echo "Collection Time: \$(date -u +'%Y-%m-%d %H:%M:%S UTC')"
                                        echo "Namespace:       ${ns}"
                                        echo "Build ID:        ${BUILD_ID}"
                                        echo ""
                                        echo "Collected Logs:"
                                        echo "------------------------------------------------------------"
                                        ls -lh "\$LOG_DIR" 2>/dev/null | awk 'NR>1 {print "  " \$NF "  " \$5}' || true
                                    } > "\$LOG_DIR/SUMMARY.txt"

                                    echo "  Summary written: \$LOG_DIR/SUMMARY.txt"
                                fi

                                TEL_DIR="${RESULTS_DIR}/server-logs/${ns}/telemetry"
                                mkdir -p "\$TEL_DIR"
                                OS_HOST="opensearch-cluster.${ns}.svc.cluster.local:9200"
                                OS_CURL="kubectl exec -n benchmark-api opensearch-benchmark-worker-${engine}-0 -c worker -- curl -sk -u admin:admin https://\$OS_HOST"

                                for ENDPOINT_FILE in \
                                    "/_cluster/health?pretty          cluster-health.json" \
                                    "/_cluster/stats?pretty           cluster-stats.json" \
                                    "/_cluster/settings?include_defaults=true&flat_settings=true&pretty  cluster-settings.json" \
                                    "/_nodes/stats?pretty             nodes-stats.json" \
                                    "/_cat/nodes?v&h=name,heap.percent,heap.current,heap.max,ram.percent,cpu,load_1m,load_5m  nodes.txt" \
                                    "/_cat/thread_pool?v&h=node_name,name,active,queue,rejected,largest,completed  thread-pools.txt" \
                                    "/_cat/tasks?v&detailed           tasks.txt" \
                                    "/_cat/segments?v                 segments.txt"
                                do
                                    ENDPOINT=\$(echo "\$ENDPOINT_FILE" | awk '{print \$1}')
                                    FILENAME=\$(echo "\$ENDPOINT_FILE" | awk '{print \$2}')
                                    \$OS_CURL "\$ENDPOINT" > "\$TEL_DIR/\$FILENAME" 2>/dev/null || true
                                    echo "  telemetry: \$FILENAME"
                                done

                                WORKER_LOG="${RESULTS_DIR}/server-logs/worker-${engine}.log"
                                kubectl logs statefulset/opensearch-benchmark-worker-${engine} \
                                    -n benchmark-api --tail=5000 2>&1 > "\$WORKER_LOG" || true
                                echo "Worker log: \$WORKER_LOG (\$(wc -l < \$WORKER_LOG) lines)"
                            """
                        }]
                    }

                    parallel engineBranches
                }
            }
        }

        // ── 6. Build summary ───────────────────────────────────────────────────
        stage('Build Summary') {
            steps {
                script {
                    def engines = params.ENGINE_TARGET == 'all'
                        ? ['jvector', 'faiss', 'lucene']
                        : [params.ENGINE_TARGET.replace('os-', '')]

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
