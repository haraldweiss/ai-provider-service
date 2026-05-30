"""Lazy initialization system — initialize providers only on first use."""

import logging
from functools import wraps
from threading import Lock
from typing import Callable, Any, Dict

logger = logging.getLogger(__name__)

# Global state for lazy initialization
_init_locks: Dict[str, Lock] = {}
_initialized: set = set()


def lazy_init(component_id: str):
    """
    Decorator for initialization functions.
    
    Ensures a component initializes exactly once, on first use,
    with thread-safe locking.
    
    Usage:
    @lazy_init('ollama')
    def init_ollama_client():
        global OLLAMA_CLIENT
        logger.info("Initializing Ollama client...")
        OLLAMA_CLIENT = OllamaClient(...)
        # Perform health checks, warmup, etc.
    
    # First call to init_ollama_client() runs the function
    # Subsequent calls are no-ops
    """
    def decorator(init_func: Callable) -> Callable:
        @wraps(init_func)
        def wrapper(*args, **kwargs) -> Any:
            # Early return if already initialized
            if component_id in _initialized:
                logger.debug(f"Component {component_id} already initialized")
                return None
            
            # Get or create lock for this component
            if component_id not in _init_locks:
                _init_locks[component_id] = Lock()
            
            # Double-check locking pattern (thread-safe)
            with _init_locks[component_id]:
                if component_id in _initialized:
                    # Another thread already initialized while we waited
                    return None
                
                logger.info(f"Lazy-initializing component: {component_id}")
                try:
                    result = init_func(*args, **kwargs)
                    _initialized.add(component_id)
                    logger.info(f"Component {component_id} initialized successfully")
                    return result
                except Exception as e:
                    logger.error(f"Failed to initialize {component_id}: {e}")
                    raise
        
        return wrapper
    
    return decorator


def mark_initialized(component_id: str):
    """Manually mark a component as initialized (used in tests or special cases)."""
    _initialized.add(component_id)
    logger.debug(f"Manually marked {component_id} as initialized")


def is_initialized(component_id: str) -> bool:
    """Check if a component is already initialized."""
    return component_id in _initialized


def get_initialized() -> set:
    """Get set of all initialized components."""
    return _initialized.copy()


def clear_initialization():
    """Reset all initialization state (mainly for testing)."""
    _initialized.clear()
    logger.warning("Cleared all initialization state")


# Example usage patterns (for documentation):
"""
# Pattern 1: Initialize on demand in a function
@lazy_init('anthropic')
def init_claude_client():
    from anthropic import Anthropic
    global claude_client
    claude_client = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
    logger.info("Claude client initialized")

def get_claude_client():
    init_claude_client()  # First call initializes, subsequent calls are no-ops
    return claude_client


# Pattern 2: Initialize in a provider's __init__
class OllamaClient:
    def __init__(self, config):
        self._init_lazy_components()
        self.config = config
    
    @lazy_init('ollama_health_monitor')
    def _init_lazy_components(self):
        # Check Ollama connectivity
        # Start background health monitoring
        # Preload model metadata
        pass


# Pattern 3: Initialize complex subsystems
@lazy_init('database')
def init_database():
    db.init_app(app)
    with app.app_context():
        db.create_all()
    logger.info("Database initialized")

@lazy_init('redis')
def init_redis():
    redis_client = redis.Redis(host='localhost', port=6379)
    redis_client.ping()
    logger.info("Redis initialized")

# Usage:
def get_database():
    init_database()
    return db

def get_cache():
    init_redis()
    return redis_client
"""
