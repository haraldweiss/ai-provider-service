"""flask vault-render CLI — full rebuild and stale-check."""

import pytest
from config import Config


@pytest.fixture
def vault_dir(tmp_path, monkeypatch):
    p = tmp_path / 'vault'
    p.mkdir()
    monkeypatch.setattr(Config, 'VAULT_PATH', str(p))
    return p


def test_vault_render_full_rebuild(app, vault_dir):
    runner = app.test_cli_runner()
    with app.app_context():
        from storage.memory import MemoryWriter
        MemoryWriter().write_note(user_id='u', app='a', title='one',
                                   body='', tags=[], folder=None, slug=None)
    result = runner.invoke(args=['vault-render', '--rebuild'])
    assert result.exit_code == 0
    assert 'rendered' in result.output.lower()
    assert (vault_dir / 'u' / 'a' / 'notes' / 'one.md').exists()


def test_vault_render_check_stale(app, vault_dir):
    runner = app.test_cli_runner()
    with app.app_context():
        from storage.memory import MemoryWriter
        from storage.vault_renderer import VaultRenderer
        n = MemoryWriter().write_note(user_id='u', app='a', title='one',
                                       body='v1', tags=[], folder=None, slug=None)
        VaultRenderer().render_one(n)
        (vault_dir / 'u' / 'a' / 'notes' / 'one.md').unlink()
    result = runner.invoke(args=['vault-render', '--check-stale'])
    assert result.exit_code == 0
    assert (vault_dir / 'u' / 'a' / 'notes' / 'one.md').exists()
