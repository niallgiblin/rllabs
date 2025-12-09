"""
Pytest configuration and fixtures
Provides RabbitMQ port-forward setup for tests in docker-compose environment
"""

import pytest
import subprocess
import os
import time
import sys
import socket
sys.path.insert(0, os.path.dirname(__file__))
from rabbitmq_helpers import is_docker_compose, check_rabbitmq_via_docker_exec


def find_free_port():
    """Find a free port for port-forwarding"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


@pytest.fixture(scope="session", autouse=True)
def setup_rabbitmq_port_forward():
    """
    Automatically set up RabbitMQ port-forward for tests in docker-compose
    This allows tests to connect to RabbitMQ even though it's not exposed
    
    Tries to create a port-forward using socat if available, otherwise
    provides helpful error messages.
    """
    if not is_docker_compose():
        yield
        return
    
    if not check_rabbitmq_via_docker_exec():
        yield
        return
    
    if os.getenv("RABBITMQ_PORT"):
        yield
        return
    
    try:
        result = subprocess.run(
            ["docker", "compose", "port", "rabbitmq", "5672"],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0 and result.stdout.strip():
            host_port = result.stdout.strip().split(':')[-1]
            if host_port and host_port != "0":
                os.environ["RABBITMQ_PORT"] = host_port
                yield
                return
    except Exception:
        pass
    
    if subprocess.run(["which", "socat"], capture_output=True).returncode == 0:
        try:
            container_id = subprocess.run(
                ["docker", "compose", "ps", "-q", "rabbitmq"],
                capture_output=True,
                text=True,
                timeout=2
            ).stdout.strip()
            
            if container_id:
                container_ip = subprocess.run(
                    ["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", container_id],
                    capture_output=True,
                    text=True,
                    timeout=2
                ).stdout.strip()
                
                if container_ip:
                    port = find_free_port()
                    socat_process = subprocess.Popen(
                        ["socat", f"TCP-LISTEN:{port},fork,reuseaddr", f"TCP:{container_ip}:5672"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
                    time.sleep(0.5)  
                    
                    if socat_process.poll() is None: 
                        os.environ["RABBITMQ_PORT"] = str(port)
                        print(f"\nuto-created RabbitMQ port-forward: localhost:{port} -> rabbitmq:5672")
                        yield
                        socat_process.terminate()
                        socat_process.wait(timeout=2)
                        return
        except Exception:
            pass
    
    # Could not set up port-forward automatically
    # Tests will skip with helpful message
    yield


@pytest.fixture(scope="session")
def rabbitmq_connection():
    """
    Provide RabbitMQ connection for tests that need it
    Automatically handles port-forward setup
    """
    from rabbitmq_helpers import get_rabbitmq_connection_or_skip
    connection = get_rabbitmq_connection_or_skip()
    yield connection
    connection.close()
