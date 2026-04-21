import pytest
from sansproto import Collector
from norless.imap_client import Proto, Response, Select, Status, proto


class FakeSocket:
    def __init__(self, chunks: list[bytes]):
        self._chunks = iter(chunks)
        self.sent: list[bytes] = []

    def recv(self, size: int) -> bytes:
        return next(self._chunks)

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)


def test_literals():
    c = Collector(proto)

    result = c.send(b'* LIST {5}\r\n12345\r\n')
    assert result == [[b'*', b'LIST', b'12345']]

    result = c.send(b'* LIST {5}\r\n12345foo\r\n')
    assert result == [[b'*', b'LIST', b'12345', b'foo']]

    result = c.send(b'* LIST {5}\r\n12345"fo\\\\\\"o"\r\n')
    assert result == [[b'*', b'LIST', b'12345', b'fo\\"o']]


def test_responses():
    c = Collector(proto)

    result = c.send(b'+\r\n')
    assert result == [[b'+']]

    result = c.send(b'+ \r\n')
    assert result == [[b'+']]

    result = c.send(b'+ boo {5}\r\n')
    assert result == [[b'+', b'boo {5}']]

    result = c.send(b'* OK boo {5}\r\n')
    assert result == [[b'*', b'OK', [], b'boo {5}']]


def test_status_response_codes():
    c = Collector(proto)

    result = c.send(b'* OK [UIDVALIDITY 2] UIDs valid.\r\n')
    assert result == [[b'*', b'OK', [b'UIDVALIDITY', b'2'], b'UIDs valid.']]

    result = c.send(b'* OK [PERMANENTFLAGS ( \\Seen \\Deleted \\* ) ] Flags permitted.\r\n')
    assert result == [
        [b'*', b'OK', [b'PERMANENTFLAGS', b'( \\Seen \\Deleted \\* ) '], b'Flags permitted.']
    ]

    result = c.send(b'A1 OK [READ-WRITE] SELECT completed\r\n')
    assert result == [[b'A1', b'OK', [b'READ-WRITE'], b'SELECT completed']]

    result = c.send(b'* OK plain text only\r\n')
    assert result == [[b'*', b'OK', [], b'plain text only']]


def test_nested_lists():
    c = Collector(proto)

    result = c.send(b'* LIST (\\HasNoChildren) "/" "INBOX"\r\n')
    assert result == [[b'*', b'LIST', [b'\\HasNoChildren'], b'/', b'INBOX']]

    result = c.send(b'* 1 FETCH (UID 9 FLAGS (\\Seen))\r\n')
    assert result == [[b'*', b'1', b'FETCH', [b'UID', b'9', b'FLAGS', [b'\\Seen']]]]


def test_literal_inside_nested_list():
    c = Collector(proto)

    result = c.send(b'* 1 FETCH (BODY[HEADER] {5}\r\n12345 FLAGS (\\Seen))\r\n')
    assert result == [[b'*', b'1', b'FETCH', [b'BODY[HEADER]', b'12345', b'FLAGS', [b'\\Seen']]]]


def test_wait_response_returns_greeting():
    p = Proto()

    assert p.wait_response(b'* OK hi\r\n', b'*') == Response(
        [], Status(b'*', b'OK', b'', b'', b'hi')
    )


def test_wait_response_collects_untagged_before_tagged_completion():
    p = Proto()
    assert p.command('LIST', (b'""', b'*')) == b'A0 LIST "" *\r\n'

    assert p.wait_response(b'* LIST () "/" "INBOX"\r\nA0 OK done\r\n') == Response(
        [[b'*', b'LIST', [], b'/', b'INBOX']],
        Status(b'A0', b'OK', b'', b'', b'done'),
    )


def test_wait_response_returns_continuation_after_untagged_data():
    p = Proto()
    assert p.command('AUTHENTICATE', (b'XOAUTH2',)) == b'A0 AUTHENTICATE XOAUTH2\r\n'

    assert p.wait_response(b'* 1 EXISTS\r\n+ \r\n', b'+', status=False) == Response(
        [[b'*', b'1', b'EXISTS']],
        Status(b'+', b'', b'', b'', b''),
    )


def test_wait_response_raises_on_current_tagged_failure_while_waiting_continuation():
    p = Proto()
    p.command('AUTHENTICATE', (b'XOAUTH2',))

    with pytest.raises(ValueError, match='auth failed'):
        p.wait_response(b'A0 NO auth failed\r\n', b'+', status=False)


def test_wait_response_keeps_untagged_no_as_collected_response():
    p = Proto()
    p.command('LIST', (b'""', b'*'))

    assert p.wait_response(b'* NO harmless\r\nA0 OK done\r\n') == Response(
        [[b'*', b'NO', [], b'harmless']],
        Status(b'A0', b'OK', b'', b'', b'done'),
    )


def test_wait_response_raises_on_untagged_bye():
    p = Proto()
    p.command('LIST', (b'""', b'*'))

    with pytest.raises(ValueError, match='closing'):
        p.wait_response(b'* BYE closing\r\n')


def test_collect_list_result():
    p = Proto()
    p.command('LIST', (b'""', b'*'))
    response = p.wait_response(b'* LIST () "/" "INBOX"\r\nA0 OK done\r\n')

    assert response is not None
    assert response.status == Status(b'A0', b'OK', b'', b'', b'done')
    assert p.collect_list(response) == [([], b'/', b'INBOX')]


def test_collect_search_result():
    p = Proto()
    p.command('UID', (b'SEARCH', b'(UID 10:*)'))
    response = p.wait_response(b'* SEARCH 12 15 20\r\nA0 OK SEARCH completed\r\n')

    assert response is not None
    assert p.collect_search(response) == [12, 15, 20]


def test_collect_search_empty_result():
    p = Proto()
    p.command('UID', (b'SEARCH', b'(UID 10:*)'))
    response = p.wait_response(b'* SEARCH\r\nA0 OK SEARCH completed\r\n')

    assert response is not None
    assert p.collect_search(response) == []


def test_collect_search_ignores_unrelated_untagged_data():
    p = Proto()
    p.command('UID', (b'SEARCH', b'(UID 10:*)'))
    response = p.wait_response(
        b'* 42 EXISTS\r\n'
        b'* OK [UIDNEXT 99] Predicted next UID.\r\n'
        b'* SEARCH 50\r\n'
        b'A0 OK SEARCH completed\r\n'
    )

    assert response is not None
    assert p.collect_search(response) == [50]


def test_collect_fetch_header_fields_response():
    p = Proto()
    p.command('UID FETCH', (b'1', b'(UID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID DATE FROM TO SUBJECT)])'))
    response = p.wait_response(
        b'* 1 FETCH (UID 123 BODY[HEADER.FIELDS (MESSAGE-ID DATE FROM TO SUBJECT)] {5}\r\n'
        b'hello)\r\n'
        b'A0 OK FETCH completed\r\n'
    )

    assert response is not None
    assert p.collect_pairs('FETCH', response, cmd_idx=2) == [
        {
            'UID': b'123',
            'BODY[HEADER.FIELDS ( MESSAGE-ID DATE FROM TO SUBJECT ) ]': b'hello',
            'BODY': b'hello',
        }
    ]


def test_collect_fetch_multiple_body_sections_response():
    p = Proto()
    p.command('UID FETCH', (b'1', b'(BODY.PEEK[HEADER] BODY.PEEK[TEXT])'))
    response = p.wait_response(
        b'* 1 FETCH (BODY[HEADER] {3}\r\n'
        b'hdr BODY[TEXT] {4}\r\n'
        b'text)\r\n'
        b'A0 OK FETCH completed\r\n'
    )

    assert response is not None
    assert p.collect_pairs('FETCH', response, cmd_idx=2) == [
        {
            'BODY[HEADER]': b'hdr',
            'BODY[TEXT]': b'text',
            'BODY': b'text',
        }
    ]


def test_collect_select_result():
    p = Proto()
    p.command('SELECT', (b'INBOX',))
    response = p.wait_response(
        b'* FLAGS (\\Answered \\Flagged \\Draft \\Deleted \\Seen $Junk $NotPhishing $Phishing Old)\r\n'
        b'* OK [PERMANENTFLAGS ( \\Answered \\Flagged \\Draft \\Deleted \\Seen $Junk $NotPhishing $Phishing Old \\* ) ] Flags permitted.\r\n'
        b'* OK [UIDVALIDITY 2] UIDs valid.\r\n'
        b'* 5318 EXISTS\r\n'
        b'* 0 RECENT\r\n'
        b'* OK [UIDNEXT 49504] Predicted next UID.\r\n'
        b'* OK [HIGHESTMODSEQ 7047630]\r\n'
        b'A0 OK [READ-WRITE] SELECT completed\r\n'
    )

    assert response is not None
    assert p.collect_select(response) == Select(
        flags=[
            b'\\Answered',
            b'\\Flagged',
            b'\\Draft',
            b'\\Deleted',
            b'\\Seen',
            b'$Junk',
            b'$NotPhishing',
            b'$Phishing',
            b'Old',
        ],
        exists=5318,
        recent=0,
        uidvalidity=2,
        uidnext=49504,
        permanentflags=[
            b'\\Answered',
            b'\\Flagged',
            b'\\Draft',
            b'\\Deleted',
            b'\\Seen',
            b'$Junk',
            b'$NotPhishing',
            b'$Phishing',
            b'Old',
            b'\\*',
        ],
        highestmodseq=7047630,
        readonly=False,
    )


def test_collect_select_readonly():
    p = Proto()
    p.command('EXAMINE', (b'INBOX',))
    response = p.wait_response(
        b'* FLAGS (\\Seen)\r\n'
        b'* OK [UIDVALIDITY 2] UIDs valid.\r\n'
        b'* 1 EXISTS\r\n'
        b'* 0 RECENT\r\n'
        b'* OK [UIDNEXT 3] Predicted next UID.\r\n'
        b'A0 OK [READ-ONLY] EXAMINE completed\r\n'
    )

    assert response is not None
    assert p.collect_select(response).readonly is True


def test_client_login_quotes_credentials():
    from norless.imap_client import Client

    sock = FakeSocket([
        b'* OK hi\r\n',
        b'A0 OK LOGIN completed\r\n',
    ])
    client = Client(sock)  # type: ignore[arg-type]

    client.login(b'user"name', b'pa\\ss')

    assert sock.sent == [b'A0 LOGIN "user\\"name" "pa\\\\ss"\r\n']


def test_client_uid_store_collects_fetch_updates():
    from norless.imap_client import Client

    sock = FakeSocket([
        b'* OK hi\r\n',
        b'* 1 FETCH (UID 123 FLAGS (\\Seen))\r\nA0 OK STORE completed\r\n',
    ])
    client = Client(sock)  # type: ignore[arg-type]

    result = client.store(b'123', b'+FLAGS', b'(\\Seen)', uid=True)

    assert result == [{'UID': b'123', 'FLAGS': [b'\\Seen']}]
