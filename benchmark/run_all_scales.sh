#!/bin/bash
set -e
cd "$(dirname "$0")/.."
MODEL="minimax/minimax-m2.7"
JUDGE="z-ai/glm-5"

for SCALE in small medium large; do
    echo "===== SCALE: $SCALE ====="
    WS="benchmark/benchmark_workspace_${SCALE}"
    
    # Init + index if needed
    if [ ! -d "$WS/.musedb" ]; then
        python -c "from musedb.cli import app; import sys; sys.argv=['musedb','init','$WS']; app()"
    fi
    python -c "from musedb.cli import app; import sys; sys.argv=['musedb','index','$WS','-w','$WS']; app()"
    
    # Start server
    FILEDB_BACKEND=sqlite FILEDB_MUSEDB_DIR="$WS/.musedb" python -c "import uvicorn; uvicorn.run('app.main:app', host='127.0.0.1', port=8000, log_level='warning')" &
    SERVER_PID=$!
    sleep 4
    
    # Verify
    curl -sf http://localhost:8000/files?limit=1 > /dev/null || { echo "Server failed"; kill $SERVER_PID 2>/dev/null; exit 1; }
    
    # Run benchmark with judge
    cd benchmark
    python benchmark.py --scale $SCALE --agents filedb rag --model $MODEL --runs 1 --judge --judge-model $JUDGE --url http://localhost:8000
    cd ..
    
    # Stop server
    kill $SERVER_PID 2>/dev/null
    wait $SERVER_PID 2>/dev/null || true
    sleep 2
    echo ""
done
echo "All scales complete."
