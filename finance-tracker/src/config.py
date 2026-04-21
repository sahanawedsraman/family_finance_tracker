"""Configuration loader and validation for the Finance Tracker application."""

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class PersonConfig:
    """Configuration for a tracked person."""

    name: str
    patterns: list[str]


@dataclass
class GeminiConfig:
    """Configuration for Gemini LLM-based parsing."""

    enabled: bool = False
    api_key: str = ""  # can also be set via GEMINI_API_KEY env var
    model: str = "gemini-2.0-flash"
    fallback_models: list[str] = None  # tried in order if primary hits rate limit

    def __post_init__(self):
        if self.fallback_models is None:
            self.fallback_models = ["gemini-2.0-flash-lite", "gemini-1.5-flash"]


@dataclass
class AppConfig:
    """Top-level application configuration."""

    drive_folder_id: str
    sheet_id: str | None
    sheet_name: str
    persons: list[PersonConfig]
    categories: dict[str, list[str]]
    budgets: dict[str, float]
    gemini: GeminiConfig = None
    log_level: str = "INFO"

    def __post_init__(self):
        if self.gemini is None:
            self.gemini = GeminiConfig()


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


def _validate_persons(raw: list) -> list[PersonConfig]:
    """Validate and build PersonConfig list from raw YAML data."""
    if not isinstance(raw, list) or len(raw) == 0:
        raise ConfigError("'persons' must be a non-empty list")
    persons: list[PersonConfig] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ConfigError(f"persons[{i}] must be a mapping")
        if "name" not in entry or not isinstance(entry["name"], str) or not entry["name"].strip():
            raise ConfigError(f"persons[{i}] must have a non-empty string 'name'")
        if "patterns" not in entry or not isinstance(entry["patterns"], list) or len(entry["patterns"]) == 0:
            raise ConfigError(f"persons[{i}] must have a non-empty list 'patterns'")
        if not all(isinstance(p, str) for p in entry["patterns"]):
            raise ConfigError(f"persons[{i}].patterns must contain only strings")
        persons.append(PersonConfig(name=entry["name"], patterns=entry["patterns"]))
    return persons


def _validate_categories(raw) -> dict[str, list[str]]:
    """Validate category keyword mappings."""
    if not isinstance(raw, dict) or len(raw) == 0:
        raise ConfigError("'categories' must be a non-empty mapping of category -> keyword list")
    categories: dict[str, list[str]] = {}
    for cat, keywords in raw.items():
        if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
            raise ConfigError(f"categories['{cat}'] must be a list of strings")
        categories[str(cat)] = keywords
    return categories


def _validate_budgets(raw) -> dict[str, float]:
    """Validate budget amounts per category."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError("'budgets' must be a mapping of category -> number")
    budgets: dict[str, float] = {}
    for cat, amount in raw.items():
        if not isinstance(amount, (int, float)):
            raise ConfigError(f"budgets['{cat}'] must be a number, got {type(amount).__name__}")
        budgets[str(cat)] = float(amount)
    return budgets


def load_config(path: str) -> AppConfig:
    """Load and validate configuration from a YAML file.

    Raises:
        SystemExit: If the file is missing or contains invalid configuration.
    """
    config_path = Path(path)
    if not config_path.is_file():
        raise SystemExit(f"Configuration file not found: {path}")

    logger.info("Loading configuration from %s", path)

    try:
        with open(config_path, "r") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        logger.error("Invalid YAML in configuration file: %s", exc)
        raise SystemExit(f"Invalid YAML in configuration file: {exc}")

    if not isinstance(data, dict):
        raise SystemExit("Configuration file must contain a YAML mapping at the top level")

    try:
        # Required string field
        drive_folder_id = data.get("drive_folder_id")
        if not isinstance(drive_folder_id, str) or not drive_folder_id.strip():
            raise ConfigError("'drive_folder_id' is required and must be a non-empty string")

        # Optional sheet_id (None means auto-create)
        sheet_id = data.get("sheet_id")
        if sheet_id is not None and not isinstance(sheet_id, str):
            raise ConfigError("'sheet_id' must be a string or null")
        # Treat empty string as None (auto-create)
        if isinstance(sheet_id, str) and not sheet_id.strip():
            sheet_id = None

        # sheet_name with default
        sheet_name = data.get("sheet_name", "Finance Tracker")
        if not isinstance(sheet_name, str) or not sheet_name.strip():
            raise ConfigError("'sheet_name' must be a non-empty string")

        # persons
        if "persons" not in data:
            raise ConfigError("'persons' is required")
        persons = _validate_persons(data["persons"])

        # categories
        if "categories" not in data:
            raise ConfigError("'categories' is required")
        categories = _validate_categories(data["categories"])

        # budgets (optional)
        budgets = _validate_budgets(data.get("budgets"))

        # log_level
        log_level = data.get("log_level", "INFO")
        if not isinstance(log_level, str):
            raise ConfigError("'log_level' must be a string")

        config = AppConfig(
            drive_folder_id=drive_folder_id,
            sheet_id=sheet_id,
            sheet_name=sheet_name,
            persons=persons,
            categories=categories,
            budgets=budgets,
            log_level=log_level,
        )
        logger.info(
            "Configuration loaded: %d persons, %d categories, %d budgets",
            len(persons),
            len(categories),
            len(budgets),
        )
        return config
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        raise SystemExit(f"Configuration error: {exc}")
