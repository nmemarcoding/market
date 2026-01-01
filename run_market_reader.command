#!/bin/bash

# Change to script directory
cd "$(dirname "$0")"

# Use the system python3
PYTHON=python3

# Create virtual env if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    $PYTHON -m venv .venv
    source .venv/bin/activate
    echo "Installing Python dependencies..."
    pip install --upgrade pip
    pip install -r requirements.txt
else
    source .venv/bin/activate
fi

# Check for tesseract
if ! command -v tesseract >/dev/null 2>&1; then
    echo ""
    echo "⚠️  Tesseract OCR is not installed."
    echo "Please install it first, for example:"
    echo "  - Using Homebrew:  brew install tesseract"
    echo "  - Or download installer from: https://github.com/tesseract-ocr/tesseract"
    echo ""
    read -p "Press Enter to exit..."
    exit 1
fi

# Run the app
$PYTHON live_number_reader.py
