import sys

import propfirm


def test_package_imports() -> None:
    assert propfirm.__version__ == "0.1.0"


def test_runs_on_python_3_15() -> None:
    assert sys.version_info[:2] == (3, 15)
