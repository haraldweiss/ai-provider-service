"""Pytest configuration — ensures the project root is in sys.path."""
import sys
from pathlib import Path

# Add the project root to sys.path so we can import agents, app, dispatcher, etc.
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
