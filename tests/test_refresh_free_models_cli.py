def test_refresh_free_models_cli_refreshes_opencode_and_openrouter(app, monkeypatch):
    from providers.opencode import OpencodeClient
    from providers.openrouter import OpenRouterClient

    monkeypatch.setattr(
        OpencodeClient,
        'try_refresh_free_models',
        classmethod(lambda cls: ['opencode-free']),
    )
    monkeypatch.setattr(
        OpenRouterClient,
        'try_refresh_free_models',
        classmethod(lambda cls: ['openrouter-free']),
        raising=False,
    )

    result = app.test_cli_runner().invoke(args=['refresh-free-models'])

    assert result.exit_code == 0
    assert 'opencode: 1 free models cached: opencode-free' in result.output
    assert 'openrouter: 1 free models cached: openrouter-free' in result.output
