#!/bin/bash
set -e
cd "$(dirname "$0")/.."
MODEL="deepseek/deepseek-chat-v3-0324"
JUDGE="z-ai/glm-5-turbo"

for SCALE in medium large; do
    echo "===== SCALE: $SCALE ====="
    WS="benchmark/benchmark_workspace_${SCALE}"
    
    # Init + index
    if [ ! -d "$WS/.opendb" ]; then
        python -c "from opendb.cli import app; import sys; sys.argv=['opendb','init','$WS']; app()"
    fi
    python -c "from opendb.cli import app; import sys; sys.argv=['opendb','index','$WS','-w','$WS']; app()"
    
    # Start server
    FILEDB_BACKEND=sqlite FILEDB_OPENDB_DIR="$WS/.opendb" python -c "import uvicorn; uvicorn.run('app.main:app', host='127.0.0.1', port=8000, log_level='warning')" &
    SERVER_PID=$!
    sleep 4
    
    # Verify
    curl -sf http://localhost:8000/files?limit=1 > /dev/null || { echo "Server failed to start"; kill $SERVER_PID 2>/dev/null; exit 1; }
    
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
