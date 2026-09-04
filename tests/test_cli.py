import os
import threading
from pathlib import Path

import pytest

from pqtools.cli import main


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
    assert "MQUERY_ERROR" in capsys.readouterr().err
    assert main(["replace-source", str(path)]) == 2


def test_cli_replace_source_rejects_invalid_unicode(tmp_path: Path, capsys):
    path = _query(tmp_path)
    assert main(["replace-source", str(path), "--source", "\udcff"]) == 2
    assert "UTF-8" in capsys.readouterr().err


@pytest.mark.skipif(os.name == "nt", reason="FIFOs are POSIX-only")
def test_cli_refuses_fifo_promptly_instead_of_blocking(tmp_path: Path, capsys):
    fifo = tmp_path / "pipe.pq"
    os.mkfifo(fifo)
    result: list[int] = []

    def run() -> None:
        result.append(main(["check", str(fifo)]))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive(), "CLI blocked on the FIFO instead of refusing it"
    assert result == [2]
    assert "non-symlink" in capsys.readouterr().err


# --------------------------------------------------------------------------
# eval
# --------------------------------------------------------------------------


def _demo_query(tmp_path: Path) -> Path:
    path = tmp_path / "demo.pq"
    path.write_text(
        'let Source = Csv.Document(File.Contents("ignored.csv")), '
        'Kept = Table.SelectRows(Source, each [b] <> "y"), '
        'Renamed = Table.RenameColumns(Kept, {{"a", "id"}}) '
        "in Renamed",
        encoding="utf-8",
    )
    return path


def _demo_csv(tmp_path: Path) -> Path:
    path = tmp_path / "data.csv"
    path.write_text("a,b\n1,x\n2,y\n3,z\n", encoding="utf-8")
    return path


def test_cli_eval_with_bind_replaces_the_connector_step(tmp_path: Path, capsys):
    query = _demo_query(tmp_path)
    data = _demo_csv(tmp_path)
    assert main(["eval", str(query), "--bind", f"Source={data}"]) == 0
    out = capsys.readouterr().out
    assert '"id": "1"' in out
    assert '"id": "3"' in out
    assert '"id": "2"' not in out


def test_cli_eval_without_bind_names_the_missing_file_and_creates_nothing(
    tmp_path: Path, capsys
):
    # The demo query points at a file that does not exist. Since local-file
    # connectors landed this is attempted rather than refused, so the contract
    # is now: fail with the path named, suggest --bind, and never bring the
    # file into existence as a side effect.
    query = _demo_query(tmp_path)
    assert main(["eval", str(query)]) == 2
    err = capsys.readouterr().err
    assert "ignored.csv" in err
    assert "--bind" in err
    assert not (tmp_path / "ignored.csv").exists()


def test_cli_eval_format_csv(tmp_path: Path, capsys):
    query = _demo_query(tmp_path)
    data = _demo_csv(tmp_path)
    assert (
        main(["eval", str(query), "--bind", f"Source={data}", "--format", "csv"]) == 0
    )
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "id,b"
    assert "1,x" in out
    assert "3,z" in out


def test_cli_eval_format_csv_requires_a_table(tmp_path: Path, capsys):
    path = tmp_path / "scalar.pq"
    path.write_text("1 + 1", encoding="utf-8")
    assert main(["eval", str(path), "--format", "csv"]) == 2
    assert "table" in capsys.readouterr().err


def test_cli_eval_bind_rejects_non_csv_json_extension(tmp_path: Path, capsys):
    query = tmp_path / "q.pq"
    query.write_text("let Source = 1 in Source", encoding="utf-8")
    bad = tmp_path / "data.txt"
    bad.write_text("x", encoding="utf-8")
    assert main(["eval", str(query), "--bind", f"Source={bad}"]) == 2
    assert ".csv or .json" in capsys.readouterr().err


def test_cli_eval_bind_json(tmp_path: Path, capsys):
    query = tmp_path / "q.pq"
    query.write_text("let Source = 1 in Table.RowCount(Source)", encoding="utf-8")
    data = tmp_path / "data.json"
    data.write_text('[{"a": 1}, {"a": 2}]', encoding="utf-8")
    assert main(["eval", str(query), "--bind", f"Source={data}"]) == 0
    assert capsys.readouterr().out.strip() == "2"


def test_cli_eval_bind_requires_name_equals_path(tmp_path: Path, capsys):
    query = tmp_path / "q.pq"
    query.write_text("1", encoding="utf-8")
    assert main(["eval", str(query), "--bind", "no-equals-sign"]) == 2
    assert "NAME=PATH" in capsys.readouterr().err


def test_cli_eval_member_picks_one_shared_member(tmp_path: Path, capsys):
    path = tmp_path / "section.pq"
    path.write_text(
        'section Section1; shared A = 1 + 1; shared B = "hi";', encoding="utf-8"
    )
    assert main(["eval", str(path), "--member", "A"]) == 0
    assert capsys.readouterr().out.strip() == "2"
    assert main(["eval", str(path), "--member", "B"]) == 0
    assert capsys.readouterr().out.strip() == '"hi"'


def test_cli_eval_member_missing_name_is_a_typed_error(tmp_path: Path, capsys):
    path = tmp_path / "section.pq"
    path.write_text("section Section1; shared A = 1;", encoding="utf-8")
    assert main(["eval", str(path), "--member", "Nope"]) == 2
    assert "no shared member named" in capsys.readouterr().err
