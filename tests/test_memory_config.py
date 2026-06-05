"""Config keys for markdown memory feature."""

import os
import importlib


def _reload_config():
    import config
    importlib.reload(config)
    return config.Config


def test_defaults():
    for var in ('VAULT_PATH', 'MEMORY_ENABLED', 'SUMMARY_PROFILE',
                'SUMMARY_MAX_NOTES_PER_DAY', 'MEMORY_FREE_MODELS'):
        os.environ.pop(var, None)
    Config = _reload_config()
    assert Config.VAULT_PATH.endswith('vault')
    assert Config.MEMORY_ENABLED is False
    assert Config.SUMMARY_PROFILE == 'cheap-first'
    assert Config.SUMMARY_MAX_NOTES_PER_DAY == 200
    assert Config.MEMORY_FREE_MODELS == []


def test_env_override():
    os.environ['VAULT_PATH'] = '/tmp/test-vault'
    os.environ['MEMORY_ENABLED'] = 'true'
    os.environ['SUMMARY_PROFILE'] = 'cheap-first'
    os.environ['SUMMARY_MAX_NOTES_PER_DAY'] = '500'
    os.environ['MEMORY_FREE_MODELS'] = 'opencode::deepseek-v4-flash-free,ollama::mistral'
    Config = _reload_config()
    try:
        assert Config.VAULT_PATH == '/tmp/test-vault'
        assert Config.MEMORY_ENABLED is True
        assert Config.SUMMARY_MAX_NOTES_PER_DAY == 500
        assert Config.MEMORY_FREE_MODELS == ['opencode::deepseek-v4-flash-free', 'ollama::mistral']
    finally:
        for var in ('VAULT_PATH', 'MEMORY_ENABLED', 'SUMMARY_PROFILE',
                    'SUMMARY_MAX_NOTES_PER_DAY', 'MEMORY_FREE_MODELS'):
            os.environ.pop(var, None)
        _reload_config()
