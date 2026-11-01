"""Load ``docassay.yaml`` and write a starter project.

Example:
    from docassay import load_config

    config = load_config("docassay.yaml")
    print(config.golden)
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import yaml
from jsonschema.exceptions import ValidationError
from jsonschema.validators import Draft202012Validator
from pydantic import ValidationError as ModelError
from yaml.constructor import ConstructorError

from docassay.models.config import Config

# Built-in adapters from the Adapters doc. Variants are listed in documented order.
PARSERS: dict[str, tuple[str, ...]] = {
    "docling": (),
    "file": (),
    "llamaparse": ("fast", "cost_effective", "agentic", "agentic_plus"),
    "reducto": (),
}
VALID_IDS = "docling, file, llamaparse, reducto"

DEFAULT_CACHE = ".docassay/cache"
DEFAULT_BASELINE = ".docassay/baseline.json"
DEFAULT_DROP = 0.02

CONFIG_TEMPLATE = """\
# Score document parsers against labeled golden files.
version: 1
golden: golden/                       # folder of *.yaml golden files
cache: .docassay/cache                # optional; this is the default
baseline: .docassay/baseline.json     # optional; this is the default
parsers:
  - id: file                          # score output your pipeline already produced
    name: my-pipeline                 # display name when one adapter is used twice
    options: {dir: out/parsed}        # <dir>/<golden id>.json, then <dir>/<golden id>.md
thresholds:
  default: {drop: 0.02}               # fail if a score falls more than this far below baseline
normalize:
  date_order: mdy                     # mdy | dmy, for ambiguous dates like 03/04/2026
  decimal: "."                        # "." | ","
tokenizer: words                      # words | tiktoken:<encoding>
"""

GOLDEN_TEMPLATE = """\
# Label what correct parser output looks like. Every section under expect is optional.
file: files/example.pdf               # relative to this golden file
id: example
expect:
  text_contains:
    - "Replace this with a sentence from the document"
"""

_VALIDATOR: Draft202012Validator | None = None
_PARSER_ID = re.compile(r"^[a-z][a-z0-9_-]*(:[a-z0-9_]+)?$")


class CoreLoader(yaml.SafeLoader):
    """YAML 1.2.2 core schema. Dates, yes/no, and on/off stay strings."""


CoreLoader.yaml_implicit_resolvers = {}


def _add_resolver(tag: str, pattern: str, first: str) -> None:
    yaml.add_implicit_resolver(tag, re.compile(pattern), list(first), Loader=CoreLoader)


_add_resolver("tag:yaml.org,2002:null", r"^(?:null|Null|NULL|~)$", "nN~")
_add_resolver("tag:yaml.org,2002:bool", r"^(?:true|True|TRUE|false|False|FALSE)$", "tTfF")
_add_resolver("tag:yaml.org,2002:int", r"^0o[0-7]+$", "0")
_add_resolver("tag:yaml.org,2002:int", r"^0x[0-9a-fA-F]+$", "0")
_add_resolver("tag:yaml.org,2002:int", r"^[-+]?[0-9]+$", "-+0123456789")
_add_resolver(
    "tag:yaml.org,2002:float",
    r"^[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?$",
    "-+.0123456789",
)
_add_resolver("tag:yaml.org,2002:float", r"^[-+]?\.(?:inf|Inf|INF)$", "-+.")
_add_resolver("tag:yaml.org,2002:float", r"^\.(?:nan|NaN|NAN)$", ".")


def _construct_int(loader: CoreLoader, node: yaml.Node) -> int:
    if not isinstance(node, yaml.ScalarNode):
        raise ConstructorError(None, None, "expected an integer scalar", node.start_mark)
    text = loader.construct_scalar(node)
    sign = 1
    if text.startswith("+"):
        text = text[1:]
    elif text.startswith("-"):
        sign = -1
        text = text[1:]
    if text.startswith("0o"):
        return sign * int(text[2:], 8)
    if text.startswith("0x"):
        return sign * int(text[2:], 16)
    return sign * int(text, 10)


def _construct_float(loader: CoreLoader, node: yaml.Node) -> float:
    if not isinstance(node, yaml.ScalarNode):
        raise ConstructorError(None, None, "expected a float scalar", node.start_mark)
    text = loader.construct_scalar(node)
    lower = text.lower()
    if lower == ".nan":
        return math.nan
    if lower in {".inf", "+.inf", "-.inf"}:
        if text.startswith("-"):
            return -math.inf
        return math.inf
    return float(text)


CoreLoader.add_constructor("tag:yaml.org,2002:int", _construct_int)
CoreLoader.add_constructor("tag:yaml.org,2002:float", _construct_float)


@dataclass(frozen=True)
class ConfigIssue:
    """One problem in a config file.

    Example:
        print(ConfigIssue("docassay.yaml", 4, 5, "unknown parser id \\"nope\\""))
    """

    path: str
    line: int
    column: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}:{self.column}: {self.message}"


class ConfigError(Exception):
    """Raised by :func:`load_config` when the file is invalid. CLI commands exit 2.

    ``exit_code`` is 2, the CLI code for a config error.

    Example:
        try:
            load_config("docassay.yaml")
        except ConfigError as err:
            raise SystemExit(2) from err
    """

    exit_code: ClassVar[int] = 2

    def __init__(self, issues: list[ConfigIssue]) -> None:
        ordered = sorted(issues, key=_issue_key)
        self.issues: list[ConfigIssue] = ordered
        super().__init__("\n".join(str(issue) for issue in ordered))


def _issue_key(issue: ConfigIssue) -> tuple[str, int, int, str]:
    return (issue.path, issue.line, issue.column, issue.message)


def display_path(path: Path) -> str:
    """Return ``path`` relative to the working directory, using ``/``.

    Example:
        display_path(Path("docassay.yaml"))
    """
    return Path(os.path.relpath(path.resolve(), Path.cwd().resolve())).as_posix()


def load_config(path: str | Path) -> Config:
    """Load ``docassay.yaml``, apply defaults, and resolve paths.

    Paths are resolved relative to the config file and stored relative to that
    file. Raises :class:`ConfigError` with every problem. Callers should exit 2.

    Example:
        config = load_config("docassay.yaml")
        print(config.parsers[0].id)
    """
    issues, config = _inspect(Path(path))
    if issues or config is None:
        raise ConfigError(issues)
    return config


def init_command(directory: str | Path | None = None, *, force: bool = False) -> int:
    """Write ``docassay.yaml`` and ``golden/example.yaml``. Return the exit code.

    Refuses to overwrite an existing file unless ``force`` is set.

    Example:
        raise SystemExit(init_command(force=False))
    """
    root = Path.cwd() if directory is None else Path(directory)
    config_path = root / "docassay.yaml"
    golden_path = root / "golden" / "example.yaml"
    existing = [path for path in (config_path, golden_path) if path.exists()]
    if existing and not force:
        for path in existing:
            print(f"{display_path(path)}:1:1: file already exists; rerun with --force")
        return 2
    try:
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        golden_path.write_text(GOLDEN_TEMPLATE, encoding="utf-8")
    except OSError as err:
        print(f"{display_path(root)}:1:1: {err.strerror or 'could not write files'}")
        return 2
    print(f"wrote {display_path(config_path)}")
    print(f"wrote {display_path(golden_path)}")
    return 0


def canonical_config_json(config: Config) -> str:
    """Serialize a config to the JSON both language ports must match.

    Example:
        text = canonical_config_json(load_config("docassay.yaml"))
    """
    payload = _canonicalize(config.model_dump(mode="json", exclude_none=True))
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _canonicalize(value: object) -> object:
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {str(key): _canonicalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    return value


def _inspect(path: Path) -> tuple[list[ConfigIssue], Config | None]:
    shown = display_path(path)
    if not path.exists():
        return [ConfigIssue(shown, 1, 1, "path not found")], None
    if not path.is_file():
        return [ConfigIssue(shown, 1, 1, "path is not a file")], None
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [ConfigIssue(shown, 1, 1, "file is not valid UTF-8")], None
    except OSError as err:
        return [ConfigIssue(shown, 1, 1, err.strerror or "could not read file")], None
    try:
        node, data = _compose(text)
    except yaml.YAMLError as err:
        return [_yaml_issue(shown, err)], None
    if not isinstance(node, yaml.MappingNode) or not isinstance(data, dict):
        line, column = _position(node)
        return [ConfigIssue(shown, line, column, "config must be a mapping")], None
    issues = _duplicate_keys(node, shown)
    issues.extend(_schema_issues(data, node, shown))
    issues.extend(_parser_issues(data, node, shown))
    if any(_is_schema_message(issue.message) for issue in issues):
        return issues, None
    _apply_defaults(data)
    _resolve_paths(data, path)
    try:
        config = Config.model_validate(data)
    except ModelError as err:
        detail = err.errors()[0]["msg"] if err.errors() else "value does not match the config model"
        issues.append(ConfigIssue(shown, 1, 1, detail))
        return issues, None
    return issues, config if not issues else None


def _apply_defaults(data: dict[object, object]) -> None:
    if "cache" not in data or data.get("cache") is None:
        data["cache"] = DEFAULT_CACHE
    if "baseline" not in data or data.get("baseline") is None:
        data["baseline"] = DEFAULT_BASELINE
    if "tokenizer" not in data or data.get("tokenizer") is None:
        data["tokenizer"] = "words"
    thresholds = data.get("thresholds")
    if not isinstance(thresholds, dict):
        thresholds = {}
        data["thresholds"] = thresholds
    default = thresholds.get("default")
    if not isinstance(default, dict):
        default = {}
        thresholds["default"] = default
    if "drop" not in default or default.get("drop") is None:
        default["drop"] = DEFAULT_DROP
    normalize = data.get("normalize")
    if not isinstance(normalize, dict):
        normalize = {}
        data["normalize"] = normalize
    if "date_order" not in normalize or normalize.get("date_order") is None:
        normalize["date_order"] = "mdy"
    if "decimal" not in normalize or normalize.get("decimal") is None:
        normalize["decimal"] = "."


def _resolve_paths(data: dict[object, object], config_path: Path) -> None:
    for key in ("golden", "cache", "baseline"):
        value = data.get(key)
        if isinstance(value, str) and value:
            data[key] = _relative_to_config(config_path, value)
    parsers = data.get("parsers")
    if not isinstance(parsers, list):
        return
    for parser in parsers:
        if not isinstance(parser, dict):
            continue
        parser_id = parser.get("id")
        options = parser.get("options")
        if not isinstance(parser_id, str) or not isinstance(options, dict):
            continue
        adapter, _variant = _split_id(parser_id)
        directory = options.get("dir")
        if adapter == "file" and isinstance(directory, str) and directory:
            options["dir"] = _relative_to_config(config_path, directory)


def _relative_to_config(config_path: Path, value: str) -> str:
    base = config_path.parent.resolve()
    absolute = (config_path.parent / value).resolve()
    relative = Path(os.path.relpath(absolute, base)).as_posix()
    return relative if relative else "."


def _parser_issues(data: dict[object, object], node: yaml.Node, shown: str) -> list[ConfigIssue]:
    issues: list[ConfigIssue] = []
    parsers = data.get("parsers")
    if not isinstance(parsers, list):
        return issues
    seen: dict[str, str] = {}
    for index, parser in enumerate(parsers):
        if not isinstance(parser, dict):
            continue
        parser_id = parser.get("id")
        if isinstance(parser_id, str) and _PARSER_ID.match(parser_id):
            issues.extend(_known_parser(parser_id, node, index, shown))
        label = _display_name(parser)
        if label is None:
            continue
        previous = seen.get(label)
        if previous is not None:
            located = _parser_node(
                node, index, "name" if isinstance(parser.get("name"), str) else "id"
            )
            line, column = _position(located)
            issues.append(ConfigIssue(shown, line, column, f'duplicate display name "{label}"'))
        else:
            seen[label] = parser_id if isinstance(parser_id, str) else ""
    return issues


def _known_parser(parser_id: str, node: yaml.Node, index: int, shown: str) -> list[ConfigIssue]:
    adapter, variant = _split_id(parser_id)
    located = _parser_node(node, index, "id")
    line, column = _position(located)
    if adapter not in PARSERS:
        return [
            ConfigIssue(
                shown,
                line,
                column,
                f'unknown parser id "{parser_id}"; valid ids: {VALID_IDS}',
            )
        ]
    variants = PARSERS[adapter]
    if variant is None:
        if variants:
            listed = ", ".join(variants)
            return [
                ConfigIssue(
                    shown,
                    line,
                    column,
                    f'missing variant for "{adapter}"; valid ids: {VALID_IDS}; '
                    f"valid variants: {listed}",
                )
            ]
        return []
    if variant not in variants:
        if variants:
            listed = ", ".join(variants)
            detail = f"; valid variants: {listed}"
        else:
            detail = ""
        return [
            ConfigIssue(
                shown,
                line,
                column,
                f'unknown variant "{variant}" for "{adapter}"; valid ids: {VALID_IDS}{detail}',
            )
        ]
    return []


def _split_id(parser_id: str) -> tuple[str, str | None]:
    if ":" in parser_id:
        adapter, variant = parser_id.split(":", 1)
        return adapter, variant
    return parser_id, None


def _display_name(parser: dict[object, object]) -> str | None:
    name = parser.get("name")
    if isinstance(name, str) and name:
        return name
    parser_id = parser.get("id")
    if isinstance(parser_id, str) and parser_id:
        return parser_id
    return None


def _parser_node(node: yaml.Node, index: int, key: str) -> yaml.Node | None:
    return _node_at(node, ["parsers", index, key], None)


def _schema_path() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "spec" / "config.schema.json"
        if candidate.is_file():
            return candidate
    raise ConfigError(
        [
            ConfigIssue(
                "spec/config.schema.json",
                1,
                1,
                "could not find spec/config.schema.json; run from a docassay checkout",
            )
        ]
    )


def _validator() -> Draft202012Validator:
    global _VALIDATOR
    if _VALIDATOR is None:
        loaded = json.loads(_schema_path().read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ConfigError(
                [ConfigIssue(str(_schema_path()), 1, 1, "config schema must be a JSON object")]
            )
        _VALIDATOR = Draft202012Validator(loaded)
    return _VALIDATOR


def _schema_issues(data: dict[object, object], node: yaml.Node, shown: str) -> list[ConfigIssue]:
    issues: list[ConfigIssue] = []
    for error in _validator().iter_errors(data):
        if error.validator == "additionalProperties" and isinstance(error.instance, dict):
            allowed = error.schema.get("properties", {}) if isinstance(error.schema, dict) else {}
            for key in error.instance:
                if key not in allowed:
                    located = _node_at(node, list(error.absolute_path), str(key))
                    line, column = _position(located)
                    issues.append(ConfigIssue(shown, line, column, f'unknown property "{key}"'))
            continue
        line, column = _position(_node_at(node, list(error.absolute_path), None))
        issues.append(ConfigIssue(shown, line, column, _schema_message(error)))
    return issues


def _schema_message(error: ValidationError) -> str:
    keyword = error.validator
    path = list(error.absolute_path)
    if keyword == "required":
        message = error.message
        if message.startswith("'") and "' " in message:
            return f'missing property "{message.split("'", 2)[1]}"'
        return 'missing property "version"'
    if keyword == "const":
        return f"must be {error.validator_value}"
    if keyword == "enum":
        values = error.validator_value
        if isinstance(values, list):
            return "must be one of " + ", ".join(str(item) for item in values)
        return "must be one of the allowed values"
    if keyword == "pattern":
        if path[-1:] == ["id"]:
            return "must match the parser id pattern"
        return "must match the pattern"
    if keyword == "type":
        return _type_message(error.validator_value)
    if keyword == "minLength":
        return (
            "must not be empty"
            if error.validator_value == 1
            else (f"must be at least {error.validator_value} characters")
        )
    if keyword == "minimum":
        return f"must be >= {error.validator_value}"
    if keyword == "maximum":
        return f"must be <= {error.validator_value}"
    if keyword == "minItems":
        return f"must have at least {error.validator_value} items"
    if keyword == "oneOf":
        if path[-1:] == ["tokenizer"]:
            return 'must be "words" or tiktoken:<encoding>'
        return "must match one schema"
    return f"schema violation ({keyword})"


def _type_message(expected: object) -> str:
    if isinstance(expected, list):
        names = [str(item) for item in expected]
        if len(names) == 1:
            return f"must be {names[0]}"
        return "must be " + ", ".join(names[:-1]) + f", or {names[-1]}"
    return f"must be {expected}"


def _is_schema_message(message: str) -> bool:
    return message.startswith(
        (
            "unknown property ",
            "missing property ",
            "must be ",
            "must not be empty",
            "must have at least ",
            "must match ",
            "schema violation ",
        )
    )


def _compose(text: str) -> tuple[yaml.Node | None, object]:
    loader = CoreLoader(text)
    try:
        node = loader.get_single_node()
        if node is None:
            return None, None
        return node, loader.construct_document(node)
    finally:
        loader.dispose()


def _yaml_issue(shown: str, err: yaml.YAMLError) -> ConfigIssue:
    mark = getattr(err, "problem_mark", None)
    problem = getattr(err, "problem", None)
    message = problem if isinstance(problem, str) and problem else str(err)
    message = message.split("\n", 1)[0]
    if mark is not None:
        return ConfigIssue(shown, mark.line + 1, mark.column + 1, f"invalid YAML: {message}")
    return ConfigIssue(shown, 1, 1, f"invalid YAML: {message}")


def _position(node: yaml.Node | None) -> tuple[int, int]:
    if node is None:
        return 1, 1
    mark = node.start_mark
    return mark.line + 1, mark.column + 1


def _mapping_value(node: yaml.Node, key: str) -> yaml.Node | None:
    if not isinstance(node, yaml.MappingNode):
        return None
    for key_node, value_node in node.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == key:
            return value_node if isinstance(value_node, yaml.Node) else None
    return None


def _mapping_key(node: yaml.Node, key: str) -> yaml.Node | None:
    if not isinstance(node, yaml.MappingNode):
        return None
    for key_node, _value_node in node.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == key:
            return key_node if isinstance(key_node, yaml.Node) else None
    return None


def _duplicate_keys(node: yaml.Node, shown: str) -> list[ConfigIssue]:
    issues: list[ConfigIssue] = []
    if isinstance(node, yaml.MappingNode):
        seen: set[object] = set()
        for key_node, value_node in node.value:
            if isinstance(key_node, yaml.ScalarNode):
                key = key_node.value
                if key in seen:
                    line, column = _position(key_node)
                    issues.append(ConfigIssue(shown, line, column, f'duplicate key "{key}"'))
                else:
                    seen.add(key)
            if isinstance(value_node, yaml.Node):
                issues.extend(_duplicate_keys(value_node, shown))
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            if isinstance(item, yaml.Node):
                issues.extend(_duplicate_keys(item, shown))
    return issues


def _node_at(node: yaml.Node, parts: list[object], key: str | None) -> yaml.Node | None:
    current: yaml.Node | None = node
    for part in parts:
        if current is None:
            return None
        if isinstance(part, int) and isinstance(current, yaml.SequenceNode):
            if isinstance(part, bool) or not 0 <= part < len(current.value):
                return current
            item = current.value[part]
            current = item if isinstance(item, yaml.Node) else current
        elif isinstance(part, str) and isinstance(current, yaml.MappingNode):
            value = _mapping_value(current, part)
            if value is None:
                return current
            current = value
        else:
            return current
    if key is not None and current is not None:
        found = _mapping_key(current, key)
        if found is not None:
            return found
    return current
