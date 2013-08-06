import sqlite3

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

class State(object):
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
