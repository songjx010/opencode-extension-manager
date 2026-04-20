#!/usr/bin/env bash
set -euo pipefail

echo "=== opencode-extension-manager — Environment Bootstrap ==="

# Check Python version
PYTHON_CMD=""
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
else
    echo "ERROR: Python 3.8+ not found. Please install Python 3.8 or later."
    exit 1
fi

PY_VERSION=$($PYTHON_CMD -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$($PYTHON_CMD -c "import sys; print(sys.version_info.major)")
PY_MINOR=$($PYTHON_CMD -c "import sys; print(sys.version_info.minor)")

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 8 ]); then
    echo "ERROR: Python 3.8+ required, got $PY_VERSION"
    exit 1
fi
echo "OK: Python $PY_VERSION found ($PYTHON_CMD)"

# Create virtual environment if not exists
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    if $PYTHON_CMD -m venv .venv 2>/dev/null; then
        echo "OK: .venv created"
    else
        echo "WARNING: venv creation failed (python3-venv may not be installed)"
        echo "Attempting system-wide install..."
        if $PYTHON_CMD -m pip install pytest pytest-cov mutmut --quiet 2>/dev/null; then
            echo "OK: Test dependencies installed system-wide"
        else
            echo "ERROR: Cannot install dependencies. Please install manually:"
            echo "  sudo apt install python3-pip python3-venv"
            echo "  python3 -m venv .venv"
            echo "  source .venv/bin/activate"
            echo "  pip install pytest pytest-cov mutmut"
            exit 1
        fi
        # Skip venv activation
        VENV_SKIPPED=1
    fi
else
    echo "OK: .venv already exists"
fi

if [ "${VENV_SKIPPED:-0}" != "1" ]; then
    source .venv/bin/activate
    pip install --upgrade pip --quiet
    echo "Installing test dependencies..."
    pip install pytest pytest-cov mutmut --quiet
    echo "OK: Test dependencies installed"
fi

# Verify tools
echo ""
echo "=== Verification ==="
echo "Python: $($PYTHON_CMD --version)"
$PYTHON_CMD -m pytest --version 2>/dev/null && echo "pytest: OK" || echo "pytest: NOT INSTALLED"
$PYTHON_CMD -m mutmut --version 2>/dev/null && echo "mutmut: OK" || echo "mutmut: NOT INSTALLED"
echo ""

# Check dialog
if command -v dialog &>/dev/null; then
    echo "OK: dialog found"
else
    echo "WARNING: dialog not found. Install with: sudo apt-get install dialog (Debian/Ubuntu) or sudo yum install dialog (RHEL/CentOS)"
fi

# Create tests directory
mkdir -p tests

echo ""
echo "=== Setup Complete ==="
echo "Activate: source .venv/bin/activate"
echo "Run tests: pytest tests/ -v"
