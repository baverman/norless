#!/usr/bin/env python2
import sys
import os.path

from norless.state import DBMStateFactory, connect

sqlite_state_path = sys.argv[1]
dbm_state_factory = DBMStateFactory(os.path.dirname(sqlite_state_path))

with connect(sqlite_state_path) as conn:
    for account, folder, uid, msgkey, flags, is_check in conn.execute("SELECT * from state"):
        dbm_state_factory.get(account, folder).put(uid, msgkey, flags, is_check)
