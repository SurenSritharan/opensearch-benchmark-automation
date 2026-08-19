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
                'complete-5m-profile-ingest'
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
        choice(
            name: 'LOG_LEVEL',
            choices: ['', 'debug', 'info', 'warning', 'error'],
            description: 'opensearch-benchmark log level. Leave blank to use the pipeline default. Set to "debug" to enable verbose OSB internal logging.'
        )
        booleanParam(
            name: 'ENABLE_PROFILING',
            defaultValue: false,
            description: 'Enable profiling to capture CPU profiling and flame graphs'
        )
        string(
            name: 'PROFILING_DURATION',
            defaultValue: '60',
            description: 'Default profiling duration is 60, can be modified'
        )
        string(
            name: 'STATS_INTERVAL',
            defaultValue: '',
            description: 'Interval in seconds to poll OpenSearch _nodes/stats during the run. Set to 0 or blank to disable. Each sample is appended as an NDJSON record to server-stats-timeseries.ndjson — one record per node per interval.'
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
        timeout(time: 7, unit: 'DAYS')
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
                echo "Results workspace: ${env.WORKSPACE}/${RESULTS_DIR}"
            }
        }

        // ── 2. Seed Certs ──────────────────────────────────────────────────────
        //     Must complete before worker pods start — they mount this secret.
        //     Always syncs from os-develop-<engine> so a cluster re-deploy that
        //     regenerates certs is picked up on every run, not just the first.
        stage('Copy Certs') {
            when {
                expression { params.SCALE_CLUSTERS }
            }
            steps {
                sh """
                    echo "Syncing opensearch-shared-certs from os-develop-${params.ENGINE} into benchmark-api-develop..."
                    kubectl get secret opensearch-shared-certs -n os-develop-${params.ENGINE} -o json | \
                        python3 -c "
import sys, json
s = json.load(sys.stdin)
s['metadata'] = {'name': s['metadata']['name'], 'namespace': 'benchmark-api-develop'}
print(json.dumps(s))
" | kubectl apply -f -
                    echo "✅ Certs synced into benchmark-api-develop"
                """
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
                                -n benchmark-api-develop --timeout=600s
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
                    // Count occurrences of each version so duplicate versions get _run1, _run2 labels.
                    def versionCounts = [:]
                    rawVersions.each { v -> versionCounts[v] = (versionCounts[v] ?: 0) + 1 }
                    def versionOccurrence = [:]
                    def runs = []
                    rawVersions.each { v ->
                        def occ = (versionOccurrence[v] ?: 0) + 1
                        versionOccurrence[v] = occ
                        def versionLabel = (versionCounts[v] > 1) ? "${v}_run${occ}" : v
                        rawSizes.each { s -> runs << [version: v, nodeSize: s, versionLabel: versionLabel] }
                    }

                    // first_run_steps: when a pipeline defines this key, run-pipeline.sh is
                    // invoked with --first-run on the very first run so it reads first_run_steps
                    // (build + search). All subsequent runs use steps (search-only).
                    def hasFirstRunSteps = pipelineJson.first_run_steps && pipelineJson.first_run_steps.size() > 0
                    def isFirstRun = true

                    def redeploy = params.REDEPLOY_CLUSTERS || pipelineJson.redeploy == true
                    def multiRun = pipelineJson.versions || pipelineJson.node_sizes

                    sh "mkdir -p ${RESULTS_DIR}"

                    runs.each { run ->
                        def version      = run.version
                        def runSize      = run.nodeSize
                        def versionLabel = run.versionLabel
                        def runKey       = "${versionLabel}/${runSize}"
                        def runExtraArgs = "--version ${version} --node-size ${runSize} --force"
                        // Always wipe the PVC on the first run when first_run_steps are defined.
                        // Also wipe on subsequent runs if DELETE_PVCS was explicitly requested.
                        if (params.DELETE_PVCS || (hasFirstRunSteps && isFirstRun)) { runExtraArgs += " --delete-pvcs" }

                        stage("${params.ENGINE} / ${versionLabel} / ${runSize}") {
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
                                        echo "Waiting for \$NS cluster health (green + 0 initializing shards)..."
                                        LAST_PROGRESS=\$SECONDS
                                        LAST_ACTIVE=9999
                                        STALL_LIMIT=7200
                                        RETRIED=0
                                        while true; do
                                            { set +x; } 2>/dev/null
                                            HEALTH=\$(kubectl exec -n \$NS opensearch-data-0 -c opensearch -- \
                                                curl -sk -u admin:admin \
                                                'https://localhost:9200/_cluster/health' 2>/dev/null || true)
                                            STATUS=\$(echo "\$HEALTH" | grep -oP '(?<="status":")[^"]+' || echo "unknown")
                                            INIT=\$(echo "\$HEALTH" | grep -oP '(?<="initializing_shards":)\\d+' || echo 0)
                                            RELOC=\$(echo "\$HEALTH" | grep -oP '(?<="relocating_shards":)\\d+' || echo 0)
                                            UNASSIGNED=\$(echo "\$HEALTH" | grep -oP '(?<="unassigned_shards":)\\d+' || echo 0)
                                            ACTIVE=\$(kubectl exec -n \$NS opensearch-data-0 -c opensearch -- \
                                                curl -sk -u admin:admin \
                                                'https://localhost:9200/_cat/recovery?h=stage&active_only=true' \
                                                2>/dev/null | grep -c . || true)
                                            set -x
                                            if [ "\$STATUS" = "green" ] && [ "\$INIT" = "0" ]; then
                                                echo "  ✅ [\$NS] cluster green — ready"
                                                break
                                            fi
                                            STALLED=\$((SECONDS - LAST_PROGRESS))
                                            echo "  [\$NS] status=\${STATUS}  initializing=\${INIT}  relocating=\${RELOC}  unassigned=\${UNASSIGNED}  active_recoveries=\${ACTIVE}  (stall \${STALLED}s/\${STALL_LIMIT}s)"
                                            if [ "\$ACTIVE" -lt "\$LAST_ACTIVE" ]; then
                                                LAST_PROGRESS=\$SECONDS
                                            fi
                                            LAST_ACTIVE=\$ACTIVE
                                            STALLED=\$((SECONDS - LAST_PROGRESS))
                                            if [ "\$STALLED" -ge "\$STALL_LIMIT" ]; then
                                                if [ "\$RETRIED" = "0" ]; then
                                                    echo "⚠️  [\$NS] shard recovery stalled for \${STALL_LIMIT}s — retrying failed shards"
                                                    kubectl exec -n \$NS opensearch-data-0 -c opensearch -- \
                                                        curl -sk -u admin:admin -X POST \
                                                        'https://localhost:9200/_cluster/reroute?retry_failed=true' > /dev/null || true
                                                    RETRIED=1
                                                    LAST_PROGRESS=\$SECONDS
                                                    LAST_ACTIVE=9999
                                                else
                                                    echo "❌ [\$NS] shard recovery stalled for \${STALL_LIMIT}s after retry — giving up"
                                                    exit 1
                                                fi
                                            fi
                                            sleep 10
                                        done
                                    """
                                }

                                        // ── b) Run benchmark ───────────────────────────────
                                        // On the first run, --first-run is passed so run-pipeline.sh
                                        // reads first_run_steps (build + search). All subsequent runs
                                        // omit the flag and read steps (search-only), reusing the PVC.
                                        // RESULTS_DEST + WORKER_POD are consumed by run-pipeline.sh's
                                        // incremental copy helper so each scenario is pulled to the
                                        // Jenkins workspace as soon as it finishes.
                                        sh """
                                            set +e
                                            DEST="${RESULTS_DIR}/${runKey}/test-runs/${params.ENGINE}"
                                            mkdir -p "\$DEST"
                                            API_URL=${params.API_URL} \
                                            RESULTS_DEST="\$DEST" \
                                            WORKER_POD="opensearch-benchmark-worker-${params.ENGINE}-0" \
                                            cloud-service/scripts/run-pipeline.sh \
                                                --pipeline ${pipeline} \
                                                ${hasFirstRunSteps && isFirstRun ? "--first-run" : ""} \
                                                ${params.LOG_LEVEL ? "--log-level ${params.LOG_LEVEL}" : ""} \
                                                ${params.ENABLE_PROFILING ? "--enable-profiling" : ""} \
                                                ${params.ENABLE_PROFILING ? "--profiling-duration ${params.PROFILING_DURATION}" : ""} \
                                                ${params.STATS_INTERVAL?.trim() ? "--stats-interval ${params.STATS_INTERVAL.trim()}" : ""} \
                                                ${params.ENGINE} \
                                                2>&1 | tee benchmark-run-develop-${params.ENGINE}-${versionLabel}-${runSize}.log
                                            PIPE_RC=\${PIPESTATUS[0]}

                                            JOB_ID=\$(grep -oP '(?<=Job ID: )\\S+' benchmark-run-develop-${params.ENGINE}-${versionLabel}-${runSize}.log | tail -1 || true)
                                            if [ -n "\$JOB_ID" ]; then
                                                echo "\$JOB_ID" > job_id_develop-${params.ENGINE}-${versionLabel}-${runSize}.txt
                                                echo "[develop] Job ID captured: \$JOB_ID"
                                            fi

                                            exit \$PIPE_RC
                                        """
                            } finally {
                                // ── c) Collect results ────────────────────────────
                                // Scenario subdirs are already copied incrementally by
                                // run-pipeline.sh during the run. This block captures the
                                // run log and final job-status JSON, and does a catch-up
                                // kubectl cp in case any copy was missed mid-run.
                                sh """
                                    mkdir -p ${RESULTS_DIR}/${runKey}
                                    cp benchmark-run-develop-${params.ENGINE}-${versionLabel}-${runSize}.log ${RESULTS_DIR}/${runKey}/ 2>/dev/null || true

                                    if [ -f job_id_develop-${params.ENGINE}-${versionLabel}-${runSize}.txt ]; then
                                        JOB_ID=\$(cat job_id_develop-${params.ENGINE}-${versionLabel}-${runSize}.txt)
                                        echo "=== develop @ ${versionLabel}/${runSize} (job: \$JOB_ID) ==="

                                        curl -s "${params.API_URL}/api/v1/benchmark/\$JOB_ID?engine=${params.ENGINE}" \
                                            | jq '.' > ${RESULTS_DIR}/${runKey}/job-status-develop.json
                                        jq '{job_id, status, scenarios_completed, scenarios_total}' \
                                            ${RESULTS_DIR}/${runKey}/job-status-develop.json

                                        DEST="${RESULTS_DIR}/${runKey}/test-runs/${params.ENGINE}"
                                        mkdir -p "\$DEST"
                                        kubectl cp -c worker benchmark-api-develop/opensearch-benchmark-worker-${params.ENGINE}-0:/results/\$JOB_ID/${params.ENGINE}/. "\$DEST/" >/dev/null 2>&1 \
                                            || echo "WARNING: catch-up kubectl cp failed — worker pod may not be running"

                                        echo "  View: ${params.API_URL}/results.html?job_id=\$JOB_ID"
                                        echo "  Results dir: ${env.WORKSPACE}/${RESULTS_DIR}/${runKey}"
                                    else
                                        echo "WARNING: no job_id for ${versionLabel}/${runSize} — benchmark may not have submitted"
                                    fi

                                    # Server logs and telemetry are collected per scenario by
                                    # _collect_scenario_server_logs() in run-pipeline.sh as each
                                    # scenario completes. They land in:
                                    #   <RESULTS_DEST>/<scenario_key>/server-logs/

                                    # ── Worker log ─────────────────────────────────
                                    WORKER_LOG="${RESULTS_DIR}/${runKey}/server-logs/worker-${params.ENGINE}.log"
                                    kubectl logs statefulset/opensearch-benchmark-worker-${params.ENGINE} \
                                        -n benchmark-api-develop --tail=5000 2>&1 > "\$WORKER_LOG" || true
                                    echo "Worker log: \$WORKER_LOG (\$(wc -l < \$WORKER_LOG) lines)"
                                """
                            }
                        }
                        } // stage
                        isFirstRun = false
                    } // runs.each
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
                                JOB_FILE="job_id_develop-${params.ENGINE}-\${version}-\${size}.txt"
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
                // Cancel any running cloud service jobs before scale-down kills the worker pods.
                // Runs on ABORTED and FAILURE — both end with a scale-down that would orphan
                // running jobs and leave them stuck in 'running' state indefinitely.
                if (currentBuild.currentResult in ['ABORTED', 'FAILURE']) {
                    echo "Build ${currentBuild.currentResult} — cancelling any running cloud service jobs..."

                    // Only send cancel requests if the API server pod is running.
                    // If it's already down, the worker process is gone and there is nothing to cancel.
                    // Note: on pod restart, init_db() cancels any orphaned 'running' jobs —
                    // so skipping cancel here does not leave jobs permanently stuck in a running state.
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
                                [ -f "\$JOB_FILE" ] || continue
                                JOB_ID=\$(cat "\$JOB_FILE")
                                echo "Cancelling develop job \$JOB_ID..."
                                curl -s -X POST "${params.API_URL}/api/v1/benchmark/\$JOB_ID/cancel?engine=${params.ENGINE}" || true

                                # Poll until the job reaches a terminal status (max 60s)
                                echo "Waiting for job \$JOB_ID to stop..."
                                for attempt in \$(seq 1 12); do
                                    STATUS=\$(curl -s "${params.API_URL}/api/v1/benchmark/\$JOB_ID?engine=${params.ENGINE}" \
                                        | jq -r '.status // "unknown"')
                                    echo "  status: \$STATUS"
                                    case "\$STATUS" in
                                        completed|failed|partial|cancelled|error) break ;;
                                    esac
                                    sleep 5
                                done
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
