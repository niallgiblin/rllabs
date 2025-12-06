#!/bin/bash
# Helper script to set up RabbitMQ port-forward for testing
# This allows tests to connect to RabbitMQ even though it's not exposed

set -e

echo "🔌 Setting up RabbitMQ port-forward for tests..."

# Check if docker-compose is running
if ! docker compose ps rabbitmq > /dev/null 2>&1; then
    echo "❌ RabbitMQ service is not running"
    echo "   Start services with: docker compose up -d"
    exit 1
fi

# Check if port-forward already exists
EXISTING_PORT=$(docker compose port rabbitmq 5672 2>/dev/null | cut -d: -f2 || echo "")

if [ -n "$EXISTING_PORT" ]; then
    echo "✅ Port-forward already exists on port $EXISTING_PORT"
    echo "   Set RABBITMQ_PORT=$EXISTING_PORT in your environment"
    export RABBITMQ_PORT=$EXISTING_PORT
    echo "   Or run: export RABBITMQ_PORT=$EXISTING_PORT"
    exit 0
fi

# Try to create port-forward using socat (if available)
if command -v socat > /dev/null 2>&1; then
    echo "📡 Creating port-forward using socat..."
    # Find an available port
    PORT=$(python3 -c "import socket; s=socket.socket(); s.bind(('', 0)); print(s.getsockname()[1]); s.close()" 2>/dev/null || echo "56720")
    
    # Start socat in background
    CONTAINER_IP=$(docker compose exec -T rabbitmq hostname -i 2>/dev/null | tr -d ' \n' || echo "")
    if [ -z "$CONTAINER_IP" ]; then
        CONTAINER_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' $(docker compose ps -q rabbitmq) 2>/dev/null || echo "")
    fi
    
    if [ -n "$CONTAINER_IP" ]; then
        socat TCP-LISTEN:$PORT,fork,reuseaddr TCP:$CONTAINER_IP:5672 > /tmp/socat-rabbitmq.log 2>&1 &
        SOCAT_PID=$!
        echo $SOCAT_PID > /tmp/socat-rabbitmq.pid
        sleep 1
        
        if kill -0 $SOCAT_PID 2>/dev/null; then
            echo "✅ Port-forward created: localhost:$PORT -> rabbitmq:5672"
            echo "   Set RABBITMQ_PORT=$PORT in your environment"
            export RABBITMQ_PORT=$PORT
            echo "   Or run: export RABBITMQ_PORT=$PORT"
            echo ""
            echo "   To stop: kill $SOCAT_PID"
            exit 0
        fi
    fi
fi

# Fallback: provide instructions
echo "⚠️  Could not automatically create port-forward"
echo ""
echo "💡 Manual setup options:"
echo ""
echo "Option 1: Use docker compose port (if supported)"
echo "   docker compose port rabbitmq 5672"
echo "   Then set RABBITMQ_PORT to the returned port"
echo ""
echo "Option 2: Use socat (install with: brew install socat)"
echo "   CONTAINER_IP=\$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' \$(docker compose ps -q rabbitmq))"
echo "   socat TCP-LISTEN:56720,fork,reuseaddr TCP:\$CONTAINER_IP:5672 &"
echo "   export RABBITMQ_PORT=56720"
echo ""
echo "Option 3: Temporarily expose RabbitMQ in docker-compose.yml"
echo "   Add 'ports: - \"5672:5672\"' to rabbitmq service (for testing only)"
exit 1

