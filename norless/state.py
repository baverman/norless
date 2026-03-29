import os.path
import sqlite3
from dbm import gnu as gdbm

from .utils import nstr
from typing import Iterator, NamedTuple, Protocol


class Row(NamedTuple):
    uid: int
    msgkey: str
    flags: str
    is_check: bool


class Info(NamedTuple):
    fname: str
    msgid: str


class State(Protocol):
    def get(self, uid: int) -> Row | None: ...
    def put(self, uid: int, msgkey: str, flags: str, is_check: bool = False) -> None: ...
    def getall(self) -> Iterator[Row]: ...
    def get_maxuid(self) -> int: ...
    def get_minuid(self) -> int: ...
    def remove(self, uids: list[int]) -> None: ...


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

    def getall(self) -> list[Info]:
        result = self.conn.execute('SELECT fname, msgid from state')
        return [Info(*r) for r in result]

    def put(self, fname: str, msgid: str) -> None:
        params = fname, msgid
        self.conn.execute('INSERT OR REPLACE INTO state (fname, msgid) VALUES (?, ?)', params)
        self.conn.commit()

    # def get(self, uid: int) -> Row | None:
    #     params = self.account, self.folder, uid
    #     rows = self.conn.execute(
    #         """SELECT uid, msgkey, flags, is_check
    #         FROM state WHERE account=? and folder=? and uid=?""",
    #         params,
    #     )
    #
    #     result = [Row(*r) for r in rows]
    #     if result:
    #         if len(result) > 1:
    #             raise Exception('Too many rows')
    #         else:
    #             return result[0]
    #     else:
    #         return None
    #
    # def getall(self) -> Iterator[Row]:
    #     params = self.account, self.folder
    #     result = self.conn.execute(
    #         """SELECT uid, msgkey, flags, is_check
    #         FROM state WHERE account=? and folder=?""",
    #         params,
    #     )
    #
    #     return (Row(*r) for r in result)
    #
    # def put(self, uid: int, msgkey: str, flags: str, is_check: bool = False) -> None:
    #     params = self.account, self.folder, uid, msgkey, flags, int(is_check)
    #     self.conn.execute("""INSERT OR REPLACE INTO state VALUES (?, ?, ?, ?, ?, ?)""", params)
    #     self.conn.commit()
    #
    # def remove(self, uid: int) -> None:
    #     params = self.account, self.folder, uid
    #     self.conn.execute('DELETE FROM state WHERE account=? and folder=? and uid=?', params)
    #     self.conn.commit()


def parse_dbm_value(value: bytes) -> Row:
    uid, key, flags, is_check = nstr(value).split('\t')
    return Row(int(uid), key, flags, is_check == '1')


class DBMStateFactory:
    def __init__(self, state_dir: str, state_type: str = 'dbm') -> None:
        self.state_dir = state_dir
        self.state_type = state_type
        self._cache: dict[object, State] = {}

    def get(self, account: str, folder: str) -> 'State':
        key = account, folder
        try:
            return self._cache[key]
        except KeyError:
            pass

        if self.state_type == 'dbm':
            state = DBMState(self.state_dir, account, folder)
        else:
            raise Exception(f'Unknown state type: {self.state_type}')

        self._cache[key] = state
        return state


class DBMState:
    def __init__(self, state_dir: str, account: str, folder: str) -> None:
        folder = folder.replace('/', ':')
        self.path = os.path.join(state_dir, '{}-{}.db'.format(account, folder))
        self.db = gdbm.open(self.path, 'cf')

    def get(self, uid: int) -> Row | None:
        try:
            return parse_dbm_value(self.db[str(uid)])
        except KeyError:
            pass
        return None

    def _iteruids(self) -> Iterator[bytes]:
        db = self.db
        uid = db.firstkey()
        while uid is not None:
            yield uid
            uid = db.nextkey(uid)

    def getall(self) -> Iterator[Row]:
        db = self.db
        for uid in self._iteruids():
            yield parse_dbm_value(db[uid])

    def get_maxuid(self) -> int:
        try:
            return max(map(int, self._iteruids()))
        except ValueError:
            return 0

    def get_minuid(self) -> int:
        try:
            return min(map(int, self._iteruids()))
        except ValueError:
            return 0

    def put(self, uid: int, msgkey: str, flags: str, is_check: bool = False) -> None:
        self.db[str(uid)] = '{}\t{}\t{}\t{}'.format(
            uid, msgkey, flags or '', '1' if is_check else '0'
        ).encode()
        self.db.sync()

    def remove(self, uids: list[int]) -> None:
        for uid in uids:
            try:
                del self.db[str(uid)]
            except KeyError:
                pass

        self.db.sync()
