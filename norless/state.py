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


class MessageInfo(NamedTuple):
    fname: str
    account: str
    folder: str
    uid: int
    msgid: str
    hash: str


def connect(fname: str) -> sqlite3.Connection:
    conn = sqlite3.connect(fname)
    conn.isolation_level = None
    conn.execute('pragma journal_mode=wal')
    conn.execute('pragma cache_size=-100000')
    conn.execute('pragma busy_timeout=10000')
    return conn


def create_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS folders (
        account text,
        folder text,
        uidvalidity integer,
        PRIMARY KEY (account, folder)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS messages (
        fname text PRIMARY KEY,
        account text,
        folder text,
        uid integer,
        msgid text,
        hash text
    )""")
    conn.execute(
        'CREATE INDEX IF NOT EXISTS messages_account_folder_uid_idx '
        'ON messages (account, folder, uid)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS messages_account_folder_hash_idx '
        'ON messages (account, folder, hash)'
    )
    conn.commit()


class SqliteState:
    def __init__(self, maildir_path: str):
        fname = os.path.join(maildir_path, 'state.sqlite')
        self.conn = connect(fname)
        create_tables(self.conn)

    def getall(self) -> list[MessageInfo]:
        result = self.conn.execute('SELECT fname, account, folder, uid, msgid, hash FROM messages')
        return [MessageInfo(*r) for r in result]

    def uidvalidity(self, account: str, folder: str) -> int | None:
        params = account, folder
        rows = self.conn.execute(
            'SELECT uidvalidity FROM folders WHERE account=? AND folder=? LIMIT 1',
            params,
        ).fetchall()
        if rows:
            return int(rows[0][0])
        return None

    def set_folder(self, account: str, folder: str, uidvalidity: int) -> None:
        params = account, folder, uidvalidity
        self.conn.execute(
            'INSERT OR REPLACE INTO folders (account, folder, uidvalidity) VALUES (?, ?, ?)',
            params,
        )
        self.conn.commit()

    def folder_messages(self, account: str, folder: str) -> list[MessageInfo]:
        params = account, folder
        result = self.conn.execute(
            'SELECT fname, account, folder, uid, msgid, hash FROM messages '
            'WHERE account=? AND folder=?',
            params,
        )
        return [MessageInfo(*r) for r in result]

    def by_uid(self, account: str, folder: str, uid: int) -> MessageInfo | None:
        params = account, folder, uid
        rows = self.conn.execute(
            'SELECT fname, account, folder, uid, msgid, hash FROM messages '
            'WHERE account=? AND folder=? AND uid=? LIMIT 1',
            params,
        ).fetchall()
        if rows:
            return MessageInfo(*rows[0])
        return None

    def max_uid(self, account: str, folder: str) -> int:
        params = account, folder
        rows = self.conn.execute(
            'SELECT MAX(uid) FROM messages WHERE account=? AND folder=?',
            params,
        ).fetchall()
        if rows and rows[0][0] is not None:
            return int(rows[0][0])
        return 0

    def by_msgid(self, account: str, folder: str, msgid: str) -> list[MessageInfo]:
        params = account, folder, msgid
        result = self.conn.execute(
            'SELECT fname, account, folder, uid, msgid, hash FROM messages '
            'WHERE account=? AND folder=? AND msgid=?',
            params,
        )
        return [MessageInfo(*r) for r in result]

    def by_message_fname(self, fname: str) -> MessageInfo | None:
        params = (fname,)
        rows = self.conn.execute(
            'SELECT fname, account, folder, uid, msgid, hash FROM messages WHERE fname=? LIMIT 1',
            params,
        ).fetchall()
        if rows:
            return MessageInfo(*rows[0])
        return None

    def put_message(
        self, fname: str, account: str, folder: str, uid: int, msgid: str, hash_value: str
    ) -> None:
        params = fname, account, folder, uid, msgid, hash_value
        self.conn.execute(
            'INSERT OR REPLACE INTO messages (fname, account, folder, uid, msgid, hash) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            params,
        )
        self.conn.commit()

    def reset_folder_messages(self, account: str, folder: str) -> None:
        params = account, folder
        self.conn.execute('UPDATE messages SET uid=-1 WHERE account=? AND folder=?', params)
        self.conn.commit()
