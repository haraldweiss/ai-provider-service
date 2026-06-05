"""flask vault-backup CLI tests."""

import os
import tarfile
import pytest
from pathlib import Path
from config import Config


def test_vault_backup_creates_files(app, tmp_path, monkeypatch):
    monkeypatch.setattr(Config, 'VAULT_PATH', str(tmp_path / 'vault'))
    runner = app.test_cli_runner()
    with app.app_context():
        from storage.memory import MemoryWriter
        from storage.vault_renderer import VaultRenderer
        (tmp_path / 'vault').mkdir()
        n = MemoryWriter().write_note(user_id='u', app='a', title='backup',
                                       body='data', tags=[], folder=None, slug=None)
        VaultRenderer().render_one(n)
    result = runner.invoke(args=['vault-backup', '--output', str(tmp_path)])
    assert result.exit_code == 0
    files = list(tmp_path.iterdir())
    tars = [f for f in files if f.name.endswith('.tar.gz')]
    dbs = [f for f in files if f.name.endswith('.db')]
    assert len(tars) >= 1


def test_vault_backup_db_only(app, tmp_path):
    runner = app.test_cli_runner()
    result = runner.invoke(args=['vault-backup', '--output', str(tmp_path), '--db-only'])
    assert result.exit_code == 0
