from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def tmp_data(tmp_path: Path) -> Path:
    return tmp_path
