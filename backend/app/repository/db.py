import psycopg
from psycopg.rows import dict_row

from app import config

_conn: psycopg.Connection | None = None


def get_conn() -> psycopg.Connection:
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg.connect(**config.PG, row_factory=dict_row, autocommit=True)
    return _conn
