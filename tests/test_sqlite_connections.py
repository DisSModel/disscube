"""The catalog must not leave SQLite connections open (they showed up as ResourceWarning and, at interpreter
exit, as `Error in sys.excepthook` after `disscube run`)."""
import gc
import sqlite3
import warnings

from disscube.catalog.sqlite_store import SqliteCatalogStore


def test_connection_is_closed_after_use(tmp_path):
    store = SqliteCatalogStore(tmp_path / "catalog.db")
    with store._get_connection() as conn:
        conn.execute("SELECT 1")
    try:
        conn.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        pass
    else:
        raise AssertionError("the connection is still open after leaving the with block")


def test_no_unclosed_database_warning(tmp_path):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        store = SqliteCatalogStore(tmp_path / "catalog.db")
        for _ in range(5):
            with store._get_connection() as conn:
                conn.execute("SELECT 1")
        gc.collect()
    assert not [w for w in caught if "unclosed database" in str(w.message)]
