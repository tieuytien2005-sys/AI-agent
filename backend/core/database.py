import re
import os
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Union
import psycopg
from psycopg.rows import dict_row

from backend.core.config import DATABASE_URL


class TextClause:
    def __init__(self, sql: str):
        self.sql = sql

    def __str__(self):
        return self.sql


def text(sql: str) -> TextClause:
    """Tương thích cú pháp SQLAlchemy text(sql)."""
    return TextClause(sql)


def _clean_dsn(url: str) -> str:
    """Chuyển đổi URL SQLAlchemy sang chuỗi kết nối chuẩn của psycopg."""
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql://", 1)
    return url


def _format_sql_and_params(sql_input: Union[str, TextClause], params: Optional[Dict[str, Any]]) -> tuple[str, Optional[Dict[str, Any]]]:
    sql_str = str(sql_input)
    if not params:
        return sql_str, None
    # Giữ nguyên %(param)s và %%, tự động escape dấu % đơn lẻ trong LIKE khi params không rỗng
    formatted_sql = re.sub(
        r"%%|%\([a-zA-Z0-9_]+\)s|%",
        lambda m: "%%" if m.group(0) == "%" else m.group(0),
        sql_str,
    )
    return formatted_sql, params


class ExecutionResult:
    def __init__(self, cursor):
        self.cursor = cursor
        self.returns_rows = cursor.description is not None

    def fetchone(self):
        row = self.cursor.fetchone()
        if row is None:
            return None
        if isinstance(row, dict):
            values = list(row.values())
            return tuple(values)
        return row

    def fetchall(self):
        rows = self.cursor.fetchall()
        if not rows:
            return []
        if isinstance(rows[0], dict):
            return [tuple(r.values()) for r in rows]
        return rows

    def keys(self):
        if self.cursor.description:
            return [col.name for col in self.cursor.description]
        return []


class DatabaseSession:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql_input: Union[str, TextClause], params: Optional[Dict[str, Any]] = None) -> ExecutionResult:
        query, p = _format_sql_and_params(sql_input, params)
        cur = self.conn.cursor()
        cur.execute(query, p)
        return ExecutionResult(cur)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


@contextmanager
def get_db():
    """
    Context manager cung cấp DatabaseSession tương thích SQLAlchemy Session.
    Tự động quản lý commit/rollback và đóng kết nối.
    """
    dsn = _clean_dsn(DATABASE_URL)
    conn = psycopg.connect(dsn, row_factory=dict_row)
    session = DatabaseSession(conn)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def execute_sql(sql: Union[str, TextClause], params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    Thực thi truy vấn SQL thô với tham số an toàn (parameterized query)
    và trả về danh sách các bản ghi dạng dictionary.

    Quy tắc:
      1. Dùng %(param)s cho mọi tham số
      2. Dấu % trong LIKE phải viết %%
         Ví dụ: WHERE col LIKE '%%full%%'
      3. Không dùng :param (SQLAlchemy style)
    """
    query, p = _format_sql_and_params(sql, params)
    dsn = _clean_dsn(DATABASE_URL)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(query, p)
            if cur.description:
                return cur.fetchall()
            return []
