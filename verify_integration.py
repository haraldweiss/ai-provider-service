#!/usr/bin/env python3
"""
Integration Verification Script
Run this in a repo that's integrating with the centralized AI provider service.
It verifies all components are working correctly.

Usage:
    python verify_integration.py

Or with explicit settings:
    AI_PROVIDER_SERVICE_URL=http://localhost:8767 \
    AI_PROVIDER_SERVICE_TOKEN=test-token \
    python verify_integration.py
"""

import os
import sys
import json
import requests
from typing import Tuple, Optional


class Colors:
    """ANSI color codes for terminal output."""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


def print_header(text: str) -> None:
    """Print a section header."""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'=' * 60}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{text}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'=' * 60}{Colors.RESET}\n")


def print_success(text: str) -> None:
    """Print a success message."""
    print(f"{Colors.GREEN}✓ {text}{Colors.RESET}")


def print_error(text: str) -> None:
    """Print an error message."""
    print(f"{Colors.RED}✗ {text}{Colors.RESET}")


def print_warning(text: str) -> None:
    """Print a warning message."""
    print(f"{Colors.YELLOW}⚠ {text}{Colors.RESET}")


def print_info(text: str) -> None:
    """Print an info message."""
    print(f"{Colors.BLUE}ℹ {text}{Colors.RESET}")


def check_environment() -> Tuple[bool, Optional[str], Optional[str]]:
    """Check environment variables are set."""
    print_header("Step 1: Environment Configuration")

    service_url = os.getenv('AI_PROVIDER_SERVICE_URL', 'http://localhost:8767')
    token = os.getenv('AI_PROVIDER_SERVICE_TOKEN')

    print(f"Service URL: {Colors.BOLD}{service_url}{Colors.RESET}")

    if token:
        print(f"Token: {Colors.BOLD}{token[:10]}...{Colors.RESET}")
        print_success("AI_PROVIDER_SERVICE_TOKEN is set")
    else:
        print_warning("AI_PROVIDER_SERVICE_TOKEN not set (trying to connect anyway...)")
        token = ""

    return True, service_url, token


def check_service_connectivity(service_url: str, token: str) -> bool:
    """Check if the service is running and accessible."""
    print_header("Step 2: Service Connectivity")

    try:
        print_info(f"Connecting to {service_url}/health...")
        response = requests.get(f"{service_url}/health", timeout=5)

        if response.status_code == 200:
            print_success(f"Service is running (HTTP {response.status_code})")
            health_data = response.json()
            print_info(f"Health status: {health_data.get('status', 'unknown')}")
            return True
        else:
            print_error(f"Service returned HTTP {response.status_code}")
            return False

    except requests.exceptions.ConnectionError:
        print_error(f"Cannot connect to {service_url}")
        print_warning("Make sure ai-provider-service is running:")
        print_warning("  cd ~/projects/ai-provider-service")
        print_warning("  STARTUP_MODE=lazy python app.py")
        return False
    except requests.exceptions.Timeout:
        print_error(f"Connection to {service_url} timed out")
        return False
    except Exception as e:
        print_error(f"Error: {str(e)}")
        return False


def check_authentication(service_url: str, token: str) -> bool:
    """Check if authentication works."""
    print_header("Step 3: Authentication")

    if not token:
        print_warning("No token provided. Checking if service requires auth...")
        headers = {}
    else:
        headers = {"Authorization": f"Bearer {token}"}

    try:
        response = requests.get(
            f"{service_url}/models/status",
            headers=headers,
            timeout=5
        )

        if response.status_code == 200:
            print_success("Authentication successful")
            return True
        elif response.status_code == 401:
            print_error("Authentication failed (401 Unauthorized)")
            print_warning("Token may be incorrect or not set")
            print_info(f"Verify token matches SERVICE_TOKEN on the service")
            return False
        else:
            print_error(f"Unexpected response: HTTP {response.status_code}")
            print_info(f"Response: {response.text[:200]}")
            return False

    except Exception as e:
        print_error(f"Error: {str(e)}")
        return False


def check_models_endpoint(service_url: str, token: str) -> bool:
    """Check if the models endpoint works."""
    print_header("Step 4: Models Endpoint")

    headers = {"Authorization": f"Bearer {token}"} if token else {}

    try:
        response = requests.get(
            f"{service_url}/models/status",
            headers=headers,
            timeout=5
        )

        if response.status_code != 200:
            print_error(f"Models endpoint returned HTTP {response.status_code}")
            return False

        data = response.json()

        print_success("Models endpoint is working")
        print_info(f"Loaded models: {data.get('loaded', [])}")
        print_info(f"Model count: {data.get('count', 0)}")
        print_info(f"VRAM usage: {data.get('utilization_pct', 0):.1f}%")

        if 'hardware' in data:
            hw = data['hardware']
            print_info(f"GPU VRAM: {hw.get('gpu_vram_mb', 0) / 1024:.1f} GB")
            print_info(f"System RAM: {hw.get('system_ram_mb', 0) / 1024:.1f} GB")
            print_info(f"CPU Cores: {hw.get('cpu_cores', 0)}")

        return True

    except Exception as e:
        print_error(f"Error: {str(e)}")
        return False


def check_chat_endpoint(service_url: str, token: str) -> bool:
    """Check if the chat endpoint works."""
    print_header("Step 5: Chat Endpoint")

    headers = {
        "Authorization": f"Bearer {token}" if token else "",
        "Content-Type": "application/json"
    }

    payload = {
        "provider": "claude",
        "model": "claude-3-5-haiku-20241022",
        "messages": [{"role": "user", "content": "Say hello in one word"}],
        "max_tokens": 20
    }

    try:
        print_info("Sending test chat request...")
        response = requests.post(
            f"{service_url}/chat",
            json=payload,
            headers=headers,
            timeout=60
        )

        if response.status_code != 200:
            print_error(f"Chat endpoint returned HTTP {response.status_code}")
            print_info(f"Response: {response.text[:200]}")
            return False

        data = response.json()
        print_success("Chat request successful")

        if 'result' in data and 'content' in data['result']:
            text = data['result']['content'][0].get('text', '')
            print_info(f"Response: {text[:100]}")

        print_info(f"Provider used: {data.get('via', 'unknown')}")
        print_info(f"Fallback: {data.get('fallback_used', False)}")

        return True

    except Exception as e:
        print_error(f"Error: {str(e)}")
        return False


def test_python_client(service_url: str, token: str) -> bool:
    """Test the Python client library."""
    print_header("Step 6: Python Client Library")

    # Try to use the client library if available
    try:
        # First check if it's in sys.path
        import_path = os.path.join(
            os.path.dirname(__file__),
            'client_library'
        )

        if import_path not in sys.path:
            sys.path.insert(0, import_path)

        # Try importing the client
        try:
            # Check if there's a __init__.py
            init_file = os.path.join(import_path, '__init__.py')
            if not os.path.exists(init_file):
                print_warning("client_library/__init__.py not found, skipping Python client test")
                return True

            # Try to import (this may fail if dependencies missing)
            from python_client import AIProviderClient

            print_success("AIProviderClient imported successfully")

            # Try to instantiate
            client = AIProviderClient(
                service_url=service_url,
                token=token
            )
            print_success("AIProviderClient instantiated")

            # Try to get status
            status = client.get_status()
            print_success("client.get_status() works")
            print_info(f"Status keys: {list(status.keys())}")

            return True

        except ImportError as e:
            print_warning(f"Cannot import client library: {str(e)}")
            print_info("This is OK if client_library/ not set up yet")
            return True

    except Exception as e:
        print_warning(f"Error testing Python client: {str(e)}")
        return True  # Don't fail, this is optional


def run_all_checks() -> bool:
    """Run all verification checks."""
    print(f"\n{Colors.BOLD}{Colors.BLUE}")
    print("╔════════════════════════════════════════════════════════════╗")
    print("║  AI Provider Service Integration Verification              ║")
    print("╚════════════════════════════════════════════════════════════╝")
    print(Colors.RESET)

    # Step 1: Environment
    ok, service_url, token = check_environment()
    if not ok:
        print_error("Environment check failed")
        return False

    # Step 2: Connectivity
    if not check_service_connectivity(service_url, token):
        print_error("Service connectivity check failed")
        return False

    # Step 3: Authentication
    if not check_authentication(service_url, token):
        print_error("Authentication check failed")
        return False

    # Step 4: Models endpoint
    if not check_models_endpoint(service_url, token):
        print_error("Models endpoint check failed")
        return False

    # Step 5: Chat endpoint
    if not check_chat_endpoint(service_url, token):
        print_error("Chat endpoint check failed")
        return False

    # Step 6: Python client (optional)
    test_python_client(service_url, token)

    return True


def print_next_steps() -> None:
    """Print next steps for integration."""
    print_header("Next Steps")

    print("✓ All verification checks passed!\n")
    print("Your repo can now integrate with the centralized AI provider service.\n")
    print("To integrate:")
    print("  1. Copy client library templates from INTEGRATION_TEMPLATES.md")
    print("  2. Add AI_PROVIDER_SERVICE_URL and AI_PROVIDER_SERVICE_TOKEN to .env")
    print("  3. Replace direct SDK calls with AIProviderClient")
    print("  4. Remove anthropic/openai from requirements.txt")
    print("  5. Test your integration\n")
    print("See MIGRATION.md for detailed instructions.")


if __name__ == '__main__':
    try:
        success = run_all_checks()

        if success:
            print_success("\n✓ All checks passed!")
            print_next_steps()
            sys.exit(0)
        else:
            print_error("\n✗ Some checks failed. See above for details.")
            print_warning("Make sure ai-provider-service is running:")
            print_warning("  cd ~/projects/ai-provider-service")
            print_warning("  STARTUP_MODE=lazy python app.py")
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n\nVerification cancelled by user")
        sys.exit(1)
    except Exception as e:
        print_error(f"\n\nUnexpected error: {str(e)}")
        sys.exit(1)
