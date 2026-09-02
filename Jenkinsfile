pipeline {
    // Unified Jenkinsfile — works on main, develop, and any feature/pipeline branch.
    //
    // Branch → environment mapping (resolved at runtime by getNamespace()):
    //   main / test-prod/*  → os-<engine>          in benchmark-api
    //   any other branch    → os-develop-<engine>   in benchmark-api-develop
    //
    // Namespace convention:
    //   os-<engine>              prod standard   (e.g. os-jvector, os-faiss, os-lucene)
    //   os-develop-<engine>      develop         (e.g. os-develop-jvector)
    //   os-<engine>-<pipeline>   prod isolated   (e.g. os-jvector-acl)
    //
    // The namespace must exist in GKE and have a RoleBinding in jenkins-agent-rbac.yaml.
    //
    // Adding a new isolated pipeline (e.g. ACL):
    //   1. Create GKE namespace: kubectl create namespace os-jvector-acl
    //   2. Add RoleBinding in jenkins-agent-rbac.yaml and re-apply it
    //   3. Create a separate Jenkins job pointing at this Jenkinsfile (own disableConcurrentBuilds)
    //   4. Set ENGINE_TARGET = jvector-acl in that job's default parameters
    //   No changes to this Jenkinsfile required.
    //
    // Set FORCE_PROD=true to force a develop-branch build to target the prod clusters.
    //
    // Multi-engine behaviour:
    //   prod  (isProd=true)  → engines run in PARALLEL   (fast, independent clusters)
    //   develop (isProd=false) → engines run SEQUENTIALLY (shared dev resources)
    //
    // Prerequisites on the Jenkins node: kubectl, curl, jq, bash
    // Kubeconfig is sourced from the gke-kubeconfig Secret file credential.
    agent any

    parameters {
        text(
            name: 'WAIT_FOR_JOBS',
            defaultValue: '',
            description: 'Newline-separated list of Jenkins job names to wait for before starting. The pipeline will block until all listed jobs finish. Leave blank to skip.'
        )
        choice(
            name: 'PIPELINE',
            choices: [
                // ── ACL benchmark sweep (run all variants in one job) ──────
                'acl-sweep-1m',
                'acl-sweep-5m',
                'acl-sweep-10m',
                // ── ACL individual variant pipelines ──────────────────────
                'acl-ultrastrict',
                'acl-ultrastrict-narrow',
                'acl-ultrastrict-micro',
                'acl-ultrastrict-fixed',
                'acl-e1-baseline',
                'acl-e2-tenant-only',
                'acl-e3-static-groups',
                'acl-m1-tenant-group',
                'acl-m2-full-identity',
                'acl-m3-identity-classification',
                'acl-c1-dual-validation',
                'acl-c3-large-users-list',
                'acl-c5-multi-tenant',
                'acl-c8-no-classification',
                // ── Standard non-ACL pipelines ────────────────────────────
                'complete',
                'search-all',
                'search-compare',
                'complete-5m-profile-ingest',
                'search-all-overquery',
                'overquery-5m-k10',
                'search-1m',
                'search-5m',
                'complete-1m',
                'complete-5m',
                'complete-1m-profile-ingest',
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
            description: 'Optional: name of any pipeline in pipelines/ (overrides the choice above). Must exist as pipelines/<name>.json.'
        )
        choice(
            name: 'ENGINE_TARGET',
            choices: ['all', 'jvector', 'faiss', 'lucene'],
            description: 'Engine to run. ACL pipelines always target jvector-acl automatically — this selector is ignored for acl-* pipelines.'
        )
        booleanParam(
            name: 'FORCE_PROD',
            defaultValue: false,
            description: 'Force this build to target prod clusters (os-<engine> / benchmark-api) regardless of branch. Useful on develop for hotfix validation.'
        )
        string(
            name: 'API_URL',
            defaultValue: '',
            description: 'Base URL of the benchmark cloud service API. Leave blank to use the branch default (prod: http://34.132.114.18, develop: http://136.116.139.175).'
        )
        booleanParam(
            name: 'SCALE_CLUSTERS',
            defaultValue: true,
            description: 'Scale up engine clusters + benchmark workers before benchmarking and scale them down afterwards.'
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
            description: 'Delete PVCs during re-deploy — destroys all indexed data. Only applies when REDEPLOY_CLUSTERS is true.'
        )
        booleanParam(
            name: 'SKIP_SCALE_DOWN',
            defaultValue: false,
            description: 'Skip post-run scale-down so clusters and workers stay up for investigation.'
        )
        choice(
            name: 'LOG_LEVEL',
            choices: ['', 'debug', 'info', 'warning', 'error'],
            description: 'opensearch-benchmark log level. Leave blank to use the pipeline default. Set to "debug" for verbose OSB internal logging.'
        )
        booleanParam(
            name: 'ENABLE_PROFILING',
            defaultValue: false,
            description: 'Enable profiling to capture CPU profiling and flame graphs.'
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
        booleanParam(
            name: 'ENABLE_PROFILING',
            defaultValue: false,
            description: 'Profile all steps in this run. Off by default. Use for ad-hoc investigation without editing pipeline JSON — e.g. pick search-1m and check this to profile every step. Individual steps can also be profiled permanently via "profile": true in the pipeline JSON, which takes precedence over this flag.'
        )
        string(
            name: 'PROFILING_DURATION',
            defaultValue: '60',
            description: 'How many seconds to run async-profiler per step (default: 60). The profiler starts at the beginning of each profiled step and stops after this duration — the step itself continues to completion. Recommended: 30–60s for CPU flame graphs.'
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
                script {
                    // Resolve and display environment context for this build.
                    def apiNs       = getApiNamespace()
                    def resolvedUrl = getApiUrl()
                    def engines     = getEngines()
                    echo "Branch context : ${isProd() ? 'PROD' : 'DEVELOP'}"
                    echo "API namespace  : ${apiNs}"
                    echo "API URL        : ${resolvedUrl}"
                    echo "Engines        : ${engines.join(', ')}"
                    echo "Results dir    : ${env.WORKSPACE}/${RESULTS_DIR}"
                }
            }
        }

        // ── 2. Copy Certs ──────────────────────────────────────────────────────
        //    The opensearch-shared-certs secret is created by deploy-namespace-cluster.sh
        //    on first deploy and persists in the OS namespace indefinitely.
        //    Copy it into the API namespace so worker pods can mount it.
        //    All engine namespaces share the same CA — copy from the first targeted engine.
        //    Always runs — certs must be current regardless of whether clusters are scaled.
        stage('Copy Certs') {
            steps {
                script {
                    def apiNs = getApiNamespace()
                    def srcNs = getNamespace(getEngines()[0])
                    sh """
                        echo "Copying opensearch-shared-certs from ${srcNs} → ${apiNs}..."
                        if ! kubectl get secret opensearch-shared-certs -n ${srcNs} -o name >/dev/null 2>&1; then
                            echo "⚠️  opensearch-shared-certs not found in ${srcNs} — skipping cert copy."
                            echo "   Run deploy-namespace-cluster.sh ${srcNs} first to generate certs."
                        else
                            SECRET_JSON=\$(kubectl get secret opensearch-shared-certs -n ${srcNs} -o json)
                            echo "\$SECRET_JSON" | python3 -c "
import sys, json
s = json.load(sys.stdin)
s['metadata'] = {'name': s['metadata']['name'], 'namespace': '${apiNs}'}
print(json.dumps(s))
" | kubectl apply -f -
                            echo "✅ Certs copied into ${apiNs}"
                        fi
                    """
                }
            }
        }

        // ── 3. Prepare Workers ─────────────────────────────────────────────────
        //    Brings up the shared API server. Individual benchmark workers are
        //    started just-in-time inside Run Benchmarks, immediately before the
        //    engine that needs them — one at a time on both prod and develop.
        stage('Prepare Workers') {
            when {
                expression { params.SCALE_CLUSTERS }
            }
            steps {
                script {
                    def apiNs = getApiNamespace()
                    sh """
                        export NAMESPACE="${apiNs}"
                        MANIFEST=\$(envsubst < gke-manifest/opensearch-benchmark-api-server.yaml)

                        # Check if the API server is already running and the manifest is unchanged.
                        # If so, skip the bounce — restarting it would interrupt any concurrent job
                        # sharing the same benchmark-api namespace (e.g. a main run while an ACL
                        # job starts up).
                        ALREADY_AVAILABLE=false
                        if kubectl get deployment opensearch-benchmark-api-server \
                                -n ${apiNs} >/dev/null 2>&1; then
                            # Spec unchanged and already available — no restart needed.
                            if echo "\$MANIFEST" | kubectl apply --dry-run=server -f - >/dev/null 2>&1; then
                                READY=\$(kubectl get deployment opensearch-benchmark-api-server \
                                    -n ${apiNs} \
                                    -o jsonpath='{.status.availableReplicas}' 2>/dev/null || echo 0)
                                if [ "\${READY:-0}" -ge 1 ]; then
                                    echo "API server already running and manifest unchanged — skipping restart."
                                    ALREADY_AVAILABLE=true
                                fi
                            else
                                # Deployment spec.selector is immutable — delete and recreate.
                                echo "Deployment spec changed — deleting and recreating opensearch-benchmark-api-server..."
                                kubectl delete deployment opensearch-benchmark-api-server \
                                    -n ${apiNs}
                                kubectl wait --for=delete pod \
                                    -l app=opensearch-benchmark,component=api-server \
                                    -n ${apiNs} --timeout=120s || true
                            fi
                        fi

                        if [ "\$ALREADY_AVAILABLE" = "false" ]; then
                            echo "\$MANIFEST" | kubectl apply -f -
                            kubectl scale deployment opensearch-benchmark-api-server \
                                --replicas=0 -n ${apiNs}
                            kubectl wait --for=delete pod \
                                -l app=opensearch-benchmark,component=api-server \
                                -n ${apiNs} --timeout=120s || true
                            kubectl scale deployment opensearch-benchmark-api-server \
                                --replicas=1 -n ${apiNs}
                            kubectl wait --for=condition=available deployment/opensearch-benchmark-api-server \
                                -n ${apiNs} --timeout=600s
                        fi
                    """
                }
            }
        }

        // ── 3. Verify Health ───────────────────────────────────────────────────
        stage('Verify Health') {
            steps {
                script {
                    def apiNs   = getApiNamespace()
                    def engines = getEngines()
                    sh """
                        echo "=== OpenSearch Clusters ==="
                        for ns in ${engines.collect { getNamespace(it) }.join(' ')}; do
                            echo "--- \$ns ---"
                            kubectl get pods -n \$ns 2>/dev/null || echo "(not deployed)"
                        done
                        echo ""
                        echo "=== Benchmark API (${apiNs}) ==="
                        kubectl get pods -n ${apiNs}
                    """
                    sh """
                        curl -sf ${getApiUrl()}/health || \
                            (echo "WARNING: API health check failed — service may still be starting" && true)
                    """
                }
            }
        }

        // ── 4. Run Benchmarks ──────────────────────────────────────────────────
        //    Per engine, in order:
        //      a) Start worker (just-in-time — only one worker up at a time)
        //      b) Seed dataset cache onto that worker's PVC
        //      c) Scale up / deploy OpenSearch cluster
        //      d) Run benchmark pipeline
        //      e) Fetch & save results
        //
        //    PROD:    engines run in parallel (independent clusters + resource pools)
        //    DEVELOP: engines run sequentially (shared dev node)
        //    Results land in <RESULTS_DIR>/<version>/<node_size>/.
        stage('Run Benchmarks') {
            steps {
                script {
                    def apiNs          = getApiNamespace()
                    def engines        = getEngines()
                    def prodRun        = isProd()
                    def apiUrl         = getApiUrl()
                    def rawAutomationBranch = prodRun ? 'main' : (env.BRANCH_NAME ?: env.GIT_BRANCH ?: 'main')
                    def automationBranch = rawAutomationBranch.contains('/') ? rawAutomationBranch.tokenize('/').last() : rawAutomationBranch

                    // Cancel any running/queued jobs from a previous build of this same job.
                    // Each engine name is unique end-to-end (jvector, jvector-acl, faiss, lucene)
                    // so this loop is always safe to run concurrently across different pipelines.
                    sh """
                        for engine in ${engines.join(' ')}; do
                            JOBS=\$(curl -s "${apiUrl}/api/v1/benchmark?engine=\$engine" \
                                | jq -r '.jobs[] | select(.status == "running" or .status == "queued") | .job_id' 2>/dev/null || true)
                            for JOB_ID in \$JOBS; do
                                echo "Cancelling stale \$engine job: \$JOB_ID"
                                curl -s -X POST "${apiUrl}/api/v1/benchmark/\$JOB_ID/cancel?engine=\$engine" || true
                            done
                        done
                    """

                    def pipeline     = params.PIPELINE_OVERRIDE?.trim() ?: params.PIPELINE
                    def pipelineJson = readJSON file: "pipelines/${pipeline}.json"

                    def rawVersions = pipelineJson.versions   ?: [params.OPENSEARCH_VERSION]
                    def rawSizes    = pipelineJson.node_sizes ?: (pipelineJson.node_size ? [pipelineJson.node_size] : ['small'])
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

                    sh "mkdir -p ${RESULTS_DIR}"

                    def redeploy         = params.REDEPLOY_CLUSTERS || pipelineJson.redeploy == true
                    def multiRun         = (pipelineJson.versions || pipelineJson.node_sizes) ? true : false
                    def hasFirstRunSteps = pipelineJson.first_run_steps && pipelineJson.first_run_steps.size() > 0

                    // Resolve dataset cache files to seed (same logic as the old Seed Dataset Cache stage).
                    def corpusSizeVal  = pipelineJson.params?.corpus_size
                    def corpusSizes    = corpusSizeVal ? [corpusSizeVal] : ['1m', '5m']
                    def datasets       = (pipelineJson.steps ?: []).collect { it.dataset }.unique()
                    def datasetsConfig = readYaml file: 'config/datasets.yaml'
                    def filesToSeed    = []
                    datasets.each { dataset ->
                        def cacheFiles = datasetsConfig.datasets[dataset]?.gcs_cache_files ?: []
                        cacheFiles.each { entry ->
                            if (corpusSizes.contains(entry.corpus_size)) { filesToSeed << entry }
                        }
                    }
                    if (filesToSeed) {
                        echo "Files to seed per worker: ${filesToSeed.collect { it.gcs_path }.join(', ')}"
                    }

                    // Build a closure that runs all (version × size) iterations for a single engine.
                    def makeEngineClosure = { String engine ->
                        def ns = getNamespace(engine)
                        return {
                            catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {

                                // ── a) Start this engine's worker just-in-time ─────────
                                if (params.SCALE_CLUSTERS) {
                                    def workerStorageClaim = prodRun \
                                        ? "benchmark-worker-storage-${engine}" \
                                        : "benchmark-worker-storage"
                                    sh """
                                        export ENGINE="${engine}"
                                        export NAMESPACE="${apiNs}"
                                        export AUTOMATION_BRANCH="${automationBranch}"
                                        export WORKER_STORAGE_CLAIM="${workerStorageClaim}"
                                        export OS_NAMESPACE="${getNamespace(engine)}"
                                        MANIFEST=\$(envsubst < gke-manifest/opensearch-benchmark-worker-template.yaml)

                                        if kubectl get statefulset opensearch-benchmark-worker-${engine} \
                                                -n ${apiNs} >/dev/null 2>&1; then
                                            if ! echo "\$MANIFEST" | kubectl apply --dry-run=server -f - >/dev/null 2>&1; then
                                                echo "StatefulSet spec changed — deleting and recreating opensearch-benchmark-worker-${engine}..."
                                                kubectl delete statefulset opensearch-benchmark-worker-${engine} \
                                                    -n ${apiNs} --cascade=orphan
                                                kubectl wait --for=delete pod \
                                                    -l app=opensearch-benchmark,component=worker,engine=${engine} \
                                                    -n ${apiNs} --timeout=120s || true
                                            fi
                                        fi

                                        echo "\$MANIFEST" | kubectl apply -f -
                                        kubectl scale statefulset opensearch-benchmark-worker-${engine} \
                                            --replicas=0 -n ${apiNs}
                                        kubectl wait --for=delete pod \
                                            -l app=opensearch-benchmark,component=worker,engine=${engine} \
                                            -n ${apiNs} --timeout=120s || true
                                        kubectl scale statefulset opensearch-benchmark-worker-${engine} \
                                            --replicas=1 -n ${apiNs}
                                        kubectl rollout status statefulset/opensearch-benchmark-worker-${engine} \
                                            -n ${apiNs} --timeout=1200s

                                        # rollout status only means the pod is Running — the app
                                        # inside may still be starting. Poll the worker pod's own
                                        # /health endpoint directly (port 8080) until it responds,
                                        # then verify the API server can proxy to it before proceeding.
                                        WORKER_POD="opensearch-benchmark-worker-${engine}-0"
                                        WORKER_NS="${apiNs}"
                                        echo "Waiting for worker-${engine} app to be ready..."
                                        for i in \$(seq 1 60); do
                                            STATUS=\$(kubectl exec -n \$WORKER_NS \$WORKER_POD -- \
                                                curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/health \
                                                2>/dev/null || echo 000)
                                            if [ "\$STATUS" = "200" ]; then
                                                echo "  ✅ worker-${engine} app ready (attempt \$i)"
                                                break
                                            fi
                                            echo "  [attempt \$i/60] worker-${engine} not ready (HTTP \$STATUS) — retrying in 5s"
                                            sleep 5
                                        done
                                        if [ "\$STATUS" != "200" ]; then
                                            echo "❌ worker-${engine} did not become reachable after 5 minutes"
                                            exit 1
                                        fi
                                    """
                                }

                                // ── b) Seed dataset cache onto this worker's PVC ───────
                                filesToSeed.each { entry ->
                                    def gcsPath    = entry.gcs_path
                                    def targetPath = entry.target_path
                                    def fileName   = gcsPath.tokenize('/').last()
                                    sh """
                                        set -euo pipefail
                                        POD="opensearch-benchmark-worker-${engine}-0"
                                        if kubectl exec -n ${apiNs} \$POD -- test -f '${targetPath}' 2>/dev/null; then
                                            echo "[${engine}] ${fileName} already present — skipping"
                                        else
                                            echo "[${engine}] Seeding ${fileName} from GCS..."
                                            kubectl exec -n ${apiNs} \$POD -- sh -c \
                                                "mkdir -p \$(dirname '${targetPath}') && gcloud storage cp '${gcsPath}' '${targetPath}'"
                                            FINAL_SIZE=\$(kubectl exec -n ${apiNs} \$POD -- \
                                                stat -c '%s' '${targetPath}' 2>/dev/null || echo 0)
                                            echo "[${engine}] Done — \${FINAL_SIZE} bytes"
                                        fi
                                    """
                                }

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

                                    stage("${engine} / ${versionLabel} / ${runSize} — Prepare Cluster") {
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
                                                        gke-manifest/deploy-namespace-cluster.sh ${ns} ${runExtraArgs}
                                                    fi
                                                fi

                                                # Wait for manager pod
                                                kubectl rollout status statefulset/opensearch-cluster-manager \
                                                    -n ${ns} --timeout=600s

                                                # Wait for all data pods to be Running
                                                echo "Waiting for opensearch-data pods to be Running in ${ns}..."
                                                RUNNING=0
                                                LAST_PROGRESS=\$SECONDS
                                                STALL_LIMIT=900
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
                                    } // Prepare Cluster stage

                                    stage("${engine} / ${versionLabel} / ${runSize} — Run Benchmark") {
                                    try {
                                        // ── a2) ACL Setup ──────────────────────────────────
                                        // Only runs when the selected pipeline starts with "acl-".
                                        // Bootstraps the ingest pipeline, index template, DLS roles,
                                        // and benchmark users before the first benchmark step fires.
                                        // Idempotent — every call is a PUT, safe to re-run on redeploy.
                                        //
                                        // Worker readiness wait is required: cluster-green only confirms
                                        // OpenSearch is ready, not the worker pod. acl-setup.sh routes
                                        // all curl calls through kubectl exec on the worker pod — if the
                                        // pod is still starting its git pull the TCP connection to the
                                        // OpenSearch service is refused (exit 7).
                                        script {
                                            if (pipeline.startsWith('acl-')) {
                                                sh """
                                                    echo "=== [${engine}] Waiting for worker pod to be Ready before ACL setup ==="
                                                    kubectl wait --for=condition=ready pod \
                                                        opensearch-benchmark-worker-${engine}-0 \
                                                        -n ${apiNs} --timeout=300s
                                                    echo "=== [${engine}] ACL Setup for ${ns} ==="
                                                    gke-manifest/acl-setup.sh ${ns}
                                                """
                                            } else {
                                                echo "[${engine}] Non-ACL pipeline — skipping ACL setup"
                                            }
                                        }

                                        // On the first run, --first-run is passed so run-pipeline.sh
                                        // reads first_run_steps (build + search). All subsequent runs
                                        // omit the flag and use steps (search-only), reusing the PVC.
                                        // RESULTS_DEST + WORKER_POD are consumed by run-pipeline.sh's
                                        // incremental copy helper so each scenario is pulled to the
                                        // Jenkins workspace as soon as it finishes.
                                        sh """
                                            set +e
                                            DEST="${RESULTS_DIR}/${runKey}/test-runs/${engine}"
                                            mkdir -p "\$DEST"
                                            API_URL=${apiUrl} \
                                            RESULTS_DEST="\$DEST" \
                                            OS_NAMESPACE="${ns}" \
                                            API_NAMESPACE="${apiNs}" \
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

                                        // ── b2) Post-ingest ACL verification ───────────────
                                        // Only for ACL pipelines. Confirms after all benchmark
                                        // steps complete that:
                                        //   [A] index default_pipeline = acl_guard
                                        //   [B] 10 random docs have ACL fields populated
                                        //   [C] DLS is restrictive (ACL user sees fewer docs than admin)
                                        script {
                                            if (pipeline.startsWith('acl-')) {
                                                sh """
                                                    echo "=== [${engine}] Post-ingest ACL verification ==="
                                                    gke-manifest/acl-setup.sh ${ns} verify
                                                """
                                            }
                                        }
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
                                                echo "=== ${engine} @ ${versionLabel}/${runSize} (job: \$JOB_ID) ==="

                                                curl -s "${apiUrl}/api/v1/benchmark/\$JOB_ID?engine=${engine}" \
                                                    | jq '.' > ${RESULTS_DIR}/${runKey}/job-status-${engine}.json
                                                jq '{job_id, status, scenarios_completed, scenarios_total}' \
                                                    ${RESULTS_DIR}/${runKey}/job-status-${engine}.json

                                                DEST="${RESULTS_DIR}/${runKey}/test-runs/${engine}"
                                                mkdir -p "\$DEST"
                                                kubectl cp -c worker ${apiNs}/opensearch-benchmark-worker-${engine}-0:/results/\$JOB_ID/${engine}/. "\$DEST/" >/dev/null 2>&1 \
                                                    || echo "WARNING: catch-up kubectl cp failed for ${engine} — worker pod may not be running"

                                                echo "  View: ${apiUrl}/results.html?job_id=\$JOB_ID"
                                                echo "  Results dir: ${BUILD_URL}artifact/${RESULTS_DIR}/${runKey}"
                                            else
                                                echo "WARNING: no job_id for ${engine} @ ${versionLabel}/${runSize} — benchmark may not have submitted"
                                            fi

                                            # ── Worker log ─────────────────────────────────
                                            WORKER_LOG="${RESULTS_DIR}/${runKey}/server-logs/worker-${engine}.log"
                                            mkdir -p "${RESULTS_DIR}/${runKey}/server-logs"
                                            kubectl logs statefulset/opensearch-benchmark-worker-${engine} \
                                                -n ${apiNs} --tail=5000 2>&1 > "\$WORKER_LOG" || true
                                            echo "Worker log: \$WORKER_LOG (\$(wc -l < \$WORKER_LOG) lines)"
                                        """
                                    } // try/finally
                                    } // Run Benchmark stage
                                } // runs.each
                            } // catchError
                        } // closure
                    }

                    // Build the engine branch map.
                    def engineBranches = engines.collectEntries { engine ->
                        [(engine): makeEngineClosure(engine)]
                    }

                    if (currentBuild.currentResult == 'ABORTED') {
                        echo "Build aborted — skipping engine runs"
                    } else if (prodRun) {
                        // PROD: run all engines in parallel — independent clusters, maximise throughput.
                        parallel engineBranches
                    } else {
                        // DEVELOP: run engines sequentially — shared dev node, avoid contention.
                        // Scale down each engine's worker after it finishes before the next one
                        // starts, regardless of SKIP_SCALE_DOWN (which only controls what stays
                        // up after the final engine completes).
                        engines.eachWithIndex { engine, idx ->
                            engineBranches[engine].call()
                            def isLast = (idx == engines.size() - 1)
                            if (params.SCALE_CLUSTERS && !isLast) {
                                sh """
                                    kubectl scale statefulset opensearch-benchmark-worker-${engine} \
                                        --replicas=0 -n ${apiNs} || true
                                    kubectl wait --for=delete pod \
                                        -l app=opensearch-benchmark,component=worker,engine=${engine} \
                                        -n ${apiNs} --timeout=120s || true
                                """
                            }
                        }
                    }
                }
            }
        }

        // ── 6. Build Summary ───────────────────────────────────────────────────
        stage('Build Summary') {
            steps {
                script {
                    def engines  = getEngines()
                    def apiUrl   = getApiUrl()
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
  Engines (resolved): ${engines.join(', ')}
  Environment:        ${isProd() ? 'prod' : 'develop'}
  API URL:            ${apiUrl}
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
                                JOB_FILE=\$(ls job_id_\${engine}-\${version}-*.txt 2>/dev/null | head -1)
                                if [ -n "\$JOB_FILE" ] && [ -f "\$JOB_FILE" ]; then
                                    JOB_ID=\$(cat "\$JOB_FILE")
                                    echo "    \${engine}: ${apiUrl}/results.html?job_id=\${JOB_ID}" \
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
                    def resultsUrl = BUILD_URL + "execution/node/3/ws/results/" + BUILD_ID
                    println "Results workspace: ${resultsUrl}"
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
                def engines = getEngines()
                def apiNs   = getApiNamespace()
                def apiUrl  = getApiUrl()

                // Cancel any running cloud service jobs before scale-down kills the worker pods.
                if (currentBuild.currentResult in ['ABORTED', 'FAILURE']) {
                    echo "Build ${currentBuild.currentResult} — cancelling any running cloud service jobs..."

                    def apiRunning = sh(
                        script: """
                            PHASE=\$(kubectl get pods -n ${apiNs} -l app=opensearch-benchmark,component=api-server \
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
                                    curl -s -X POST "${apiUrl}/api/v1/benchmark/\$JOB_ID/cancel?engine=${engine}" || true

                                    echo "Waiting for ${engine} job \$JOB_ID to stop..."
                                    for attempt in \$(seq 1 12); do
                                        STATUS=\$(curl -s "${apiUrl}/api/v1/benchmark/\$JOB_ID?engine=${engine}" \
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
                                --replicas=0 -n ${apiNs} || true
                        """
                    }

                    // Only scale down the API server if no other worker pods are still
                    // running in the same namespace. This allows two jobs (e.g. the main
                    // pipeline and an ACL pipeline both using FORCE_PROD / benchmark-api)
                    // to run concurrently — the first to finish won't kill the API server
                    // while the second job's worker is still active.
                    def otherWorkersRunning = sh(
                        script: """
                            OTHER=\$(kubectl get pods -n ${apiNs} \
                                -l app=opensearch-benchmark,component=worker \
                                --field-selector=status.phase=Running \
                                --no-headers 2>/dev/null \
                                | grep -v -E '${engines.collect { "worker-${it}-" }.join('|')}' \
                                | wc -l)
                            echo \$OTHER
                        """,
                        returnStdout: true
                    ).trim().toInteger()

                    if (otherWorkersRunning > 0) {
                        echo "Skipping API server scale-down — ${otherWorkersRunning} other worker pod(s) still active in ${apiNs}."
                    } else {
                        echo "Scaling down API server..."
                        sh """
                            kubectl scale deployment opensearch-benchmark-api-server \
                                --replicas=0 -n ${apiNs} || true
                        """
                    }

                    echo "Scaling down OpenSearch cluster(s)..."
                    engines.each { engine ->
                        def ns = getNamespace(engine)
                        sh "gke-manifest/scale-down-clusters.sh ${ns} || true"
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

// ── Helper functions ───────────────────────────────────────────────────────────
//
// isProd()           → true when running on main, test-prod/*, or FORCE_PROD=true
// getNamespace(eng)  → os-<eng>         (prod)  or os-develop-<eng>  (develop)
// getApiNamespace()  → benchmark-api    (prod)  or benchmark-api-develop
// getApiUrl()        → resolved API URL (param > branch default)
// getEngines()       → list of engine strings derived from ENGINE_TARGET param

def isProd() {
    def rawBranch = env.BRANCH_NAME ?: env.GIT_BRANCH ?: scm.branches[0].name
    def branch = rawBranch.contains('/') ? rawBranch.tokenize('/').last() : rawBranch
    return params.FORCE_PROD || branch == 'main' || branch.startsWith('test-prod')
}

def getNamespace(String engine) {
    return "${isProd() ? 'os' : 'os-develop'}-${engine}"
}

def getApiNamespace() {
    return isProd() ? 'benchmark-api' : 'benchmark-api-develop'
}

def getApiUrl() {
    def overrideUrl = params.API_URL?.trim()
    if (overrideUrl) { return overrideUrl }
    return isProd() ? 'http://34.132.114.18' : 'http://136.116.139.175'
}

def getEngines() {
    // ACL pipelines always run on jvector-acl — ENGINE_TARGET is ignored.
    def pipeline = params.PIPELINE_OVERRIDE?.trim() ?: params.PIPELINE
    if (pipeline?.startsWith('acl-')) {
        return ['jvector-acl']
    }
    def target = params.ENGINE_TARGET?.trim() ?: 'all'
    if (target == 'all') {
        // On develop, run only jvector by default — the shared dev node handles one
        // engine at a time.
        return isProd() ? ['jvector', 'faiss', 'lucene'] : ['jvector']
    }
    return target.split(',').collect { it.trim() }.findAll { it }
}

// Made with Bob
