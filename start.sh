#!/bin/bash

# Start the Disposable Email Service with Forwarding
# Uses uv (https://github.com/astral-sh/uv) for fast package management

echo "Starting Disposable Email Service..."
echo ""

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "Error: uv is required but not installed."
    echo "Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment with uv..."
    uv venv
fi

# Install dependencies
echo "Installing dependencies with uv..."
uv pip install -r requirements.txt

# Start the service
echo ""
echo "============================================"
echo "Disposable Email Service is starting..."
echo "============================================"
echo ""

uv run python main.py
