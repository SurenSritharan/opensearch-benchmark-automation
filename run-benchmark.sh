#!/usr/bin/env bash
# run-benchmark.sh

# 1. Only create the virtual environment if it doesn't exist yet
if [ ! -d "venv" ]; then
    echo "📦 Creating isolated virtual environment..."
    python3 -m venv venv
fi

# 2. Activate it for the current terminal session bubble
source venv/bin/activate

# 3. Ensure project dependencies are met (quietly skips if already installed)
echo "⚙️  Verifying Python dependencies..."
pip install -r requirements.txt --quiet

# 4. Hand off all arguments seamlessly straight to your core Python orchestrator
python3 run_benchmark.py "$@"