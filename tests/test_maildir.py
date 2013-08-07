from email.mime.text import MIMEText
from norless.maildir import Maildir

def test_dir_create(tmpdir):
    path = tmpdir.join('inbox')
    md = Maildir(path.strpath)
    
    assert path.check()
    assert path.stat().mode & 0777 == 0700

    for p in ('new', 'cur', 'tmp'):
        pp = path.join(p)
        assert pp.check()
        assert pp.stat().mode & 0777 == 0700

def test_adding_unseen_message(tmpdir):
    path = tmpdir.join('inbox')
    md = Maildir(path.strpath)

    msgkey = md.add('msg')
    msgpath = path.join('new').join(msgkey)
    assert msgpath.check()
    assert msgpath.read() == 'msg'
    assert msgpath.stat().mode & 0777 == 0600
    assert not path.join('tmp').listdir()

    assert md.get_flags(msgkey) == ''
    assert msgkey in md
    md._invalidate()
    assert md.get_flags(msgkey) == ''

def test_adding_seen_message(tmpdir):
    path = tmpdir.join('inbox')
    md = Maildir(path.strpath)

    msgkey = md.add('msg', 'S')
    msgpath = path.join('cur').join(msgkey + ':2,S')
    assert msgpath.check()
    assert msgpath.read() == 'msg'
    assert msgpath.stat().mode & 0777 == 0600
    assert not path.join('tmp').listdir()

    assert md.get_flags(msgkey) == 'S'
    md._invalidate()
    assert md.get_flags(msgkey) == 'S'

def test_adding_message_object(tmpdir):
    path = tmpdir.join('inbox')
    md = Maildir(path.strpath)

    msgkey = md.add(MIMEText('boo'))
    msgpath = path.join('new').join(msgkey)
    assert 'boo' in msgpath.read()

def test_message_discard(tmpdir):
    path = tmpdir.join('inbox')
    md = Maildir(path.strpath)

    md.discard('garbage')

    msgkey = md.add('boo')
    msgpath = path.join('new').join(msgkey)
    msgpath.remove()
    assert not msgpath.check() 
    md.discard(msgkey)
    assert msgkey not in md._toc

    msgkey = md.add('boo')
    msgpath = path.join('new').join(msgkey)
    assert msgpath.check()
    md.discard(msgkey)
    assert not msgpath.check() 
    assert msgkey not in md._toc

    msgkey = md.add('boo')
    msgpath = path.join('new').join(msgkey)
    assert msgpath.check()
    md._invalidate()
    md.discard(msgkey)
    assert msgkey not in md._toc
    assert not msgpath.check() 

    msgkey = md.add('boo', 'S')
    msgpath = path.join('cur').join(msgkey + ':2,S')
    assert msgpath.check()
    md.discard(msgkey)
    assert msgkey not in md._toc
    assert not msgpath.check() 

    msgkey = md.add('boo', 'S')
    msgpath = path.join('cur').join(msgkey + ':2,S')
    assert msgpath.check()
    md._invalidate()
    md.discard(msgkey)
    assert msgkey not in md._toc
    assert not msgpath.check() 

def test_iterflags(tmpdir):
    path = tmpdir.join('inbox')
    md = Maildir(path.strpath)
    
    k1 = md.add('boo')
    k2 = md.add('boo', 'S')
    k3 = md.add('boo', 'SF')

    result = set(md.iterflags())
    assert result == set([(k1, ''), (k2, 'S'), (k3, 'SF')])

def test_add_flags(tmpdir):
    path = tmpdir.join('inbox')
    md = Maildir(path.strpath)

    key = md.add('boo')
    md.add_flags(key, 'S')
    assert not path.join('new').join(key).check()
    assert path.join('cur').join(key + ':2,S').check()
    assert md.get_flags(key) == 'S'
    md._invalidate()
    assert md.get_flags(key) == 'S'

def test_set_flags(tmpdir):
    path = tmpdir.join('inbox')
    md = Maildir(path.strpath)

    key = md.add('boo', 'R')
    md.set_flags(key, 'S')
    assert not path.join('new').join(key + ':2,R').check()
    assert path.join('cur').join(key + ':2,S').check()
    assert md.get_flags(key) == 'S'
    md._invalidate()
    assert md.get_flags(key) == 'S'
