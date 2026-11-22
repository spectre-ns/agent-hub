"""The text transform under live co-editing: every pair of concurrent edits converges, and both are kept."""

import random

import pytest

from practicehub import ot


def ins(text, at, words):
    return ot.normalize([at, words, ot.u16len(text) - at])


def dele(text, at, n):
    return ot.normalize([at, -n, ot.u16len(text) - at - n])


def converge(text, a, b):
    a2, b2 = ot.transform(a, b)
    left = ot.apply(b2, ot.apply(a, text))
    right = ot.apply(a2, ot.apply(b, text))
    assert left == right
    return left


def test_apply_diff_and_normalize():
    assert ot.apply([5, "big ", -3, 10], "abcdeXYZfghijklmno") == "abcdebig fghijklmno"
    assert ot.normalize([1, 2, "a", "b", -1, -2, 0, ""]) == [3, "ab", -3]
    assert ot.normalize([2, -1, "x"]) == [2, "x", -1]  # an insert goes before the delete it touches
    for old, new in [("", "abc"), ("abc", ""), ("hello world", "hello brave world"), ("aaa", "aa"), ("ab", "ab"),
                     ("x🙂y", "x🙃y"), ("🙂", "")]:
        assert ot.apply(ot.diff(old, new), old) == new
    with pytest.raises(ot.OTError):
        ot.apply([3], "ab")
    with pytest.raises(ot.OTError):
        ot.normalize([1, None])


def test_two_people_typing_in_the_same_paragraph_both_survive():
    text = "The physician on call carries the phone."
    jo = ins(text, 4, "senior ")              # The senior physician ...
    noor = ins(text, ot.u16len(text) - 1, " at night")
    assert converge(text, jo, noor) == "The senior physician on call carries the phone at night."


def test_inserts_at_the_same_place_put_the_first_operation_first_on_both_sides():
    text = "ab"
    assert converge(text, ins(text, 1, "X"), ins(text, 1, "Y")) == "aXYb"
    assert converge(text, ins(text, 1, "Y"), ins(text, 1, "X")) == "aYXb"


def test_overlapping_deletes_and_an_insert_inside_a_deleted_range():
    text = "0123456789"
    assert converge(text, dele(text, 2, 5), dele(text, 4, 5)) == "019"
    assert converge(text, dele(text, 2, 6), ins(text, 4, "new")) == "01new89"
    assert converge(text, dele(text, 0, 10), dele(text, 0, 10)) == ""
    assert converge(text, ins(text, 10, "!"), dele(text, 0, 10)) == "!"


def test_compose_is_the_same_as_applying_in_turn():
    text = "hello world"
    a = ins(text, 5, ",")
    mid = ot.apply(a, text)
    b = dele(mid, 0, 1)
    assert ot.apply(ot.compose(a, b), text) == ot.apply(b, mid) == "ello, world"
    with pytest.raises(ot.OTError):
        ot.compose(a, [1])


def test_transform_index_moves_a_caret_with_the_text():
    text = "abcdef"
    assert ot.transform_index(ins(text, 1, "XX"), 3) == 5
    assert ot.transform_index(ins(text, 4, "XX"), 3) == 3
    assert ot.transform_index(dele(text, 0, 2), 3) == 1
    assert ot.transform_index(dele(text, 2, 3), 3) == 2
    assert ot.transform_index(ins(text, 3, "Z"), 3, before_insert=True) == 3


def _random_op(rng, text):
    """A random edit of text: a few inserts and deletes, anywhere (never inside a character)."""
    op, pos = [], 0
    while pos < len(text):
        r = rng.random()
        k = rng.randint(1, min(4, len(text) - pos))
        units = ot.u16len(text[pos:pos + k])
        if r < 0.5:
            op.append(units)
        elif r < 0.75:
            op.append(-units)
        else:
            op.append(rng.choice(["x", "yz", "🙂", "\n", " w "]))
            continue
        pos += k
    if rng.random() < 0.3:
        op.append(rng.choice(["end", "."]))
    return ot.normalize(op)


def test_random_concurrent_edits_always_converge():
    rng = random.Random(20261121)
    for _ in range(3000):
        text = "".join(rng.choice("ab c\n") for _ in range(rng.randint(0, 20)))
        a, b = _random_op(rng, text), _random_op(rng, text)
        converge(text, a, b)
        # compose agrees with applying in turn. (Transforming a composed operation can order a tie differently
        # from transforming its parts one by one; client and server always transform the same composed
        # operation, so they still agree: the three-client test below checks that.)
        a2 = _random_op(rng, ot.apply(a, text))
        assert ot.apply(ot.compose(a, a2), text) == ot.apply(a2, ot.apply(a, text))
        assert ot.diff(text, ot.apply(a, text)) and ot.apply(ot.diff(text, ot.apply(a, text)), text) == ot.apply(a, text) \
            or ot.apply(a, text) == text


def test_a_central_server_with_three_clients_converges():
    """Jupiter-style: each client has at most one operation in flight and buffers the rest; the server transforms
    each incoming operation against the history since the client's base. Every client ends on the server's text."""
    rng = random.Random(7)
    for _ in range(60):
        server = {"text": "start text", "log": []}
        clients = [{"text": server["text"], "base": 0, "inflight": None, "buffer": None, "seen": 0}
                   for _ in range(3)]

        def receive(c):
            while c["seen"] < len(server["log"]):
                who, op = server["log"][c["seen"]]
                c["seen"] += 1
                if who is c:  # our own, acknowledged
                    c["inflight"], c["buffer"] = c["buffer"], None
                    c["base"] = c["seen"]
                    if c["inflight"] is not None:
                        send(c)
                    continue
                if c["inflight"] is not None:
                    c["inflight"], op = ot.transform(c["inflight"], op)
                if c["buffer"] is not None:
                    c["buffer"], op = ot.transform(c["buffer"], op)
                c["text"] = ot.apply(op, c["text"])
                c["base"] = c["seen"]

        pending = []

        def send(c):
            pending.append((c, c["inflight"], c["base"]))

        def deliver():
            c, op, base = pending.pop(rng.randrange(len(pending)))
            for _, past in server["log"][base:]:
                op, _ = ot.transform(op, past)
            server["text"] = ot.apply(op, server["text"])
            server["log"].append((c, op))

        for _ in range(40):
            c = rng.choice(clients)
            r = rng.random()
            if r < 0.5:
                op = _random_op(rng, c["text"])
                c["text"] = ot.apply(op, c["text"])
                if c["inflight"] is None:
                    c["inflight"] = op
                    send(c)
                else:
                    c["buffer"] = op if c["buffer"] is None else ot.compose(c["buffer"], op)
            elif r < 0.8 and pending:
                deliver()
            else:
                receive(c)
        while pending or any(c["seen"] < len(server["log"]) for c in clients):
            while pending:
                deliver()
            for c in clients:
                receive(c)
        assert all(c["text"] == server["text"] for c in clients)
