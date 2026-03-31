import os.path
import sqlite3

from typing import NamedTuple


class Row(NamedTuple):
    uid: int
    msgkey: str
    flags: str
    is_check: bool


class Info(NamedTuple):
    fname: str
    msgid: str


def connect(fname: str) -> sqlite3.Connection:
    conn = sqlite3.connect(fname)
    conn.isolation_level = None
    conn.execute('pragma journal_mode=wal')
    conn.execute('pragma cache_size=-100000')
    conn.execute('pragma busy_timeout=10000')
    return conn


def create_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS state (
        fname text,
        msgid text,
        PRIMARY KEY (fname, msgid)
    )""")
    conn.commit()


class SqliteState:
    def __init__(self, maildir_path: str):
        fname = os.path.join(maildir_path, 'state.sqlite')
        self.conn = connect(fname)
        create_tables(self.conn)

    def by_fname(self, fname: str) -> Info | None:
        params = (fname,)
        rows = self.conn.execute(
            'SELECT fname, msgid FROM state WHERE fname=? LIMIT 1',
            params,
        ).fetchall()

        if rows:
            return Info(*rows[0])
        return None

    def by_msgid(self, msgid: str) -> Info | None:
        params = (msgid,)
        rows = self.conn.execute(
            'SELECT fname, msgid FROM state WHERE msgid=? LIMIT 1',
            params,
        ).fetchall()

        if rows:
            return Info(*rows[0])
        return None

    def getall(self) -> list[Info]:
        result = self.conn.execute('SELECT fname, msgid from state')
        return [Info(*r) for r in result]

    def put(self, fname: str, msgid: str) -> None:
        params = fname, msgid
        self.conn.execute('INSERT OR REPLACE INTO state (fname, msgid) VALUES (?, ?)', params)
        self.conn.commit()
