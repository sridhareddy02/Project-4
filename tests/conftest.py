import pytest

from mktg_copilot.data.warehouse import build_warehouse, read_sql
from mktg_copilot.engine import Engine


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("wh") / "warehouse.db"
    build_warehouse(path)
    return path


@pytest.fixture(scope="session")
def engine(db_path):
    return Engine(db_path)


@pytest.fixture(scope="session")
def raw(engine):
    return read_sql(engine.con, "SELECT * FROM marketing_daily")
