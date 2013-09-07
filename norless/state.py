import os.path
import sqlite3
import gdbm

from collections import namedtuple

Row = namedtuple('Row', 'uid, msgkey, flags, is_check')

def connect(fname):
    conn = sqlite3.connect(fname)
    conn.text_factory = str
    return conn

def create_tables(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS state(
        account text,
        folder text,
        uid integer,
        msgkey text,
        flags text,
        is_check integer,
        UNIQUE(account, folder, uid, msgkey)
    )''')
    conn.commit()

class SqliteState(object):
    def __init__(self, conn, account, folder, write_lock):
        self.conn = conn
        self.account = account
        self.folder = folder
        self.write_lock = write_lock

    def get(self, uid):
        params = self.account, self.folder, uid
        result = self.conn.execute('''SELECT uid, msgkey, flags, is_check
            FROM state WHERE account=? and folder=? and uid=?''', params)

        result = [Row(*r) for r in result]
        if result:
            if len(result) > 1:
                raise Exception('Too many rows')
            else:
                return result[0]
        else:
            return None

    def getall(self):
        params = self.account, self.folder
        result = self.conn.execute('''SELECT uid, msgkey, flags, is_check
            FROM state WHERE account=? and folder=?''', params)

        return (Row(*r) for r in result)

    def put(self, uid, msgkey, flags, is_check=0):
        params = self.account, self.folder, uid, msgkey, flags, is_check
        with self.write_lock:
            self.conn.execute('''INSERT OR REPLACE INTO state VALUES (?, ?, ?, ?, ?, ?)''', params)
            self.conn.commit()

    def remove(self, uid):
        params = self.account, self.folder, uid
        with self.write_lock:
            self.conn.execute('DELETE FROM state WHERE account=? and folder=? and uid=?', params)
            self.conn.commit()


def parse_dbm_value(value):
    uid, key, flags, is_check = value.split('\t')
    return Row(int(uid), key, flags, is_check == '1')


class DBMStateFactory(object):
    def __init__(self, state_dir):
        self.state_dir = state_dir
        self._cache = {}

    def get(self, account, folder):
        key = account, folder
        try:
            return self._cache[key]
        except KeyError:
            pass

        state = self._cache[key] = DBMState(self.state_dir, account, folder)
        return state
        

class DBMState(object):
    def __init__(self, state_dir, account, folder):
        folder = folder.replace('/', ':')
        self.path = os.path.join(state_dir, '{}-{}.db'.format(account, folder))
        self.db = gdbm.open(self.path , 'cf')

    def get(self, uid):
        try:
            return parse_dbm_value(self.db[str(uid)])
        except KeyError:
            pass

    def _iteruids(self):
        db = self.db
        uid = db.firstkey()
        while uid != None:
            yield uid
            uid = db.nextkey(uid)

    def getall(self):
        db = self.db
        for uid in self._iteruids():
            yield parse_dbm_value(db[uid])

    def get_maxuid(self):
        try:
            return max(map(int, self._iteruids()))
        except ValueError:
            return 0

    def get_minuid(self):
        try:
            return min(map(int, self._iteruids()))
        except ValueError:
            return 0

    def put(self, uid, msgkey, flags, is_check=0):
        self.db[str(uid)] = '{}\t{}\t{}\t{}'.format(uid, msgkey, flags or '',
            '1' if is_check else '0')
        self.db.sync()

    def remove(self, uid, sync=True):
        try:
            del self.db[str(uid)]
            if sync:
                self.db.sync()
        except KeyError:
            pass

    def remove_many(self, uids):
        for uid in uids:
            self.remove(uid, False)

        self.db.sync()
