from pathlib import Path

from mquery_toolkit.cli import main


def _query(tmp_path: Path) -> Path:
    path = tmp_path / "query.pq"
    path.write_text("let A=Number.From(1) in A", encoding="utf-8")
    return path


def test_cli_parse_check_and_dependencies(tmp_path: Path, capsys):
    path = _query(tmp_path)
    assert main(["parse", str(path)]) == 0
    assert "LetExpression" in capsys.readouterr().out
    assert main(["check", str(path)]) == 0
    assert main(["dependencies", str(path)]) == 0
    assert "Number.From" in capsys.readouterr().out


def test_cli_dry_run_and_write(tmp_path: Path, capsys):
    path = _query(tmp_path)
    assert main(["format", str(path)]) == 0
    assert "---" in capsys.readouterr().out
    assert "A=Number" in path.read_text()
    assert main(["format", str(path), "--write"]) == 0
    assert "A = Number" in path.read_text()


def test_cli_rename_and_replace_source(tmp_path: Path):
    path = _query(tmp_path)
    assert main(["rename", str(path), "--old", "A", "--new", "B", "--write"]) == 0
    assert "let B" in path.read_text()
    assert (
        main(["replace-source", str(path), "--source", "let C = 3 in C", "--write"])
        == 0
    )
    assert path.read_text() == "let C = 3 in C"


def test_cli_refuses_missing_required_edit_arguments(tmp_path: Path, capsys):
    path = _query(tmp_path)
    assert main(["rename", str(path)]) == 2
    assert "MQUERY_ERROR" in capsys.readouterr().out
    assert main(["replace-source", str(path)]) == 2
