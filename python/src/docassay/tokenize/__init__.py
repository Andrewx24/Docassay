"""Token counters used by the chunk scorer.

Example:
    from docassay.tokenize import token_count

    token_count("one two three") == 3
"""

from __future__ import annotations

import importlib

from docassay.normalize import norm_text
from docassay.normalize.text import is_whitespace

__all__ = ["token_count"]


def token_count(text: str, tokenizer: str = "words") -> int:
    """Count tokens in ``text``.

    ``words`` counts maximal runs of non-whitespace characters after
    ``norm_text``. ``tiktoken:<encoding>`` uses that encoding and requires the
    ``tiktoken`` extra. The count is the length of the encoding, on the
    original text.

    Example:
        token_count("one two three", "words") == 3
    """
    if tokenizer == "words":
        return _words(text)
    prefix = "tiktoken:"
    if tokenizer.startswith(prefix):
        encoding = tokenizer[len(prefix) :]
        if not encoding:
            raise ValueError(
                'tokenizer "tiktoken:" needs an encoding, for example "tiktoken:cl100k_base".'
            )
        return _tiktoken(text, encoding)
    raise ValueError(f'unknown tokenizer {tokenizer!r}. Use "words" or "tiktoken:<encoding>".')


def _words(text: str) -> int:
    count = 0
    in_run = False
    for char in norm_text(text):
        if is_whitespace(char):
            in_run = False
            continue
        if not in_run:
            count += 1
            in_run = True
    return count


def _tiktoken(text: str, encoding: str) -> int:
    try:
        module = importlib.import_module("tiktoken")
    except ImportError as exc:
        raise ImportError(
            f'tokenizer "tiktoken:{encoding}" requires the tiktoken extra. '
            "Install docassay[tiktoken]."
        ) from exc
    get_encoding = getattr(module, "get_encoding", None)
    if not callable(get_encoding):
        raise ImportError(
            f'tokenizer "tiktoken:{encoding}" requires the tiktoken extra. '
            "Install docassay[tiktoken]."
        )
    try:
        encoder = get_encoding(encoding)
    except KeyError as exc:
        raise ValueError(
            f"unknown tiktoken encoding {encoding!r}. Use an encoding name such as cl100k_base."
        ) from exc
    encode = getattr(encoder, "encode", None)
    if not callable(encode):
        raise TypeError(f"tiktoken encoding {encoding!r} has no encode method.")
    tokens = encode(text)
    if not isinstance(tokens, list):
        raise TypeError(f"tiktoken encoding {encoding!r} did not return a list of tokens.")
    return len(tokens)
