import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cqs.store import WorkStore  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    st = WorkStore.create(tmp_path / "w.db", slug="w", title="テスト作品")
    yield st
    st.close()
