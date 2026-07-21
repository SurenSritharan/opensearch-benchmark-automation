pipeline {
    // Develop branch pipeline — targets os-develop-<engine> clusters in benchmark-api-develop namespace.
    // This IS the primary Jenkinsfile on the develop branch.
    // The production multi-engine pipeline lives in Jenkinsfile.main (main branch).
    //
    // Prerequisites on the Jenkins node: kubectl, curl, jq, bash
    // Kubeconfig is sourced from the gke-kubeconfig Secret file credential.
    agent any

    parameters {
        choice(
            name: 'PIPELINE',
            choices: [
                'complete',
                'search-all',
                'search-compare',
            ],
            description: 'Pipeline to run. Corresponds to a file in the pipelines/ directory.'
        )
        string(
            name: 'PIPELINE_OVERRIDE',
            defaultValue: '',
            description: 'Optional: name of any pipeline in pipelines/ (overrides the choice above).'
        )
        choice(
            name: 'ENGINE',
            choices: ['jvector', 'faiss', 'lucene'],
            description: 'Engine to benchmark against. Targets the os-develop-<engine> cluster.'
        )
        string(
            name: 'API_URL',
            defaultValue: 'http://136.116.139.175',
            description: 'Base URL of the develop benchmark cloud service API.'
        )
        booleanParam(
            name: 'SCALE_CLUSTERS',
            defaultValue: true,
            description: 'Scale up the os-develop-<engine> cluster + develop worker before benchmarking and scale them down afterwards.'
        )
        booleanParam(
            name: 'REDEPLOY_CLUSTERS',
            defaultValue: false,
            description: 'Re-deploy the os-develop-<engine> cluster before benchmarking (applies OPENSEARCH_VERSION). Implies scale-up.'
        )
        string(
            name: 'OPENSEARCH_VERSION',
            defaultValue: '3.7.0',
            description: 'OpenSearch version to deploy (used when REDEPLOY_CLUSTERS is true, or on first-time deploy). Use 3.5.0.2 for baseline, 3.6.0 for Derived Source, 3.7.0 for latest, 3.8.0 for NVQ.'
        )
        booleanParam(
            name: 'DELETE_PVCS',
            defaultValue: false,
            description: 'Delete PVCs during re-deploy — destroys all indexed data. Only applies when REDEPLOY_CLUSTERS is true.'
        )
        booleanParam(
            name: 'SKIP_SCALE_DOWN',
            defaultValue: false,
            description: 'Skip post-run scale-down so the cluster and worker stay up for investigation.'
        )
    }

    environment {
        RESULTS_DIR = "results/${BUILD_ID}"
        KUBECONFIG  = "${env.WORKSPACE}/.kube/config"
    }

    // triggers {
    //     // No automatic cron — interns trigger manually or on a schedule they define.
    //     // Uncomment and adjust to enable a cadence:
    //     cron('0 8 * * *')  
    // }

    options {
        buildDiscarder(logRotator(numToKeepStr: '30', artifactNumToKeepStr: '10'))
        timestamps()
        timeout(time: 16, unit: 'HOURS')
        disableConcurrentBuilds()
    }

    stages {

        // ── 1. Prepare ─────────────────────────────────────────────────────────
        stage('Prepare') {
            steps {
                withCredentials([file(credentialsId: 'gke-kubeconfig', variable: 'KUBECONFIG_SRC')]) {
                    sh '''
                        mkdir -p "$(dirname "${KUBECONFIG}")"
                        cp "${KUBECONFIG_SRC}" "${KUBECONFIG}"
                        chmod 600 "${KUBECONFIG}"
                    '''
                }
                sh 'chmod +x gke-manifest/*.sh cloud-service/scripts/*.sh'
            }
        }

        // ── 2. Seed Certs ──────────────────────────────────────────────────────
        //     Must complete before worker pods start — they mount this secret.
        stage('Copy Certs') {
            when {
                expression { params.SCALE_CLUSTERS }
            }
            steps {
                sh '''
                    if ! kubectl get secret opensearch-shared-certs -n benchmark-api-develop &>/dev/null; then
                        echo "Copying opensearch-shared-certs into benchmark-api-develop..."
                        kubectl get secret opensearch-shared-certs -n benchmark-api -o json | \
                            python3 -c "
import sys, json
s = json.load(sys.stdin)
s['metadata'] = {'name': s['metadata']['name'], 'namespace': 'benchmark-api-develop'}
print(json.dumps(s))
" | kubectl apply -f -
                        echo "✅ Certs available in benchmark-api-develop"
                    else
                        echo "✅ opensearch-shared-certs already present in benchmark-api-develop"
                    fi
                '''
            }
        }

        // ── 3. Prepare Worker ─────────────────────────────────────────────────
        stage('Prepare Worker') {
            when {
                expression { params.SCALE_CLUSTERS }
            }
            steps {
                script {
                def workerBranchKey = "benchmark-worker-${params.ENGINE}"
                parallel(
                    'benchmark-api-server': {
                        sh '''
                            # Apply the develop-specific API server (benchmark-api-develop namespace)
                            kubectl apply -f gke-manifest/opensearch-benchmark-api-server.yaml
                            kubectl scale deployment opensearch-benchmark-api-server \
                                --replicas=0 -n benchmark-api-develop
                            kubectl wait --for=delete pod \
                                -l app=opensearch-benchmark-develop,component=api-server \
                                -n benchmark-api-develop --timeout=120s || true
                            kubectl scale deployment opensearch-benchmark-api-server \
                                --replicas=1 -n benchmark-api-develop
                            kubectl wait --for=condition=available deployment/opensearch-benchmark-api-server \
                                -n benchmark-api-develop --timeout=300s
                        '''
                    },
                    (workerBranchKey): {
                        sh """
                            cat gke-manifest/opensearch-benchmark-worker-template.yaml | \
                                sed 's/\\\${ENGINE}/${params.ENGINE}/g' | \
                                kubectl apply -f -
                            kubectl scale statefulset opensearch-benchmark-worker-${params.ENGINE} \
                                --replicas=0 -n benchmark-api-develop
                            kubectl wait --for=delete pod \
                                -l app=opensearch-benchmark-develop,component=worker,engine=${params.ENGINE} \
                                -n benchmark-api-develop --timeout=120s || true
                            kubectl scale statefulset opensearch-benchmark-worker-${params.ENGINE} \
                                --replicas=1 -n benchmark-api-develop
                            kubectl rollout status statefulset/opensearch-benchmark-worker-${params.ENGINE} \
                                -n benchmark-api-develop --timeout=1200s
                        """
                    }
                )
                } // script
            }
        }

        // ── 4. Verify Health ───────────────────────────────────────────────────
        stage('Verify Health') {
            steps {
                sh """
                    echo "=== os-develop-${params.ENGINE} cluster ==="
                    kubectl get pods -n os-develop-${params.ENGINE} 2>/dev/null || echo "(not deployed)"
                    echo ""
                    echo "=== Benchmark API develop (benchmark-api-develop namespace) ==="
                    kubectl get pods -n benchmark-api-develop
                """
                sh """
                    curl -sf ${params.API_URL}/health || \
                        (echo "WARNING: API health check failed — service may still be starting" && true)
                """
            }
        }

        // ── 5. Seed Dataset Cache ──────────────────────────────────────────────
        stage('Seed Dataset Cache') {
            steps {
                script {
                    def pipeline      = params.PIPELINE_OVERRIDE?.trim() ?: params.PIPELINE
                    def pipelineJson  = readJSON file: "pipelines/${pipeline}.json"
                    def corpusSizeVal = pipelineJson.params?.corpus_size
                    def corpusSizes   = corpusSizeVal ? [corpusSizeVal] : ['1m', '5m']
                    def datasets      = (pipelineJson.steps ?: []).collect { it.dataset }.unique()

                    def filesToSeed = []
                    def datasetsConfig = readYaml file: 'config/datasets.yaml'

                    datasets.each { dataset ->
                        def cacheFiles = datasetsConfig.datasets[dataset]?.gcs_cache_files ?: []
                        cacheFiles.each { entry ->
                            if (corpusSizes.contains(entry.corpus_size)) {
                                filesToSeed << entry
                            }
                        }
                    }

                    if (filesToSeed.isEmpty()) {
                        echo "No GCS cache files required for ${pipeline} — skipping"
                        return
                    }

                    filesToSeed.each { entry ->
                        def gcsPath    = entry.gcs_path
                        def targetPath = entry.target_path
                        def fileName   = gcsPath.tokenize('/').last()

                        echo "Seeding ${fileName} to develop worker..."
                        sh """
                            set -euo pipefail
                            POD="opensearch-benchmark-worker-${params.ENGINE}-0"

                            if kubectl exec -n benchmark-api-develop \$POD -- test -f '${targetPath}' 2>/dev/null; then
                                echo "[develop] ${fileName} already present — skipping"
                            else
                                echo "[develop] Downloading ${fileName} from GCS..."
                                kubectl exec -n benchmark-api-develop \$POD -- sh -c \
                                    "mkdir -p \$(dirname '${targetPath}') && gcloud storage cp '${gcsPath}' '${targetPath}'"
                                FINAL_SIZE=\$(kubectl exec -n benchmark-api-develop \$POD -- \
                                    stat -c '%s' '${targetPath}' 2>/dev/null || echo 0)
                                echo "[develop] Done — \${FINAL_SIZE} bytes"
                            fi
                        """
                    }
                }
            }
        }

        // ── 6. Per-Run (version × node_size) ──────────────────────────────────
        //    Loops over every (version, nodeSize) combination declared in the
        //    pipeline JSON — same pattern as the main Jenkinsfile.
        //    Single-version / single-size pipelines produce one iteration.
        //    Results land in <RESULTS_DIR>/<version>/<size>/.
        stage('Per-Run') {
            steps {
                script {
                    def pipeline     = params.PIPELINE_OVERRIDE?.trim() ?: params.PIPELINE
                    def pipelineJson = readJSON file: "pipelines/${pipeline}.json"

                    // Build the list of (version, nodeSize) pairs to iterate.
                    // versions and node_sizes can each be a single value or an array —
                    // all combinations are run sequentially.
                    def rawVersions = pipelineJson.versions   ?: [params.OPENSEARCH_VERSION]
                    def rawSizes    = pipelineJson.node_sizes ?: (pipelineJson.node_size ? [pipelineJson.node_size] : ['small'])
                    def runs = []
                    rawVersions.each { v -> rawSizes.each { s -> runs << [version: v, nodeSize: s] } }

                    def redeploy = params.REDEPLOY_CLUSTERS || pipelineJson.redeploy == true
                    def multiRun = pipelineJson.versions || pipelineJson.node_sizes

                    sh "mkdir -p ${RESULTS_DIR}"

                    runs.each { run ->
                        def version = run.version
                        def runSize = run.nodeSize
                        def runKey  = "${version}/${runSize}"
                        def runExtraArgs = "--version ${version} --node-size ${runSize} --force"
                        if (params.DELETE_PVCS) { runExtraArgs += " --delete-pvcs" }

                        echo "════════════════════════════════════════"
                        echo "Benchmarking OpenSearch ${version} / ${runSize}"
                        echo "════════════════════════════════════════"

                        catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                            try {
                                // ── a) Scale up or deploy the cluster ─────────────
                                if (params.SCALE_CLUSTERS) {
                                    sh """
                                        NS="os-develop-${params.ENGINE}"
                                        if [ "${multiRun}" = "true" ] || [ "${redeploy}" = "true" ]; then
                                            echo "Deploying \$NS (version ${version}, size ${runSize})..."
                                            gke-manifest/deploy-namespace-cluster.sh \$NS ${runExtraArgs}
                                        else
                                            STS_COUNT=\$(kubectl get statefulset -n \$NS --no-headers 2>/dev/null | wc -l)
                                            if [ "\$STS_COUNT" -gt 0 ]; then
                                                echo "Scaling up existing \$NS cluster..."
                                                gke-manifest/scale-up-clusters.sh \$NS
                                            else
                                                echo "No StatefulSets in \$NS — running initial deploy..."
                                                gke-manifest/deploy-namespace-cluster.sh \$NS ${runExtraArgs}
                                            fi
                                        fi

                                        # Wait for manager pod
                                        kubectl rollout status statefulset/opensearch-cluster-manager \
                                            -n \$NS --timeout=300s

                                        # Wait for all data pods to be Running
                                        echo "Waiting for opensearch-data pods to be Running in \$NS..."
                                        RUNNING=0
                                        LAST_PROGRESS=\$SECONDS
                                        STALL_LIMIT=300
                                        while true; do
                                            NEW_RUNNING=\$(kubectl get pods -n \$NS -l app=opensearch-data \
                                                --field-selector=status.phase=Running \
                                                --no-headers 2>/dev/null | wc -l)
                                            TOTAL=\$(kubectl get pods -n \$NS -l app=opensearch-data \
                                                --no-headers 2>/dev/null | wc -l)
                                            echo "  [\$NS] data pods Running: \${NEW_RUNNING}/\${TOTAL}"
                                            if [ "\$NEW_RUNNING" -ge 3 ] && [ "\$TOTAL" -ge 3 ]; then
                                                echo "  ✅ [\$NS] all data pods Running"
                                                break
                                            fi
                                            if [ "\$NEW_RUNNING" -gt "\$RUNNING" ]; then
                                                RUNNING=\$NEW_RUNNING; LAST_PROGRESS=\$SECONDS
                                            fi
                                            STALLED=\$((SECONDS - LAST_PROGRESS))
                                            if [ "\$STALLED" -ge "\$STALL_LIMIT" ]; then
                                                echo "❌ [\$NS] data pods stalled at \${RUNNING}/3 for \${STALL_LIMIT}s — giving up"
                                                exit 1
                                            fi
                                            sleep 10
                                        done

                                        # Wait for cluster to reach green with no initializing shards
                                        echo "Waiting for \$NS cluster health..."
                                        LAST_PROGRESS_SCORE="0.0"
                                        LAST_PROGRESS=\$SECONDS
                                        STALL_LIMIT=600
                                        while true; do
                                            HEALTH=\$(kubectl exec -n \$NS opensearch-data-0 -c opensearch -- \
                                                curl -sk -u admin:admin \
                                                'https://localhost:9200/_cluster/health' 2>/dev/null || true)
                                            STATUS=\$(echo "\$HEALTH" | grep -oP '(?<="status":")[^"]+' || true)
                                            INIT=\$(echo "\$HEALTH" | grep -oP '(?<="initializing_shards":)\\d+' || echo 0)
                                            if [ "\$STATUS" = "green" ] && [ "\${INIT:-1}" = "0" ]; then
                                                echo "  ✅ [\$NS] cluster green — ready"
                                                break
                                            elif [ "\$STATUS" = "yellow" ] || [ "\$STATUS" = "green" ]; then
                                                echo "  [\$NS] cluster \${STATUS} but \${INIT} shards initializing — waiting..."
                                            fi
                                            RECOVERY=\$(kubectl exec -n \$NS opensearch-data-0 -c opensearch -- \
                                                curl -sk -u admin:admin \
                                                'https://localhost:9200/_cat/recovery?h=index,shard,stage,bytes_percent,translog_ops_percent&active_only=true' \
                                                2>/dev/null || true)
                                            if [ -n "\$RECOVERY" ]; then
                                                echo "  [\$NS] status=\${STATUS:-unknown} initializing=\${INIT}"
                                                echo "\$RECOVERY" | while read line; do echo "    \$line"; done
                                                SCORE=\$(echo "\$RECOVERY" | awk '{sum += \$4 + \$5} END {printf "%.1f", sum}')
                                                MAX_SCORE=\$(echo "\$RECOVERY" | awk 'END {printf "%.1f", NR * 200}')
                                            else
                                                echo "  [\$NS] status=\${STATUS:-unknown} initializing=\${INIT} (no active recoveries)"
                                                # No active recoveries means transfers completed — treat as progress
                                                LAST_PROGRESS=\$SECONDS
                                                SCORE=0; MAX_SCORE=0
                                            fi
                                            if [ "\$(echo "\$SCORE \$LAST_PROGRESS_SCORE" | awk '{print (\$1 > \$2)}')" = "1" ]; then
                                                LAST_PROGRESS_SCORE=\$SCORE; LAST_PROGRESS=\$SECONDS
                                            fi
                                            if [ "\$(echo "\$MAX_SCORE" | awk '{print (\$1 > 0)}')" = "1" ] && \
                                               [ "\$(echo "\$SCORE \$MAX_SCORE" | awk '{print (\$1 >= \$2)}')" = "1" ]; then
                                                sleep 10; continue
                                            fi
                                            STALLED=\$((SECONDS - LAST_PROGRESS))
                                            if [ "\$STALLED" -ge "\$STALL_LIMIT" ]; then
                                                echo "❌ [\$NS] shard recovery stalled for \${STALL_LIMIT}s — giving up"
                                                exit 1
                                            fi
                                            sleep 10
                                        done
                                    """
                                }

                                // ── b) Run benchmark ──────────────────────────────
                                sh """
                                    set +e
                                    API_URL=${params.API_URL} \
                                    cloud-service/scripts/run-pipeline.sh \
                                        --pipeline ${pipeline} \
                                        ${params.ENGINE} \
                                        2>&1 | tee benchmark-run-develop-${version}-${runSize}.log
                                    PIPE_RC=\${PIPESTATUS[0]}

                                    JOB_ID=\$(grep -oP '(?<=Job ID: )\\S+' benchmark-run-develop-${version}-${runSize}.log | tail -1 || true)
                                    if [ -n "\$JOB_ID" ]; then
                                        echo "\$JOB_ID" > job_id_develop-${version}-${runSize}.txt
                                        echo "[develop] Job ID captured: \$JOB_ID"
                                    fi

                                    exit \$PIPE_RC
                                """
                            } finally {
                                // ── c) Collect results ────────────────────────────
                                sh """
                                    mkdir -p ${RESULTS_DIR}/${runKey}
                                    cp benchmark-run-develop-${version}-${runSize}.log ${RESULTS_DIR}/${runKey}/ 2>/dev/null || true

                                    if [ -f job_id_develop-${version}-${runSize}.txt ]; then
                                        JOB_ID=\$(cat job_id_develop-${version}-${runSize}.txt)
                                        echo "=== develop @ ${version}/${runSize} (job: \$JOB_ID) ==="

                                        curl -s "${params.API_URL}/api/v1/benchmark/\$JOB_ID?engine=${params.ENGINE}" \
                                            | jq '.' > ${RESULTS_DIR}/${runKey}/job-status-develop.json
                                        jq '{job_id, status, scenarios_completed, scenarios_total}' \
                                            ${RESULTS_DIR}/${runKey}/job-status-develop.json

                                        DEST="${RESULTS_DIR}/${runKey}/test-runs/${params.ENGINE}"
                                        mkdir -p "\$DEST"
                                        kubectl cp benchmark-api-develop/opensearch-benchmark-worker-${params.ENGINE}-0:/results/\$JOB_ID/${params.ENGINE}/. "\$DEST/" 2>/dev/null \
                                            && echo "  Copied results -> \$DEST/" \
                                            || echo "WARNING: kubectl cp failed — worker pod may not be running"

                                        echo "  View: ${params.API_URL}/results.html?job_id=\$JOB_ID"
                                    else
                                        echo "WARNING: no job_id for ${version}/${runSize} — benchmark may not have submitted"
                                    fi

                                    # ── Server logs ────────────────────────────────
                                    NS="os-develop-${params.ENGINE}"
                                    LOG_DIR="${RESULTS_DIR}/${runKey}/server-logs/\$NS"
                                    mkdir -p "\$LOG_DIR"

                                    echo "Collecting logs from namespace: \$NS"

                                    PODS=\$(kubectl get pods -n \$NS \
                                        --no-headers \
                                        -o custom-columns=':metadata.name,:spec.nodeName' 2>/dev/null \
                                        | while read POD NODE; do
                                            POOL=\$(kubectl get node "\$NODE" \
                                                -o jsonpath='{.metadata.labels.cloud\\.google\\.com/gke-nodepool}' \
                                                2>/dev/null || true)
                                            if [ "\$POOL" = "server-pool" ]; then echo "\$POD"; fi
                                        done || true)

                                    if [ -z "\$PODS" ]; then
                                        echo "  No server-pool pods found in \$NS — skipping"
                                    else
                                        for POD in \$PODS; do
                                            CONTAINERS=\$(kubectl get pod "\$POD" -n \$NS \
                                                -o jsonpath='{.spec.containers[*].name}' 2>/dev/null || true)
                                            for CONTAINER in \$CONTAINERS; do
                                                LOGFILE="\$LOG_DIR/\${POD}-\${CONTAINER}.log"
                                                kubectl logs "\$POD" -c "\$CONTAINER" -n \$NS \
                                                    --tail=5000 2>&1 > "\$LOGFILE" || true
                                                SIZE=\$(wc -l < "\$LOGFILE" 2>/dev/null || echo 0)
                                                echo "  \$POD / \$CONTAINER: \${SIZE} lines -> \$LOGFILE"
                                            done

                                            GC_LOGFILE="\$LOG_DIR/\${POD}-gc.log"
                                            kubectl exec "\$POD" -c opensearch -n \$NS -- \
                                                cat /usr/share/opensearch/logs/gc.log \
                                                > "\$GC_LOGFILE" 2>/dev/null || true
                                            if [ -s "\$GC_LOGFILE" ]; then
                                                echo "  \$POD gc.log: \$(wc -l < \$GC_LOGFILE) lines"
                                            else
                                                rm -f "\$GC_LOGFILE"
                                                echo "  \$POD gc.log: not found (skipping)"
                                            fi

                                            HPROF_FILES=\$(kubectl exec "\$POD" -c opensearch -n \$NS -- \
                                                sh -c 'ls /usr/share/opensearch/data/*.hprof 2>/dev/null || true')
                                            for HPROF in \$HPROF_FILES; do
                                                HPROF_NAME=\$(basename "\$HPROF")
                                                LOCAL_HPROF="\$LOG_DIR/\${POD}-\${HPROF_NAME}"
                                                echo "  Found heap dump: \$HPROF — copying..."
                                                kubectl cp "\$NS/\${POD}:\${HPROF}" "\$LOCAL_HPROF" \
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
                                            echo "Namespace:       \$NS"
                                            echo "Build ID:        ${BUILD_ID}"
                                            echo ""
                                            echo "Collected Logs:"
                                            echo "------------------------------------------------------------"
                                            ls -lh "\$LOG_DIR" 2>/dev/null | awk 'NR>1 {print "  " \$NF "  " \$5}' || true
                                        } > "\$LOG_DIR/SUMMARY.txt"

                                        echo "  Summary written: \$LOG_DIR/SUMMARY.txt"
                                    fi

                                    # ── Telemetry ──────────────────────────────────
                                    TEL_DIR="${RESULTS_DIR}/${runKey}/server-logs/\$NS/telemetry"
                                    mkdir -p "\$TEL_DIR"
                                    OS_HOST="opensearch-cluster.\$NS.svc.cluster.local:9200"

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
                                        kubectl exec -n benchmark-api-develop opensearch-benchmark-worker-${params.ENGINE}-0 -c worker -- \
                                            curl -sk -u admin:admin "https://\$OS_HOST\$ENDPOINT" \
                                            > "\$TEL_DIR/\$FILENAME" 2>/dev/null || true
                                        echo "  telemetry: \$FILENAME"
                                    done

                                    # ── Worker log ─────────────────────────────────
                                    WORKER_LOG="${RESULTS_DIR}/${runKey}/server-logs/worker-${params.ENGINE}.log"
                                    kubectl logs statefulset/opensearch-benchmark-worker-${params.ENGINE} \
                                        -n benchmark-api-develop --tail=5000 2>&1 > "\$WORKER_LOG" || true
                                    echo "Worker log: \$WORKER_LOG (\$(wc -l < \$WORKER_LOG) lines)"
                                """
                            }
                        }
                    }
                }
            }
        }

        // ── 7. Build Summary ───────────────────────────────────────────────────
        stage('Build Summary') {
            steps {
                script {
                    def pipeline = params.PIPELINE_OVERRIDE?.trim() ?: params.PIPELINE
                    def pipelineJson = readJSON file: "pipelines/${pipeline}.json"
                    def rawVersions = pipelineJson.versions   ?: [params.OPENSEARCH_VERSION]
                    def rawSizes    = pipelineJson.node_sizes ?: (pipelineJson.node_size ? [pipelineJson.node_size] : ['small'])

                    sh """
                        cat > ${RESULTS_DIR}/BUILD_SUMMARY.txt << EOF
========================================
OpenSearch Benchmark Build Summary (develop)
========================================
Build ID:  ${BUILD_ID}
Build URL: ${BUILD_URL}
Date:      \$(date -u +"%Y-%m-%d %H:%M:%S UTC")

Parameters:
  Pipeline:           ${pipeline}
  Cluster:            os-develop-${params.ENGINE}
  API URL:            ${params.API_URL}
  Scale Clusters:     ${params.SCALE_CLUSTERS}
  Redeploy Clusters:  ${params.REDEPLOY_CLUSTERS}
  OpenSearch Version: ${params.OPENSEARCH_VERSION}

Results per run:
EOF
                        for version_dir in ${RESULTS_DIR}/*/; do
                            version=\$(basename "\$version_dir")
                            for size_dir in "\$version_dir"*/; do
                                size=\$(basename "\$size_dir")
                                echo "  --- OpenSearch \$version / \$size ---" >> ${RESULTS_DIR}/BUILD_SUMMARY.txt
                                JOB_FILE="job_id_develop-\${version}-\${size}.txt"
                                if [ -f "\$JOB_FILE" ]; then
                                    JOB_ID=\$(cat "\$JOB_FILE")
                                    echo "    develop: ${params.API_URL}/results.html?job_id=\${JOB_ID}" \
                                        >> ${RESULTS_DIR}/BUILD_SUMMARY.txt
                                else
                                    echo "    develop: no job submitted" \
                                        >> ${RESULTS_DIR}/BUILD_SUMMARY.txt
                                fi
                            done
                        done
                        echo "========================================" >> ${RESULTS_DIR}/BUILD_SUMMARY.txt
                        cat ${RESULTS_DIR}/BUILD_SUMMARY.txt
                    """
                }
            }
        }

        // ── 8. Archive ─────────────────────────────────────────────────────────
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
                if (currentBuild.currentResult == 'ABORTED') {
                    def apiRunning = sh(
                        script: """
                            PHASE=\$(kubectl get pods -n benchmark-api-develop -l app=opensearch-benchmark-develop,component=api-server \
                                --no-headers 2>/dev/null | awk '{print \$3}' | head -1)
                            [ "\$PHASE" = "Running" ]
                        """,
                        returnStatus: true
                    ) == 0

                    if (apiRunning) {
                        sh """
                            for JOB_FILE in job_id_develop-*.txt; do
                                if [ -f "\$JOB_FILE" ]; then
                                    JOB_ID=\$(cat "\$JOB_FILE")
                                    echo "Cancelling develop job \$JOB_ID..."
                                    curl -s -X POST "${params.API_URL}/api/v1/benchmark/\$JOB_ID/cancel?engine=${params.ENGINE}" || true
                                fi
                            done
                        """
                    }
                }

                if (params.SCALE_CLUSTERS && !params.SKIP_SCALE_DOWN) {
                    echo "Scaling down develop worker and API server..."
                    sh """
                        kubectl scale statefulset opensearch-benchmark-worker-${params.ENGINE} \
                            --replicas=0 -n benchmark-api-develop || true
                        kubectl scale deployment opensearch-benchmark-api-server \
                            --replicas=0 -n benchmark-api-develop || true
                    """
                    echo "Scaling down os-develop-${params.ENGINE} cluster..."
                    sh "gke-manifest/scale-down-clusters.sh os-develop-${params.ENGINE} || true"
                }

                sh """
                    if [ -f ${RESULTS_DIR}/BUILD_SUMMARY.txt ]; then
                        cat ${RESULTS_DIR}/BUILD_SUMMARY.txt
                    fi
                """
            }
        }

        success { echo "Develop benchmark pipeline completed successfully." }
        failure { echo "Develop benchmark pipeline failed. Check logs for details." }

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
