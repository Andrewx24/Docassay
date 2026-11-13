"""Word tokenizer and the optional tiktoken hook."""

from __future__ import annotations

import pytest

from docassay.tokenize import token_count


def test_words_count_runs_after_normalization() -> None:
    assert token_count("  one   two  ") == 2
    assert token_count("") == 0
    assert token_count("a\u00a0b") == 2
    assert token_count("conti-\n nued") == 1


def test_unknown_tokenizer_names_the_choices() -> None:
    with pytest.raises(ValueError, match="words"):
        token_count("hello", "nope")


def test_tiktoken_prefix_requires_an_encoding() -> None:
    with pytest.raises(ValueError, match="cl100k_base"):
        token_count("hello", "tiktoken:")


def test_tiktoken_import_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> object:
        raise ImportError(name)

    monkeypatch.setattr("docassay.tokenize.importlib.import_module", missing)
    with pytest.raises(ImportError, match=r"docassay\[tiktoken\]"):
        token_count("hello", "tiktoken:cl100k_base")


def test_tiktoken_stand_in_counts_encoded_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    class Encoder:
        def encode(self, text: str) -> list[int]:
            return [1, 2, 3] if text == "hello" else []

    class Module:
        @staticmethod
        def get_encoding(name: str) -> Encoder:
            if name != "cl100k_base":
                raise KeyError(name)
            return Encoder()

    monkeypatch.setattr("docassay.tokenize.importlib.import_module", lambda name: Module)
    assert token_count("hello", "tiktoken:cl100k_base") == 3
    with pytest.raises(ValueError, match="unknown tiktoken encoding"):
        token_count("hello", "tiktoken:nope")


def test_tiktoken_stand_in_rejects_a_broken_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("docassay.tokenize.importlib.import_module", lambda name: object())
    with pytest.raises(ImportError, match=r"docassay\[tiktoken\]"):
        token_count("hello", "tiktoken:cl100k_base")

    class NoEncode:
        @staticmethod
        def get_encoding(name: str) -> object:
            return object()

    monkeypatch.setattr("docassay.tokenize.importlib.import_module", lambda name: NoEncode)
    with pytest.raises(TypeError, match="encode"):
        token_count("hello", "tiktoken:cl100k_base")

    class BadList:
        class Encoder:
            def encode(self, text: str) -> str:
                return text

        @staticmethod
        def get_encoding(name: str) -> Encoder:
            return BadList.Encoder()

    monkeypatch.setattr("docassay.tokenize.importlib.import_module", lambda name: BadList)
    with pytest.raises(TypeError, match="list"):
        token_count("hello", "tiktoken:cl100k_base")
