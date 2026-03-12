	#!/usr/bin/env bash
    
    set -e
    
    REPO_DIR="/home/dr/Schreibtisch/FlashingGui"
    VENV_DIR="$REPO_DIR/venv"
    PYTHON_SCRIPT="$REPO_DIR/main.py"
    BRANCH="claude/stm32-flashing-gui-8w9Wy"
    
    cd "$REPO_DIR"
    
    echo "Hole neuesten Stand aus dem Repository..."
    git checkout "$BRANCH"
    git pull origin "$BRANCH"
    
    echo "Aktiviere virtuelles Environment..."
    source "$VENV_DIR/bin/activate"
    
    echo "Starte Python-Anwendung..."
    python "$PYTHON_SCRIPT"