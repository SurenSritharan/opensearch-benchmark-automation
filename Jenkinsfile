pipeline {
    // Main branch pipeline — targets os-<engine> clusters in benchmark-api namespace.
    // This IS the primary Jenkinsfile on the main branch.
    //
    // Prerequisites on the Jenkins node: kubectl, curl, jq, bash
    // Kubeconfig is sourced from the gke-kubeconfig Secret file credential.
    agent any

    parameters {
        text(
            name: 'WAIT_FOR_JOBS',
            defaultValue: 'ACL Pipeline',
            description: 'Newline-separated list of Jenkins job names to wait for before starting. The pipeline will block until all listed jobs finish. Leave blank to skip.'
        )
        choice(
            name: 'PIPELINE',
            choices: [
                'search-compare',
                'search-all',
                'search-all-overquery',
                'overquery-5m-k10',
                'search-1m',
                'search-5m',
                'complete-1m',
                'complete-5m',
                'complete',
                'complete-1m-profile-ingest',
                'complete-5m-profile-ingest',
                'msmarco-jvector-hq-build',
                'msmarco-jvector-hq-full',
                'msmarco-jvector-hq-search',
                'msmarco-jvector-index-sweep-1m',
                'msmarco-jvector-index-sweep-auto-1m',
                'parquet-50k',
                'parquet-1m',
                'dbpedia-parquet-50k',
                'dbpedia-parquet-500k',
                'dbpedia-parquet-1m',
                'complete-5m-test',
            ],
            description: 'Pipeline to run. Corresponds to a file in the pipelines/ directory.'
        )
        string(
            name: 'PIPELINE_OVERRIDE',
            defaultValue: '',
            description: 'Optional: name of a custom pipeline in the pipelines/ directory (overrides the choice above). Must exist as pipelines/<name>.json.'
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
            description: 'OpenSearch version to deploy (used when REDEPLOY_CLUSTERS is true, or on first-time deploy). Use 3.5.0.2 for baseline, 3.6.0 for Derived Source, 3.7.0 for latest, 3.8.0 for NVQ.'
        )
        booleanParam(
            name: 'DELETE_PVCS',
            defaultValue: false,
            description: 'Delete PVCs during re-deploy — destroys all indexed data (only used when REDEPLOY_CLUSTERS is true)'
        )
        booleanParam(
            name: 'SKIP_SCALE_DOWN',
            defaultValue: false,
            description: 'Skip post-run scale-down (keeps clusters and workers running for investigation)'
        )
        choice(
            name: 'LOG_LEVEL',
            choices: ['', 'debug', 'info', 'warning', 'error'],
            description: 'opensearch-benchmark log level. Leave blank for default. Set "debug" for verbose OSB internal logging.'
        )
        booleanParam(
            name: 'ENABLE_PROFILING',
            defaultValue: false,
            description: 'Profile all steps in this run. Off by default. Use for ad-hoc investigation without editing pipeline JSON — e.g. pick search-1m and check this to profile every step. Individual steps can also be profiled permanently via "profile": true in the pipeline JSON, which takes precedence over this flag.'
        )
        string(
            name: 'PROFILING_DURATION',
            defaultValue: '60',
            description: 'Duration in seconds for async-profiler sampling (used when ENABLE_PROFILING is true).'
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

        // ── 0. Wait for upstream jobs ──────────────────────────────────────────
        // Block until every job listed in WAIT_FOR_JOBS is no longer building.
        // Jobs are polled in parallel; each waits independently. Polls every 60 s;
        // times out after 4 hours per job.
        stage('Wait for Upstream Jobs') {
            when {
                expression { params.WAIT_FOR_JOBS?.trim() }
            }
            steps {
                script {
                    def jobs = params.WAIT_FOR_JOBS.split('\n').collect { it.trim() }.findAll { it }
                    echo "Waiting for upstream job(s): ${jobs.join(', ')}"

                    def waitBranches = jobs.collectEntries { jobName ->
                        [(jobName): {
                            def apiUrl = "${env.JENKINS_URL}job/${jobName.replace(' ', '%20')}/lastBuild/api/json"
                            timeout(time: 4, unit: 'HOURS') {
                                waitUntil(initialRecurrencePeriod: 60000) {
                                    def building = sh(
                                        script: """curl -sf --max-time 10 '${apiUrl}' | python3 -c "import sys,json; d=json.load(sys.stdin); print('true' if d.get('building') else 'false')" 2>/dev/null || echo 'false'""",
                                        returnStdout: true
                                    ).trim()
                                    if (building == 'true') {
                                        echo "'${jobName}' is currently running — waiting 60 s..."
                                        return false
                                    }
                                    echo "'${jobName}' is not running — proceeding."
                                    return true
                                }
                            }
                        }]
                    }

                    parallel waitBranches
                }
            }
        }

        // ── 1. Prepare ─────────────────────────────────────────────────────────
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
                // Sync TLS certs to all namespaces so workers and clusters share the same CA.
                sh './gke-manifest/sync-certs.sh'
                echo "Results workspace: ${env.WORKSPACE}/${RESULTS_DIR}"
            }
        }

        // ── 2. Prepare Workers ─────────────────────────────────────────────────
        //    Brings the API server and benchmark workers up before seeding and
        //    benchmarking. OpenSearch cluster scale-up/deploy happens per-engine
        //    inside the Run Benchmarks stage so each engine starts independently.
        stage('Prepare Workers') {
            when {
                expression { params.SCALE_CLUSTERS }
            }
            steps {
                script {
                    def engines = params.ENGINE_TARGET == 'all'
                        ? ['jvector', 'faiss', 'lucene']
                        : [params.ENGINE_TARGET.replace('os-', '')]

                    def waitBranches = [:]

                    waitBranches['benchmark-api-server'] = {
                        sh '''
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
                                kubectl scale statefulset opensearch-benchmark-worker-${engine} \
                                    --replicas=0 -n benchmark-api
                                kubectl wait --for=delete pod \
                                    -l app=opensearch-benchmark,component=worker,engine=${engine} \
                                    -n benchmark-api --timeout=120s || true
                                kubectl scale statefulset opensearch-benchmark-worker-${engine} \
                                    --replicas=1 -n benchmark-api
                                kubectl rollout status statefulset/opensearch-benchmark-worker-${engine} \
                                    -n benchmark-api --timeout=1200s
                            """
                        }
                    }

                    parallel waitBranches
                }
            }
        }

        // ── 3. Verify Health ───────────────────────────────────────────────────
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

        // ── 4. Seed Dataset Cache ──────────────────────────────────────────────
        //    Ensures GCS-hosted corpus files are present on every worker PVC
        //    before benchmarking starts. Which files to seed is driven entirely
        //    by the `gcs_cache_files` list in config/datasets.yaml.
        stage('Seed Dataset Cache') {
            steps {
                script {
                    def engines = params.ENGINE_TARGET == 'all'
                        ? ['jvector', 'faiss', 'lucene']
                        : [params.ENGINE_TARGET.replace('os-', '')]

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

                    echo "Files to seed: ${filesToSeed.collect { it.gcs_path }.join(', ')}"

                    filesToSeed.each { entry ->
                        def gcsPath    = entry.gcs_path
                        def targetPath = entry.target_path
                        def fileName   = gcsPath.tokenize('/').last()

                        echo "Seeding ${fileName} to ${engines.size()} worker(s)..."

                        def seedBranches = engines.collectEntries { engine ->
                            [(engine): {
                                sh """
                                    set -euo pipefail
                                    POD="opensearch-benchmark-worker-${engine}-0"

                                    if kubectl exec -n benchmark-api \$POD -- test -f '${targetPath}' 2>/dev/null; then
                                        echo "[${engine}] ${fileName} already present — skipping"
                                    else
                                        echo "[${engine}] Downloading ${fileName} from GCS..."
                                        kubectl exec -n benchmark-api \$POD -- sh -c \
                                            "mkdir -p \$(dirname '${targetPath}') && gcloud storage cp '${gcsPath}' '${targetPath}'"
                                        FINAL_SIZE=\$(kubectl exec -n benchmark-api \$POD -- \
                                            stat -c '%s' '${targetPath}' 2>/dev/null || echo 0)
                                        echo "[${engine}] Done — \${FINAL_SIZE} bytes"
                                    fi
                                """
                            }]
                        }

                        parallel seedBranches
                    }
                }
            }
        }

        // ── 5. Run Benchmarks ──────────────────────────────────────────────────
        //    Outer loop: iterates over each (version, node_size) combination.
        //    Inner loop: runs all engines in parallel, each executing:
        //      a) Scale up / deploy cluster
        //      b) Run benchmark pipeline
        //      c) Fetch & save results
        //      d) Collect server logs & telemetry
        //
        //    Results land in <RESULTS_DIR>/<version>/<node_size>/.
        stage('Run Benchmarks') {
            steps {
                script {
                    def engines = params.ENGINE_TARGET == 'all'
                        ? ['jvector', 'faiss', 'lucene']
                        : [params.ENGINE_TARGET.replace('os-', '')]

                    // Cancel any running/queued jobs from a previous build.
                    sh """
                        for engine in ${engines.join(' ')}; do
                            JOBS=\$(curl -s "${params.API_URL}/api/v1/benchmark?engine=\$engine" \
                                | jq -r '.jobs[] | select(.status == "running" or .status == "queued") | .job_id' 2>/dev/null || true)
                            for JOB_ID in \$JOBS; do
                                echo "Cancelling stale \$engine job: \$JOB_ID"
                                curl -s -X POST "${params.API_URL}/api/v1/benchmark/\$JOB_ID/cancel?engine=\$engine" || true
                            done
                        done
                    """

                    def pipeline     = params.PIPELINE_OVERRIDE?.trim() ?: params.PIPELINE
                    def pipelineJson = readJSON file: "pipelines/${pipeline}.json"

                    def rawVersions  = pipelineJson.versions   ?: [params.OPENSEARCH_VERSION]
                    def rawSizes     = pipelineJson.node_sizes ?: (pipelineJson.node_size ? [pipelineJson.node_size] : ['small'])
                    def versionCounts = [:]
                    rawVersions.each { v -> versionCounts[v] = (versionCounts[v] ?: 0) + 1 }
                    def versionOccurrence = [:]
                    def runs = []
                    rawVersions.each { v ->
                        def occ = (versionOccurrence[v] ?: 0) + 1
                        versionOccurrence[v] = occ
                        def versionLabel = (versionCounts[v] > 1) ? "${v}_run${occ}" : v
                        rawSizes.each { s ->
                            runs << [version: v, nodeSize: s, versionLabel: versionLabel]
                        }
                    }

                    sh "mkdir -p ${RESULTS_DIR}"

                    def redeploy = params.REDEPLOY_CLUSTERS || pipelineJson.redeploy == true
                    def extraArgs = "--version ${params.OPENSEARCH_VERSION} --node-size small --force"
                    if (params.DELETE_PVCS) { extraArgs += " --delete-pvcs" }

                    def hasFirstRunSteps = pipelineJson.first_run_steps && pipelineJson.first_run_steps.size() > 0
                    def multiRun = pipelineJson.versions || pipelineJson.node_sizes

                    def engineBranches = engines.collectEntries { engine ->
                        def ns = "os-${engine}"
                        [(engine): {
                            catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                                def engineFirstRun = true
                                runs.each { run ->
                                    if (currentBuild.currentResult == 'ABORTED') { return }
                                    def version      = run.version
                                    def runSize      = run.nodeSize
                                    def versionLabel = run.versionLabel
                                    def runKey       = "${versionLabel}/${runSize}"
                                    def isFirstRun   = engineFirstRun
                                    engineFirstRun   = false

                                    def runExtraArgs = "--version ${version} --node-size ${runSize} --force"
                                    if (params.DELETE_PVCS || (hasFirstRunSteps && isFirstRun)) { runExtraArgs += " --delete-pvcs" }

                                    stage("${engine} / ${versionLabel} / ${runSize}") {
                                    echo "════════════════════════════════════════"
                                    echo "[${engine}] Benchmarking OpenSearch ${version} / ${runSize}"
                                    echo "════════════════════════════════════════"

                                    try {
                                        // ── a) Scale up or deploy this engine's cluster ────
                                        if (params.SCALE_CLUSTERS) {
                                            sh """
                                                if [ "${multiRun}" = "true" ] || [ "${redeploy}" = "true" ]; then
                                                    echo "Deploying ${ns} (version ${version}, size ${runSize})..."
                                                    gke-manifest/deploy-namespace-cluster.sh ${ns} ${runExtraArgs}
                                                else
                                                    STS_COUNT=\$(kubectl get statefulset -n ${ns} --no-headers 2>/dev/null | wc -l)
                                                    if [ "\$STS_COUNT" -gt 0 ]; then
                                                        echo "Scaling up existing cluster in ${ns}..."
                                                        gke-manifest/scale-up-clusters.sh ${ns}
                                                    else
                                                        echo "No StatefulSets in ${ns} — running initial deploy..."
                                                        gke-manifest/deploy-namespace-cluster.sh ${ns} ${extraArgs}
                                                    fi
                                                fi

                                                # Wait for manager pod
                                                # 600s timeout: the install-jvector-plugin init container
                                                # downloads from Maven Central at startup, which regularly
                                                # takes 5+ minutes on cold nodes.
                                                kubectl rollout status statefulset/opensearch-cluster-manager \
                                                    -n ${ns} --timeout=600s

                                                # Wait for all data pods to be Running
                                                echo "Waiting for opensearch-data pods to be Running in ${ns}..."
                                                RUNNING=0
                                                LAST_PROGRESS=\$SECONDS
                                                STALL_LIMIT=600
                                                while true; do
                                                    NEW_RUNNING=\$(kubectl get pods -n ${ns} -l app=opensearch-data \
                                                        --field-selector=status.phase=Running \
                                                        --no-headers 2>/dev/null | wc -l)
                                                    TOTAL=\$(kubectl get pods -n ${ns} -l app=opensearch-data \
                                                        --no-headers 2>/dev/null | wc -l)
                                                    echo "  [${ns}] data pods Running: \${NEW_RUNNING}/\${TOTAL}"
                                                    if [ "\$NEW_RUNNING" -ge 3 ] && [ "\$TOTAL" -ge 3 ]; then
                                                        echo "  ✅ [${ns}] all data pods Running"
                                                        break
                                                    fi
                                                    if [ "\$NEW_RUNNING" -gt "\$RUNNING" ]; then
                                                        RUNNING=\$NEW_RUNNING
                                                        LAST_PROGRESS=\$SECONDS
                                                    fi
                                                    STALLED=\$((SECONDS - LAST_PROGRESS))
                                                    if [ "\$STALLED" -ge "\$STALL_LIMIT" ]; then
                                                        echo "❌ [${ns}] data pods stalled at \${RUNNING}/3 for \${STALL_LIMIT}s — giving up"
                                                        exit 1
                                                    fi
                                                    sleep 10
                                                done

                                                # Wait for cluster to reach green with no initializing shards
                                                echo "Waiting for ${ns} cluster health (green + 0 initializing shards)..."
                                                LAST_PROGRESS=\$SECONDS
                                                LAST_ACTIVE=9999
                                                STALL_LIMIT=7200
                                                RETRIED=0
                                                while true; do
                                                    { set +x; } 2>/dev/null
                                                    HEALTH=\$(kubectl exec -n ${ns} opensearch-data-0 -c opensearch -- \
                                                        curl -sk -u admin:admin \
                                                        'https://localhost:9200/_cluster/health' 2>/dev/null || true)
                                                    STATUS=\$(echo "\$HEALTH" | grep -oP '(?<="status":")[^"]+' || echo "unknown")
                                                    INIT=\$(echo "\$HEALTH" | grep -oP '(?<="initializing_shards":)\\d+' || echo 0)
                                                    RELOC=\$(echo "\$HEALTH" | grep -oP '(?<="relocating_shards":)\\d+' || echo 0)
                                                    UNASSIGNED=\$(echo "\$HEALTH" | grep -oP '(?<="unassigned_shards":)\\d+' || echo 0)
                                                    ACTIVE=\$(kubectl exec -n ${ns} opensearch-data-0 -c opensearch -- \
                                                        curl -sk -u admin:admin \
                                                        'https://localhost:9200/_cat/recovery?h=stage&active_only=true' \
                                                        2>/dev/null | grep -c . || true)
                                                    set -x
                                                    if [ "\$STATUS" = "green" ] && [ "\$INIT" = "0" ]; then
                                                        echo "  ✅ [${ns}] cluster green — ready"
                                                        break
                                                    fi
                                                    STALLED=\$((SECONDS - LAST_PROGRESS))
                                                    echo "  [${ns}] status=\${STATUS}  initializing=\${INIT}  relocating=\${RELOC}  unassigned=\${UNASSIGNED}  active_recoveries=\${ACTIVE}  (stall \${STALLED}s/\${STALL_LIMIT}s)"
                                                    if [ "\$ACTIVE" -lt "\$LAST_ACTIVE" ]; then
                                                        LAST_PROGRESS=\$SECONDS
                                                    fi
                                                    LAST_ACTIVE=\$ACTIVE
                                                    STALLED=\$((SECONDS - LAST_PROGRESS))
                                                    if [ "\$STALLED" -ge "\$STALL_LIMIT" ]; then
                                                        if [ "\$RETRIED" = "0" ]; then
                                                            echo "⚠️  [${ns}] shard recovery stalled for \${STALL_LIMIT}s — retrying failed shards"
                                                            kubectl exec -n ${ns} opensearch-data-0 -c opensearch -- \
                                                                curl -sk -u admin:admin -X POST \
                                                                'https://localhost:9200/_cluster/reroute?retry_failed=true' > /dev/null || true
                                                            RETRIED=1
                                                            LAST_PROGRESS=\$SECONDS
                                                            LAST_ACTIVE=9999
                                                        else
                                                            echo "❌ [${ns}] shard recovery stalled for \${STALL_LIMIT}s after retry — giving up"
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
                                            DEST="${RESULTS_DIR}/${runKey}/test-runs/${engine}"
                                            mkdir -p "\$DEST"
                                            API_URL=${params.API_URL} \
                                            RESULTS_DEST="\$DEST" \
                                            OS_NAMESPACE="${ns}" \
                                            WORKER_POD="opensearch-benchmark-worker-${engine}-0" \
                                            cloud-service/scripts/run-pipeline.sh \
                                                --pipeline ${pipeline} \
                                                ${hasFirstRunSteps && isFirstRun ? "--first-run" : ""} \
                                                ${params.LOG_LEVEL ? "--log-level ${params.LOG_LEVEL}" : ""} \
                                                ${params.ENABLE_PROFILING ? "--enable-profiling" : ""} \
                                                ${params.ENABLE_PROFILING ? "--profiling-duration ${params.PROFILING_DURATION}" : ""} \
                                                ${params.STATS_INTERVAL?.trim() ? "--stats-interval ${params.STATS_INTERVAL.trim()}" : ""} \
                                                ${engine} \
                                                2>&1 | tee benchmark-run-${engine}-${versionLabel}-${runSize}.log
                                            PIPE_RC=\${PIPESTATUS[0]}

                                            JOB_ID=\$(grep -oP '(?<=Job ID: )\\S+' benchmark-run-${engine}-${versionLabel}-${runSize}.log | tail -1 || true)
                                            if [ -n "\$JOB_ID" ]; then
                                                echo "\$JOB_ID" > job_id_${engine}-${versionLabel}-${runSize}.txt
                                                echo "[${engine}] Job ID captured: \$JOB_ID"
                                            fi

                                            exit \$PIPE_RC
                                        """
                                    } finally {
                                        // ── c) Fetch & save results ────────────────────────
                                        // Scenario subdirs are already copied incrementally by
                                        // run-pipeline.sh during the run. This block captures the
                                        // run log and final job-status JSON, and does a catch-up
                                        // kubectl cp in case any copy was missed mid-run.
                                        sh """
                                            mkdir -p ${RESULTS_DIR}/${runKey}
                                            cp benchmark-run-${engine}-${versionLabel}-${runSize}.log ${RESULTS_DIR}/${runKey}/ 2>/dev/null || true

                                            if [ -f job_id_${engine}-${versionLabel}-${runSize}.txt ]; then
                                                JOB_ID=\$(cat job_id_${engine}-${versionLabel}-${runSize}.txt)
                                                POD="opensearch-benchmark-worker-${engine}-0"
                                                echo "=== ${engine} @ ${versionLabel}/${runSize} (job: \$JOB_ID) ==="

                                                curl -s "${params.API_URL}/api/v1/benchmark/\$JOB_ID?engine=${engine}" \
                                                    | jq '.' > ${RESULTS_DIR}/${runKey}/job-status-${engine}.json
                                                jq '{job_id, status, scenarios_completed, scenarios_total}' \
                                                    ${RESULTS_DIR}/${runKey}/job-status-${engine}.json

                                                DEST="${RESULTS_DIR}/${runKey}/test-runs/${engine}"
                                                mkdir -p "\$DEST"
                                                kubectl cp -c worker benchmark-api/\$POD:/results/\$JOB_ID/${engine}/. "\$DEST/" >/dev/null 2>&1 \
                                                    || echo "WARNING: catch-up kubectl cp failed for ${engine} — worker pod may not be running"

                                                echo "  View: ${params.API_URL}/results.html?job_id=\$JOB_ID"
                                                echo "  Results dir: ${BUILD_URL}artifact/${RESULTS_DIR}/${runKey}"
                                            else
                                                echo "WARNING: no job_id for ${engine} @ ${versionLabel}/${runSize} — benchmark may not have submitted"
                                            fi

                                            # ── Worker log ─────────────────────────────────
                                            WORKER_LOG="${RESULTS_DIR}/${runKey}/server-logs/worker-${engine}.log"
                                            mkdir -p "${RESULTS_DIR}/${runKey}/server-logs"
                                            kubectl logs statefulset/opensearch-benchmark-worker-${engine} \
                                                -n benchmark-api --tail=5000 2>&1 > "\$WORKER_LOG" || true
                                            echo "Worker log: \$WORKER_LOG (\$(wc -l < \$WORKER_LOG) lines)"
                                        """
                                    } // try/finally
                                    } // stage
                                } // runs.each
                            } // catchError
                        }] // engineBranches entry
                    }

                    if (currentBuild.currentResult != 'ABORTED') {
                        parallel engineBranches
                    } else {
                        echo "Build aborted — skipping engine runs"
                    }
                }
            }
        }

        // ── 6. Build Summary ───────────────────────────────────────────────────
        stage('Build Summary') {
            steps {
                script {
                    def engines = params.ENGINE_TARGET == 'all'
                        ? ['jvector', 'faiss', 'lucene']
                        : [params.ENGINE_TARGET.replace('os-', '')]

                    def pipeline = params.PIPELINE_OVERRIDE?.trim() ?: params.PIPELINE

                    sh """
                        cat > ${RESULTS_DIR}/BUILD_SUMMARY.txt << EOF
========================================
OpenSearch Benchmark Build Summary
========================================
Build ID:  ${BUILD_ID}
Build URL: ${BUILD_URL}
Date:      \$(date -u +"%Y-%m-%d %H:%M:%S UTC")

Parameters:
  Pipeline:           ${pipeline}
  Engine Target:      ${params.ENGINE_TARGET}
  API URL:            ${params.API_URL}
  Scale Clusters:     ${params.SCALE_CLUSTERS}
  Redeploy Clusters:  ${params.REDEPLOY_CLUSTERS}
  OpenSearch Version: ${params.OPENSEARCH_VERSION}
  Enable Profiling:   ${params.ENABLE_PROFILING}

Results per engine:
EOF
                        for version_dir in ${RESULTS_DIR}/*/; do
                            version=\$(basename "\$version_dir")
                            echo "  --- OpenSearch \$version ---" >> ${RESULTS_DIR}/BUILD_SUMMARY.txt
                            for engine in ${engines.join(' ')}; do
                                JOB_FILE="job_id_\${engine}-\${version}.txt"
                                if [ -f "\$JOB_FILE" ]; then
                                    JOB_ID=\$(cat "\$JOB_FILE")
                                    echo "    \${engine}: ${params.API_URL}/results.html?job_id=\${JOB_ID}" \
                                        >> ${RESULTS_DIR}/BUILD_SUMMARY.txt
                                else
                                    echo "    \${engine}: no job submitted" \
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

        // ── 7. Archive Results ─────────────────────────────────────────────────
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
                def engines = params.ENGINE_TARGET == 'all'
                    ? ['jvector', 'faiss', 'lucene']
                    : [params.ENGINE_TARGET.replace('os-', '')]

                // Cancel any running cloud service jobs before scale-down kills the worker pods.
                if (currentBuild.currentResult in ['ABORTED', 'FAILURE']) {
                    echo "Build ${currentBuild.currentResult} — cancelling any running cloud service jobs..."

                    def apiRunning = sh(
                        script: """
                            PHASE=\$(kubectl get pods -n benchmark-api -l app=opensearch-benchmark,component=api-server \
                                --no-headers 2>/dev/null | awk '{print \$3}' | head -1)
                            [ "\$PHASE" = "Running" ]
                        """,
                        returnStatus: true
                    ) == 0

                    if (apiRunning) {
                        engines.each { engine ->
                            sh """
                                for JOB_FILE in job_id_${engine}-*.txt; do
                                    [ -f "\$JOB_FILE" ] || continue
                                    JOB_ID=\$(cat "\$JOB_FILE")
                                    echo "Cancelling ${engine} job \$JOB_ID..."
                                    curl -s -X POST "${params.API_URL}/api/v1/benchmark/\$JOB_ID/cancel?engine=${engine}" || true

                                    echo "Waiting for ${engine} job \$JOB_ID to stop..."
                                    for attempt in \$(seq 1 12); do
                                        STATUS=\$(curl -s "${params.API_URL}/api/v1/benchmark/\$JOB_ID?engine=${engine}" \
                                            | jq -r '.status // "unknown"')
                                        echo "  [${engine}] status: \$STATUS"
                                        case "\$STATUS" in
                                            completed|failed|partial|cancelled|error) break ;;
                                        esac
                                        sleep 5
                                    done
                                done
                            """
                        }
                    } else {
                        echo "API server pod is not running — skipping cancel"
                    }
                }

                if (params.SCALE_CLUSTERS && !params.SKIP_SCALE_DOWN) {

                    echo "Scaling down benchmark worker(s)..."
                    engines.each { engine ->
                        sh """
                            kubectl scale statefulset opensearch-benchmark-worker-${engine} \
                                --replicas=0 -n benchmark-api || true
                        """
                    }

                    if (params.ENGINE_TARGET == 'all') {
                        echo "Scaling down API server..."
                        sh '''
                            kubectl scale deployment opensearch-benchmark-api-server \
                                --replicas=0 -n benchmark-api || true
                        '''
                    }

                    echo "Scaling down OpenSearch cluster(s)..."
                    if (params.ENGINE_TARGET == 'all') {
                        sh "gke-manifest/scale-down-clusters.sh all || true"
                    } else {
                        sh "gke-manifest/scale-down-clusters.sh ${params.ENGINE_TARGET} || true"
                    }
                }

                sh """
                    if [ -f ${RESULTS_DIR}/BUILD_SUMMARY.txt ]; then
                        cat ${RESULTS_DIR}/BUILD_SUMMARY.txt
                    fi
                """
            }
        }

        success { echo "Benchmark pipeline completed successfully." }
        failure { echo "Benchmark pipeline failed. Check logs for details." }

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
