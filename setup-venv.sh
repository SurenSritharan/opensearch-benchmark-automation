#!/usr/bin/env bash
# setup-venv.sh - One-time virtual environment setup

set -e

echo "🔧 Setting up Python virtual environment..."

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
    echo "✅ Virtual environment created"
else
    echo "✅ Virtual environment already exists"
fi

# Activate virtual environment temporarily for setup
source venv/bin/activate

# Install dependencies
echo "📥 Installing dependencies..."
pip install -r requirements.txt

echo ""
echo "✅ Setup complete!"
echo ""
echo "The virtual environment is ready. You can now run:"
echo "  ./run-benchmark-parallel.sh    # Runs parallel benchmarks"
echo "  ./run-benchmark.sh             # Runs sequential benchmarks"
echo "  ./view_logs.py                 # Views benchmark logs"
echo ""
echo "All scripts will automatically use the virtual environment."

# Made with Bob
