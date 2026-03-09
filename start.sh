#!/bin/bash

# Start the Disposable Email Service with Forwarding

echo "Starting Disposable Email Service..."
echo ""

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is required but not installed."
    exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install -q -r requirements.txt

# Start the service
echo ""
echo "============================================"
echo "Disposable Email Service is starting..."
echo "============================================"
echo ""

python main.py

# Deactivate on exit
deactivate