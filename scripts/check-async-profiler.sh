#!/bin/bash
# Script to check if async-profiler is installed in OpenSearch pods

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "🔍 Checking async-profiler installation in OpenSearch pods..."
echo ""

# Check for namespace argument
if [ -z "$1" ]; then
    echo -e "${YELLOW}Usage: $0 <namespace>${NC}"
    echo "Example: $0 os-jvector"
    exit 1
fi

NAMESPACE=$1

# Get pod label selector from cluster.yaml
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../config/cluster.yaml"

if [ ! -f "$CONFIG_FILE" ]; then
    echo -e "${RED}❌ Config file not found: $CONFIG_FILE${NC}"
    exit 1
fi

# Extract pod_label_selector from YAML (simple grep approach)
POD_SELECTOR=$(grep "pod_label_selector:" "$CONFIG_FILE" | sed 's/.*pod_label_selector: *"\(.*\)".*/\1/' | tr -d '"')

if [ -z "$POD_SELECTOR" ]; then
    echo -e "${YELLOW}⚠️  Could not find pod_label_selector in $CONFIG_FILE${NC}"
    echo -e "${YELLOW}   Using default: app=opensearch-data${NC}"
    POD_SELECTOR="app=opensearch-data"
fi

echo "Using pod selector: $POD_SELECTOR"
echo ""

# Find OpenSearch pods using the configured selector
PODS=$(kubectl get pods -n "$NAMESPACE" -l "$POD_SELECTOR" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)

if [ -z "$PODS" ]; then
    echo -e "${RED}❌ No OpenSearch pods found in namespace $NAMESPACE${NC}"
    exit 1
fi

echo -e "${GREEN}Found OpenSearch pods:${NC}"
for pod in $PODS; do
    echo "  - $pod"
done
echo ""

# Check each pod for async-profiler
ALL_INSTALLED=true
for pod in $PODS; do
    echo "Checking pod: $pod"
    
    # Check if async-profiler binary exists
    if kubectl exec -n "$NAMESPACE" "$pod" -c opensearch -- test -f /opt/async-profiler/bin/asprof 2>/dev/null; then
        echo -e "  ${GREEN}✅ async-profiler found at /opt/async-profiler/bin/asprof${NC}"
        
        # Try to get version
        VERSION=$(kubectl exec -n "$NAMESPACE" "$pod" -c opensearch -- /opt/async-profiler/bin/asprof --version 2>/dev/null || echo "unknown")
        echo -e "  ${GREEN}   Version: $VERSION${NC}"
    else
        echo -e "  ${RED}❌ async-profiler NOT found at /opt/async-profiler/bin/asprof${NC}"
        ALL_INSTALLED=false
    fi
    
    # Check OpenSearch process PID
    PID=$(kubectl exec -n "$NAMESPACE" "$pod" -c opensearch -- pgrep -f "org.opensearch.bootstrap.OpenSearch" 2>/dev/null || echo "")
    if [ -n "$PID" ]; then
        echo -e "  ${GREEN}✅ OpenSearch process found at PID: $PID${NC}"
    else
        echo -e "  ${YELLOW}⚠️  Could not find OpenSearch process${NC}"
    fi
    
    echo ""
done

if [ "$ALL_INSTALLED" = true ]; then
    echo -e "${GREEN}✅ All pods have async-profiler installed!${NC}"
    echo ""
    echo "You can now run profiling with the benchmark automation."
else
    echo -e "${RED}❌ Some pods are missing async-profiler${NC}"
    echo ""
    echo -e "${YELLOW}📝 To install async-profiler in your OpenSearch pods:${NC}"
    echo ""
    echo "1. Add async-profiler to your OpenSearch Docker image:"
    echo "   - Download from: https://github.com/async-profiler/async-profiler/releases"
    echo "   - Extract to /opt/async-profiler in the container"
    echo ""
    echo "2. Or install it at runtime (temporary, lost on pod restart):"
    echo "   For each pod, run:"
    echo "   kubectl exec -n $NAMESPACE <pod-name> -c opensearch -- bash -c \\"
    echo "     'cd /tmp && \\"
    echo "      wget https://github.com/async-profiler/async-profiler/releases/download/v3.0/async-profiler-3.0-linux-x64.tar.gz && \\"
    echo "      tar -xzf async-profiler-3.0-linux-x64.tar.gz && \\"
    echo "      mkdir -p /opt/async-profiler && \\"
    echo "      mv async-profiler-3.0-linux-x64/* /opt/async-profiler/ && \\"
    echo "      rm -rf async-profiler-3.0-linux-x64*'"
    echo ""
    echo "3. Recommended: Build a custom OpenSearch image with async-profiler pre-installed"
    echo "   Add to your Dockerfile:"
    echo "   RUN wget https://github.com/async-profiler/async-profiler/releases/download/v3.0/async-profiler-3.0-linux-x64.tar.gz && \\"
    echo "       tar -xzf async-profiler-3.0-linux-x64.tar.gz && \\"
    echo "       mkdir -p /opt/async-profiler && \\"
    echo "       mv async-profiler-3.0-linux-x64/* /opt/async-profiler/ && \\"
    echo "       rm -rf async-profiler-3.0-linux-x64.tar.gz"
fi

# Made with Bob
