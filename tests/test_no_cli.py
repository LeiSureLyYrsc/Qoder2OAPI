from pathlib import Path


def test_no_cli_or_subprocess_in_src():
    src_dir = Path(__file__).parent.parent / "src"
    py_files = list(src_dir.rglob("*.py"))
    assert len(py_files) > 0

    forbidden = ["subprocess", "qodercn", "qoderclicn", "popen"]

    for f in py_files:
        content = f.read_text(encoding="utf-8").lower()
        for term in forbidden:
            assert term not in content, f"Found forbidden term '{term}' in {f.name}"
