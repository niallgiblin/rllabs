"""
Helper functions for RabbitMQ connectivity in tests
Handles both docker-compose (services not exposed) and kubernetes (port-forward) scenarios

RabbitMQ tests need to either:
1. Use a temporary port-forward (handled by pytest fixture)
2. Skip with helpful message
"""

import os
import subprocess
import pika
from typing import Optional, Tuple


def is_docker_compose() -> bool:
    """Check if running in docker-compose environment"""
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0 and len(result.stdout.strip()) > 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def check_rabbitmq_via_docker_exec() -> bool:
    """Check RabbitMQ connectivity via docker compose exec"""
    try:
        result = subprocess.run(
            ["docker", "compose", "exec", "-T", "rabbitmq", "rabbitmq-diagnostics", "ping"],
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def setup_rabbitmq_port_forward() -> Optional[int]:
    """
    Set up a temporary port-forward for RabbitMQ in docker-compose
    Returns the host port, or None if failed
    """
    if not is_docker_compose():
        return None
    
    try:
        result = subprocess.run(
            ["docker", "compose", "port", "rabbitmq", "5672"],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0 and result.stdout.strip():
            host_port = int(result.stdout.strip().split(':')[-1])
            return host_port
    except Exception:
        pass
    
    # Try to create port-forward using socat or similar
    # For now, return None - tests will need to handle this
    return None


def create_rabbitmq_connection(host: Optional[str] = None, port: Optional[int] = None) -> Optional[pika.BlockingConnection]:
    """
    Create a RabbitMQ connection
    
    Args:
        host: Optional host (default: localhost or from env)
        port: Optional port (default: 5672 or from env)
        
    Returns:
        pika.BlockingConnection if successful, None otherwise
    """
    if host is None:
        host = os.getenv("RABBITMQ_HOST", "localhost")
    if port is None:
        port = int(os.getenv("RABBITMQ_PORT", "5672"))
    
    try:
        credentials = pika.PlainCredentials("admin", "admin_password")
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=host,
                port=port,
                credentials=credentials,
                connection_attempts=3,
                retry_delay=1,
                socket_timeout=5
            )
        )
        return connection
    except Exception:
        return None


def get_rabbitmq_connection_or_skip(host: Optional[str] = None, port: Optional[int] = None):
    """
    Get RabbitMQ connection or skip test with helpful message
    
    Tries multiple methods:
    1. Direct connection to host:port (or from env vars)
    2. If in docker-compose, tries to use port-forward if available
    3. If all fail, skips with helpful message
    
    Use this in tests:
        connection = get_rabbitmq_connection_or_skip()
    
    To enable tests in docker-compose:
        export RABBITMQ_PORT=<forwarded_port>
        Or run: ./tests/setup_rabbitmq_portforward.sh
    """
    import pytest
    
    env_host = os.getenv("RABBITMQ_HOST", host)
    env_port = os.getenv("RABBITMQ_PORT")
    if env_port:
        try:
            env_port = int(env_port)
            connection = create_rabbitmq_connection(env_host or "localhost", env_port)
            if connection:
                return connection
        except (ValueError, TypeError):
            pass
    
    connection = create_rabbitmq_connection(host, port)
    if connection:
        return connection
    
    if is_docker_compose():
        if check_rabbitmq_via_docker_exec():
            try:
                port_info = subprocess.run(
                    ["docker", "compose", "port", "rabbitmq", "5672"],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                if port_info.returncode == 0 and port_info.stdout.strip():
                    host_port = int(port_info.stdout.strip().split(':')[-1])
                    connection = create_rabbitmq_connection("localhost", host_port)
                    if connection:
                        return connection
            except Exception:
                pass
            
            pytest.skip(
                "RabbitMQ is running but not exposed for security. "
                "To run RabbitMQ tests:\n"
                "  1. Run: ./tests/setup_rabbitmq_portforward.sh\n"
                "  2. Or manually: export RABBITMQ_PORT=<forwarded_port>\n"
                "  3. Or temporarily expose in docker-compose.yml for testing"
            )
        else:
            pytest.skip("RabbitMQ is not running or not accessible via docker compose exec")
    else:
        pytest.skip(
            "RabbitMQ not available. "
            "For kubernetes, set up port-forward: kubectl port-forward svc/rabbitmq 5672:5672"
        )
