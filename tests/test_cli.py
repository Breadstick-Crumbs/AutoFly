from pathlib import Path

from typer.testing import CliRunner

from autofly.cli import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_init_does_not_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    first = runner.invoke(app, ["init", "--path", str(path)])
    assert first.exit_code == 0
    original = path.read_text(encoding="utf-8")
    second = runner.invoke(app, ["init", "--path", str(path)])
    assert second.exit_code == 2
    assert path.read_text(encoding="utf-8") == original


def test_validate_routes_watches_and_dry_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "config.yaml"
    assert runner.invoke(app, ["init", "--path", str(path)]).exit_code == 0
    validation = runner.invoke(app, ["config", "validate", "--config", str(path), "--json"])
    assert validation.exit_code == 0
    assert '"valid": true' in validation.stdout
    watches = runner.invoke(app, ["watches", "list", "--config", str(path), "--json"])
    assert watches.exit_code == 0
    assert "sample-cok-dxb" in watches.stdout
    routes = runner.invoke(app, ["routes", "--config", str(path), "--json"])
    assert routes.exit_code == 0
    assert '"origin": "COK"' in routes.stdout
    dry_run = runner.invoke(app, ["check", "--all", "--dry-run", "--config", str(path), "--json"])
    assert dry_run.exit_code == 0
    assert '"queries_executed": 0' in dry_run.stdout


def test_check_requires_exactly_one_selector(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    runner.invoke(app, ["init", "--path", str(path)])
    neither = runner.invoke(app, ["check", "--dry-run", "--config", str(path)])
    both = runner.invoke(
        app,
        ["check", "--all", "--watch", "sample-cok-dxb", "--dry-run", "--config", str(path)],
    )
    assert neither.exit_code == 2
    assert both.exit_code == 2
