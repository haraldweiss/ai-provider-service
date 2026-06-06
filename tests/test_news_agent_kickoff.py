"""news-agent kickoff prompt builder — must inject current date so the
LLM does not fall back to its knowledge cutoff and republish stale releases."""

from datetime import date


def test_kickoff_contains_today_iso():
    """The kickoff must mention today's ISO date (YYYY-MM-DD) so the model
    has an unambiguous anchor for `recent` / `letzte Woche` framing."""
    from agents.news.prompts import build_user_kickoff
    today = date.today().isoformat()
    text = build_user_kickoff()
    assert today in text, f'expected {today} in kickoff, got: {text}'


def test_kickoff_contains_freshness_window():
    """The kickoff must declare an explicit freshness window so the model
    actually filters web_search results, not just decorates with a date."""
    from agents.news.prompts import build_user_kickoff
    text = build_user_kickoff()
    # Either '7 Tage' or 'last 7 days' or 'seit <iso>' — pin on the
    # phrase we ship to keep the contract obvious.
    assert '7 Tage' in text or 'last 7 days' in text, (
        f'expected freshness-window phrase, got: {text}'
    )


def test_kickoff_warns_against_knowledge_cutoff():
    """Defensive: the kickoff should explicitly tell the model to ignore
    its knowledge cutoff and rely only on web_search/web_fetch results.
    Otherwise it falls back to training-data versions (e.g. Ollama 0.30
    instead of whatever's actually current)."""
    from agents.news.prompts import build_user_kickoff
    text = build_user_kickoff().lower()
    # Be lenient on phrasing — accept any of these signals.
    signals = ['knowledge cutoff', 'training data', 'trainingsdaten',
               'nicht aus dem gedächtnis', 'web_search', 'verifiziere']
    assert any(s in text for s in signals), (
        f'expected anti-cutoff signal in: {text}'
    )


def test_kickoff_seven_days_window_uses_correct_iso():
    """The 7-day cutoff date should be exactly today-7days, not approximate."""
    from agents.news.prompts import build_user_kickoff
    from datetime import timedelta
    text = build_user_kickoff()
    expected_cutoff = (date.today() - timedelta(days=7)).isoformat()
    assert expected_cutoff in text, (
        f'expected freshness-cutoff {expected_cutoff} in: {text}'
    )
