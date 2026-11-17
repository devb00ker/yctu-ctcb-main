import struct
from terminal.video import Reassembler, fragment


def test_big_endian_header():
    packets = fragment(b"abcd", 0x1234, 2)
    assert packets == [bytes.fromhex("1234000000000004")+b"ab", bytes.fromhex("1234000100000004")+b"cd"]


def test_duplicate_out_of_order_variable_fragments():
    r = Reassembler()
    packets = fragment(bytes(range(256))*13, 9, 512)
    assert r.push(packets[3], 0) == []
    assert r.push(packets[3], 0.01) == []
    output = []
    for i in [6, 5, 4, 2, 1, 0]:
        output += r.push(packets[i], 0.02)
    assert output == [bytes(range(256))*13]
    assert r.stats["duplicate"] == 1


def test_wraparound_missing_timeout_and_reorder_frames():
    r = Reassembler()
    assert r.push(fragment(b"a",65535)[0],0) == [b"a"]
    assert r.push(fragment(b"c",1)[0],0.01) == []
    assert r.push(fragment(b"b",0)[0],0.02) == [b"b", b"c"]
    assert r.push(fragment(b"e",3)[0],0.03) == []
    assert r.drain(0.2) == [b"e"]
    assert r.stats["missing"] == 1


def test_malformed_expired_and_bounded_memory():
    r = Reassembler(max_frames=2)
    for payload in (b"", b"12345678", struct.pack("!HHI",1,0,2**32-1)+b"x", bytes(1401)):
        assert r.push(payload,0) == []
    for i in range(10):
        assert r.push(fragment(b"abcdef",i,3)[0],0) == []
    assert len(r.frames) <= 2
    r.drain(1)
    assert not r.frames and r.stats["invalid"] == 4


def test_conflicting_duplicate_cannot_complete():
    r = Reassembler()
    p = fragment(b"abcdef",1,3)
    r.push(p[0],0)
    assert r.push(p[0][:-1]+b"Z",0.01) == []
    assert r.stats["invalid"] == 1
