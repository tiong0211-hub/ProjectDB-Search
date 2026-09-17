from __future__ import annotations

from projectdb_search.storage.inverted_index import is_loose_match


def test_substring_relationship_matches_both_directions():
    assert is_loose_match("compressor", "compressorstation") is True
    assert is_loose_match("compressorstation", "compressor") is True


def test_single_character_typo_matches():
    assert is_loose_match("compresor", "compressor") is True  # missing char
    assert is_loose_match("compressorr", "compressor") is True  # extra char
    assert is_loose_match("compressir", "compressor") is True  # substituted char


def test_two_character_difference_does_not_match():
    assert is_loose_match("compressirr", "compressor") is False


def test_short_tokens_do_not_fuzzy_match_even_with_one_char_difference():
    # "ab" vs "ac" is a 1-char substitution, but both are too short for the
    # typo tolerance to mean anything -- it would match almost any pair.
    assert is_loose_match("ab", "ac") is False


def test_exact_match_is_loose_match_too():
    assert is_loose_match("hx-203", "hx-203") is True


def test_unrelated_words_do_not_match():
    assert is_loose_match("unrelated", "totally-different") is False


def test_numeric_tokens_never_loose_match_even_when_one_digit_apart():
    # Years/revision numbers are exact identifiers -- "2018" must not
    # loosely match "2019" or "2015" just because they're 1 char apart.
    assert is_loose_match("2018", "2019") is False
    assert is_loose_match("2018", "2015") is False
    assert is_loose_match("2018", "2018") is True
