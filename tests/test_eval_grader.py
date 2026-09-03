"""Tests for eval grader module."""

import pytest
from eval.grader import (
    deterministic_score, llm_judge_score, composite_score,
    score_to_grade, GRADE_LABELS,
)


class TestDeterministicScore:
    def test_empty_response_returns_zero(self):
        assert deterministic_score('') == 0.0
        assert deterministic_score('   ') == 0.0

    def test_no_criteria_returns_half(self):
        assert deterministic_score('some response') == 0.5

    def test_keyword_matching(self):
        score = deterministic_score(
            'The function returns True',
            expected_keywords='function,returns,True',
        )
        assert score == 1.0

    def test_partial_keyword_match(self):
        score = deterministic_score(
            'The function works',
            expected_keywords='function,returns,True',
        )
        assert 0.3 < score < 0.4

    def test_min_length_penalty(self):
        score = deterministic_score('hi', min_length=100)
        assert score < 0.1

    def test_min_length_met(self):
        score = deterministic_score('x' * 100, min_length=100)
        assert score == 1.0

    def test_max_length_penalty(self):
        score = deterministic_score('x' * 200, max_length=100)
        assert score < 1.0

    def test_combined_criteria(self):
        score = deterministic_score(
            'def is_palindrome(s): return s == s[::-1]',
            expected_keywords='def,is_palindrome,return',
            min_length=20,
        )
        assert score == 1.0


class TestScoreToGrade:
    def test_excellent(self):
        assert score_to_grade(4.7) == 'excellent'

    def test_good(self):
        assert score_to_grade(4.0) == 'good'

    def test_acceptable(self):
        assert score_to_grade(3.0) == 'acceptable'

    def test_poor(self):
        assert score_to_grade(2.0) == 'poor'

    def test_very_poor(self):
        assert score_to_grade(1.0) == 'very_poor'

    def test_boundary_values(self):
        assert score_to_grade(4.5) == 'excellent'
        assert score_to_grade(3.5) == 'good'
        assert score_to_grade(2.5) == 'acceptable'
        assert score_to_grade(1.5) == 'poor'


class TestCompositeScore:
    def test_perfect_scores(self):
        score = composite_score(5.0, 5.0, 1.0, latency_ms=1000)
        assert score == 5.0

    def test_zero_scores(self):
        score = composite_score(1.0, 1.0, 0.0, latency_ms=120000)
        assert score < 1.5

    def test_latency_bonus(self):
        fast = composite_score(3.0, 3.0, 0.5, latency_ms=1000)
        slow = composite_score(3.0, 3.0, 0.5, latency_ms=60000)
        assert fast > slow

    def test_no_latency(self):
        score = composite_score(3.0, 3.0, 0.5)
        assert 1.0 < score < 5.0

    def test_bounded(self):
        score = composite_score(5.0, 5.0, 1.0, latency_ms=100)
        assert score <= 5.0
        score = composite_score(0.0, 0.0, 0.0, latency_ms=999999)
        assert score >= 0.0


class TestLlmJudgeScore:
    def test_empty_response(self):
        result = llm_judge_score('task', '')
        assert result['accuracy'] == 1.0
        assert result['quality'] == 1.0

    def test_no_judge_fn(self):
        result = llm_judge_score('task', 'response')
        assert result['accuracy'] == 3.0
        assert result['quality'] == 3.0

    def test_judge_fn_success(self):
        def mock_judge(messages):
            return '{"accuracy": 4, "quality": 5, "reason": "good"}'

        result = llm_judge_score('task', 'response', judge_dispatch_fn=mock_judge)
        assert result['accuracy'] == 4.0
        assert result['quality'] == 5.0

    def test_judge_fn_parse_failure(self):
        def mock_judge(messages):
            return 'not json'

        result = llm_judge_score('task', 'response', judge_dispatch_fn=mock_judge)
        assert result['accuracy'] == 3.0
        assert result['quality'] == 3.0

    def test_judge_fn_exception(self):
        def mock_judge(messages):
            raise RuntimeError('boom')

        result = llm_judge_score('task', 'response', judge_dispatch_fn=mock_judge)
        assert result['accuracy'] == 3.0

    def test_judge_clamps_values(self):
        def mock_judge(messages):
            return '{"accuracy": 10, "quality": -5, "reason": "x"}'

        result = llm_judge_score('task', 'response', judge_dispatch_fn=mock_judge)
        assert result['accuracy'] == 5.0
        assert result['quality'] == 1.0
