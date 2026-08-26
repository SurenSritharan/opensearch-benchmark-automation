pipeline {
    // Main branch pipeline — targets os-<engine> clusters in benchmark-api namespace.
    // This IS the primary Jenkinsfile on the main branch.
    //
    // Prerequisites on the Jenkins node: kubectl, curl, jq, bash
    // Kubeconfig is sourced from the gke-kubeconfig Secret file credential.
    agent any
    
    parameters {
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
        SCM_BRANCH = "${scm.branches[0].name}"
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
        stage('Print Branch') {
            steps {
                echo "The configured SCM branch is: ${env.SCM_BRANCH}"
            }
        }
    }
}

// Made with Bob
