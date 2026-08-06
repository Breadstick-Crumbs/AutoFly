from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from autofly.cli import app
from autofly.errors import ConfigError
from autofly.setup import build_setup_config, parse_iata_list, write_setup_files

runner = CliRunner()


def test_guided_setup_creates_valid_native_files(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    answers = "\n".join(
        [
            "COK, CCJ",
            "DXB, AAN",
            "",
            "2026-08-10",
            "2026-08-20",
            "INR",
            "25000",
            "Asia/Kolkata",
            "n",
            "",
        ]
    )

    result = runner.invoke(
        app,
        ["setup", "--config", str(config_path), "--env-file", str(env_path)],
        input=answers,
    )

    assert result.exit_code == 0, result.output
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw["watches"][0]["origins"] == ["COK", "CCJ"]
    assert raw["watches"][0]["destinations"] == ["DXB", "AAN"]
    assert raw["watches"][0]["dates"]["departure_start"] == "2026-08-10"
    assert raw["notifications"]["telegram"]["enabled"] is False
    environment = env_path.read_text(encoding="utf-8")
    assert f"AUTOFLY_CONFIG={config_path.resolve()}" in environment
    assert "TELEGRAM_BOT_TOKEN" not in environment
    validation = runner.invoke(app, ["config", "validate", "--config", str(config_path)])
    assert validation.exit_code == 0


def test_setup_refuses_to_overwrite_before_prompting(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("owned by user", encoding="utf-8")

    result = runner.invoke(
        app,
        ["setup", "--config", str(config_path), "--env-file", str(tmp_path / ".env")],
    )

    assert result.exit_code == 2
    assert config_path.read_text(encoding="utf-8") == "owned by user"


def test_docker_setup_uses_container_paths_and_exact_safety_budget(tmp_path: Path) -> None:
    origins = parse_iata_list("COK,CCJ,TRV,CNN", "origins")
    destinations = parse_iata_list("DXB,SHJ,AUH,RKT,AAN", "destinations")
    config = build_setup_config(
        origins=origins,
        destinations=destinations,
        date_mode="range",
        date_values={"departure_start": "2026-08-10", "departure_end": "2026-08-20"},
        currency="INR",
        maximum_price=25000,
        timezone="Asia/Kolkata",
        telegram_enabled=True,
        docker=True,
        config_path=tmp_path / "config.yaml",
    )

    write_setup_files(
        config,
        config_path=tmp_path / "config.yaml",
        env_path=tmp_path / ".env",
        telegram_token="secret-value",  # noqa: S106 - synthetic fixture
        telegram_chat_id="12345",
        docker=True,
    )

    assert config.scheduler.max_queries_per_cycle == 120
    assert config.database.path == Path("/var/lib/autofly/autofly.db")
    environment = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "AUTOFLY_CONFIG=/etc/autofly/config.yaml" in environment
    assert "TELEGRAM_BOT_TOKEN=secret-value" in environment


def test_setup_rejects_environment_line_injection_before_writing(tmp_path: Path) -> None:
    config = build_setup_config(
        origins=["COK"],
        destinations=["DXB"],
        date_mode="rolling",
        date_values={"days_from_now": 1, "days_to": 30},
        currency="INR",
        maximum_price=25000,
        timezone="UTC",
        telegram_enabled=True,
        docker=False,
        config_path=tmp_path / "config.yaml",
    )

    with pytest.raises(ConfigError, match="control character"):
        write_setup_files(
            config,
            config_path=tmp_path / "config.yaml",
            env_path=tmp_path / ".env",
            telegram_token="value\nINJECTED=yes",  # noqa: S106 - injection fixture
            telegram_chat_id="12345",
            docker=False,
        )

    assert not (tmp_path / "config.yaml").exists()
