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
                'all',
                'wiki-1m',
                'wiki-5m',
                'msmarco-1m',
                'msmarco-5m',
                'search-only'
            ],
            description: 'Test pipeline to run. "all" runs both datasets at 1M + 5M. "search-only" requires indexes to already exist.'
        )
        string(
            name: 'API_URL',
            defaultValue: 'http://34.132.114.18',
            description: 'Base URL of the benchmark cloud service API'
        )
        booleanParam(
            name: 'SCALE_CLUSTERS',
            defaultValue: true,
            description: 'Scale up clusters before benchmarking and scale them down afterwards'
        )
        choice(
            name: 'SCALE_TARGET',
            choices: ['all', 'os-jvector', 'os-faiss', 'os-lucene'],
            description: 'Which cluster namespace(s) to scale up/down (only used when SCALE_CLUSTERS is true)'
        )
        booleanParam(
            name: 'REDEPLOY_CLUSTERS',
            defaultValue: false,
            description: 'Re-deploy OpenSearch clusters before benchmarking (applies OPENSEARCH_VERSION)'
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
        // ── 1. Cluster re-deploy (optional, run before scale-up) ──────────────
        stage('Re-deploy Clusters') {
            when {
                expression { params.REDEPLOY_CLUSTERS }
            }
            steps {
                container('kubectl') {
                    script {
                        def extraArgs = "--version ${params.OPENSEARCH_VERSION} --force"
                        if (params.DELETE_PVCS) {
                            extraArgs += " --delete-pvcs"
                        }

                        def target = params.SCALE_TARGET
                        if (target == 'all') {
                            echo "Re-deploying all OpenSearch clusters (version ${params.OPENSEARCH_VERSION})..."
                            sh "gke-manifest/deploy-all-clusters.sh ${extraArgs}"
                        } else {
                            echo "Re-deploying ${target} (version ${params.OPENSEARCH_VERSION})..."
                            sh "gke-manifest/deploy-namespace-cluster.sh ${target} ${extraArgs}"
                        }
                    }
                }
            }
        }

        // ── 2. Scale up clusters ───────────────────────────────────────────────
        stage('Scale Up Clusters') {
            when {
                expression { params.SCALE_CLUSTERS && !params.REDEPLOY_CLUSTERS }
            }
            steps {
                container('kubectl') {
                    script {
                        echo "Scaling up ${params.SCALE_TARGET}..."
                        sh "gke-manifest/scale-up-clusters.sh ${params.SCALE_TARGET}"
                    }
                }
            }
        }

        // ── 3. Verify cluster health ───────────────────────────────────────────
        stage('Verify Cluster Health') {
            steps {
                container('kubectl') {
                    script {
                        echo "Verifying cluster health before benchmark..."
                        sh '''
                            kubectl cluster-info
                            kubectl get nodes
                            for ns in os-jvector os-faiss os-lucene; do
                                if kubectl get namespace $ns &>/dev/null; then
                                    echo "--- $ns ---"
                                    kubectl get pods -n $ns
                                fi
                            done
                        '''
                    }
                }
            }
        }

        // ── 4. Run benchmark via cloud service ────────────────────────────────
        stage('Run Benchmark') {
            steps {
                container('curl') {
                    script {
                        def scriptMap = [
                            'all'        : 'cloud-service/scripts/test-all-complete.sh',
                            'wiki-1m'    : 'cloud-service/scripts/test-wiki-1m-complete.sh',
                            'wiki-5m'    : 'cloud-service/scripts/test-wiki-5m-complete.sh',
                            'msmarco-1m' : 'cloud-service/scripts/test-msmarco-1m-complete.sh',
                            'msmarco-5m' : 'cloud-service/scripts/test-msmarco-5m-complete.sh',
                            'search-only': 'cloud-service/scripts/test-search.sh'
                        ]

                        def script = scriptMap[params.PIPELINE]
                        if (!script) {
                            error("Unknown PIPELINE value: ${params.PIPELINE}")
                        }

                        echo "Running pipeline '${params.PIPELINE}' against ${params.API_URL} ..."
                        sh """
                            chmod +x ${script}
                            API_URL=${params.API_URL} ${script} 2>&1 | tee benchmark-run.log

                            # Capture the job_id from the log for later use
                            JOB_ID=\$(grep -oP '(?<=Job ID: )\\S+' benchmark-run.log | tail -1 || true)
                            if [ -n "\$JOB_ID" ]; then
                                echo "\$JOB_ID" > job_id.txt
                                echo "Job ID captured: \$JOB_ID"
                            fi
                        """
                    }
                }
            }
        }

        // ── 5. Fetch & save results ────────────────────────────────────────────
        stage('Fetch Results') {
            steps {
                container('curl') {
                    script {
                        echo "Fetching final results from cloud service..."
                        sh """
                            mkdir -p ${RESULTS_DIR}
                            cp benchmark-run.log ${RESULTS_DIR}/benchmark-run.log

                            if [ -f job_id.txt ]; then
                                JOB_ID=\$(cat job_id.txt)
                                echo "Fetching results for job: \$JOB_ID"

                                # Job status summary
                                curl -s "${params.API_URL}/api/v1/benchmark/\$JOB_ID" \
                                    | jq '.' > ${RESULTS_DIR}/job-status.json

                                # Full sweep results
                                curl -s "${params.API_URL}/api/v1/benchmark/\$JOB_ID/results" \
                                    | jq '.' > ${RESULTS_DIR}/results.json

                                echo "Results saved to ${RESULTS_DIR}/"
                                echo ""
                                echo "Summary:"
                                jq '{job_id, status, scenarios_completed, scenarios_total}' \
                                    ${RESULTS_DIR}/job-status.json

                                echo ""
                                echo "View results at: ${params.API_URL}/results.html?job_id=\$JOB_ID"
                            else
                                echo "WARNING: No job_id.txt found — results may be embedded in the log above."
                            fi

                            # Write build summary
                            cat > ${RESULTS_DIR}/BUILD_SUMMARY.txt << EOF
========================================
OpenSearch Benchmark Build Summary
========================================
Build ID:  ${BUILD_ID}
Build URL: ${BUILD_URL}
Date:      \$(date -u +"%Y-%m-%d %H:%M:%S UTC")

Parameters:
  Pipeline:           ${params.PIPELINE}
  API URL:            ${params.API_URL}
  Scale Clusters:     ${params.SCALE_CLUSTERS}
  Scale Target:       ${params.SCALE_TARGET}
  Redeploy Clusters:  ${params.REDEPLOY_CLUSTERS}
  OpenSearch Version: ${params.OPENSEARCH_VERSION}

Results Directory: ${RESULTS_DIR}
========================================
EOF
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
                // Scale down clusters after the run (even on failure)
                if (params.SCALE_CLUSTERS) {
                    container('kubectl') {
                        echo "Scaling down ${params.SCALE_TARGET} to conserve resources..."
                        sh "gke-manifest/scale-down-clusters.sh ${params.SCALE_TARGET} || true"
                    }
                }

                // Print final summary if it was written
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
