"""Tests for the configuration loader."""

import pytest
import yaml

from src.config import AppConfig, ConfigError, PersonConfig, load_config


VALID_CONFIG = {
    "drive_folder_id": "abc123",
    "sheet_id": None,
    "sheet_name": "Test Sheet",
    "persons": [
        {"name": "Alice", "patterns": ["alice", "chase_alice"]},
        {"name": "Bob", "patterns": ["bob"]},
    ],
    "categories": {
        "Groceries": ["walmart", "costco"],
        "Dining": ["starbucks"],
    },
    "budgets": {
        "Groceries": 800,
        "Dining": 400,
    },
    "log_level": "DEBUG",
}


def _write_yaml(tmp_path, data):
    """Helper to write a YAML config file and return its path."""
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(data, default_flow_style=False))
    return str(p)


# --- Happy path ---


class TestValidConfig:
    def test_loads_all_fields(self, tmp_path):
        path = _write_yaml(tmp_path, VALID_CONFIG)
        cfg = load_config(path)

        assert isinstance(cfg, AppConfig)
        assert cfg.drive_folder_id == "abc123"
        assert cfg.sheet_id is None
        assert cfg.sheet_name == "Test Sheet"
        assert len(cfg.persons) == 2
        assert cfg.persons[0] == PersonConfig(name="Alice", patterns=["alice", "chase_alice"])
        assert cfg.categories["Groceries"] == ["walmart", "costco"]
        assert cfg.budgets["Dining"] == 400.0
        assert cfg.log_level == "DEBUG"

    def test_defaults_for_optional_fields(self, tmp_path):
        minimal = {
            "drive_folder_id": "xyz",
            "persons": [{"name": "A", "patterns": ["a"]}],
            "categories": {"Food": ["grocery"]},
        }
        path = _write_yaml(tmp_path, minimal)
        cfg = load_config(path)

        assert cfg.sheet_id is None
        assert cfg.sheet_name == "Finance Tracker"
        assert cfg.budgets == {}
        assert cfg.log_level == "INFO"

    def test_sheet_id_as_string(self, tmp_path):
        data = {**VALID_CONFIG, "sheet_id": "some-sheet-id"}
        path = _write_yaml(tmp_path, data)
        cfg = load_config(path)
        assert cfg.sheet_id == "some-sheet-id"

    def test_integer_budgets_converted_to_float(self, tmp_path):
        path = _write_yaml(tmp_path, VALID_CONFIG)
        cfg = load_config(path)
        assert isinstance(cfg.budgets["Groceries"], float)


# --- Missing file ---


class TestMissingFile:
    def test_missing_config_file_exits(self):
        with pytest.raises(SystemExit, match="Configuration file not found"):
            load_config("/nonexistent/path/config.yaml")


# --- Missing required fields ---


class TestMissingFields:
    def test_missing_drive_folder_id(self, tmp_path):
        data = {**VALID_CONFIG}
        del data["drive_folder_id"]
        path = _write_yaml(tmp_path, data)
        with pytest.raises(SystemExit, match="drive_folder_id"):
            load_config(path)

    def test_missing_persons(self, tmp_path):
        data = {**VALID_CONFIG}
        del data["persons"]
        path = _write_yaml(tmp_path, data)
        with pytest.raises(SystemExit, match="persons"):
            load_config(path)

    def test_missing_categories(self, tmp_path):
        data = {**VALID_CONFIG}
        del data["categories"]
        path = _write_yaml(tmp_path, data)
        with pytest.raises(SystemExit, match="categories"):
            load_config(path)

    def test_person_missing_name(self, tmp_path):
        data = {**VALID_CONFIG, "persons": [{"patterns": ["a"]}]}
        path = _write_yaml(tmp_path, data)
        with pytest.raises(SystemExit, match="name"):
            load_config(path)

    def test_person_missing_patterns(self, tmp_path):
        data = {**VALID_CONFIG, "persons": [{"name": "A"}]}
        path = _write_yaml(tmp_path, data)
        with pytest.raises(SystemExit, match="patterns"):
            load_config(path)


# --- Invalid values ---


class TestInvalidValues:
    def test_empty_drive_folder_id(self, tmp_path):
        data = {**VALID_CONFIG, "drive_folder_id": ""}
        path = _write_yaml(tmp_path, data)
        with pytest.raises(SystemExit, match="drive_folder_id"):
            load_config(path)

    def test_drive_folder_id_not_string(self, tmp_path):
        data = {**VALID_CONFIG, "drive_folder_id": 12345}
        path = _write_yaml(tmp_path, data)
        with pytest.raises(SystemExit, match="drive_folder_id"):
            load_config(path)

    def test_sheet_id_wrong_type(self, tmp_path):
        data = {**VALID_CONFIG, "sheet_id": 999}
        path = _write_yaml(tmp_path, data)
        with pytest.raises(SystemExit, match="sheet_id"):
            load_config(path)

    def test_persons_empty_list(self, tmp_path):
        data = {**VALID_CONFIG, "persons": []}
        path = _write_yaml(tmp_path, data)
        with pytest.raises(SystemExit, match="persons"):
            load_config(path)

    def test_persons_not_a_list(self, tmp_path):
        data = {**VALID_CONFIG, "persons": "not a list"}
        path = _write_yaml(tmp_path, data)
        with pytest.raises(SystemExit, match="persons"):
            load_config(path)

    def test_categories_empty(self, tmp_path):
        data = {**VALID_CONFIG, "categories": {}}
        path = _write_yaml(tmp_path, data)
        with pytest.raises(SystemExit, match="categories"):
            load_config(path)

    def test_budget_value_not_number(self, tmp_path):
        data = {**VALID_CONFIG, "budgets": {"Groceries": "a lot"}}
        path = _write_yaml(tmp_path, data)
        with pytest.raises(SystemExit, match="budgets"):
            load_config(path)

    def test_invalid_yaml_syntax(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text(": :\n  - [invalid")
        with pytest.raises(SystemExit, match="Invalid YAML"):
            load_config(str(p))

    def test_top_level_not_mapping(self, tmp_path):
        p = tmp_path / "list.yaml"
        p.write_text("- item1\n- item2\n")
        with pytest.raises(SystemExit, match="YAML mapping"):
            load_config(str(p))

    def test_person_empty_patterns(self, tmp_path):
        data = {**VALID_CONFIG, "persons": [{"name": "A", "patterns": []}]}
        path = _write_yaml(tmp_path, data)
        with pytest.raises(SystemExit, match="patterns"):
            load_config(path)
