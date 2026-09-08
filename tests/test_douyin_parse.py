"""Regression tests for douyin friend-row parsing.

Fixtures mirror real Douyin chat-page DOM shapes, including the Docker report
(#1): rows where the streak days are a bare number ("侯 | 392 | 11:25") with
no "天" suffix and no fire emoji in text - the signal lives in the screen
reader label or a spark class. The old parser returned 0 friends for these.

Run: python3 -m pytest tests -q  (or)  python3 tests/test_douyin_parse.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from keeper.douyin import parse_rows  # noqa: E402


def test_aria_label_with_spark():
    rows = [{"lines": ["侯", "11:25", "[早上好]"], "aria": "侯，火花392天", "title": "", "cls": ""}]
    out = parse_rows(rows)
    assert len(out) == 1
    assert out[0]["name"] == "侯" and out[0]["spark"] == 392


def test_bare_number_with_spark_class():
    # issue #1 diagnostic: "侯 | 392 | 11:25 | [早上好]"
    rows = [{"lines": ["侯", "392", "11:25", "[早上好]"], "aria": "", "title": "",
             "cls": "conversation-item spark-badge"}]
    out = parse_rows(rows)
    assert len(out) == 1
    assert out[0]["name"] == "侯" and out[0]["spark"] == 392


def test_days_text_with_fire_in_row():
    rows = [{"lines": ["小李", "火花 12天", "昨天 20:37"], "aria": "", "title": "", "cls": "chat-item"}]
    out = parse_rows(rows)
    assert len(out) == 1
    assert out[0]["name"] == "小李" and out[0]["spark"] == 12


def test_no_fire_signal_is_excluded():
    rows = [{"lines": ["宝宝巴", "358", "昨天 16:54"], "aria": "", "title": "", "cls": "chat-item"}]
    assert parse_rows(rows) == []


def test_bare_number_without_fire_signal_is_excluded():
    # 392 could be an unread badge; never guess without a fire signal
    rows = [{"lines": ["侯", "392", "11:25"], "aria": "", "title": "", "cls": "chat-item"}]
    assert parse_rows(rows) == []


def test_title_attribute_signal():
    rows = [{"lines": ["阿伟", "5"], "aria": "", "title": "火花5天", "cls": ""}]
    out = parse_rows(rows)
    assert len(out) == 1
    assert out[0]["name"] == "阿伟" and out[0]["spark"] == 5


def test_name_from_aria_when_lines_are_noisy():
    rows = [{"lines": ["分享[视频]", "昨天 20:37"], "aria": "李：火花88天", "title": "", "cls": ""}]
    out = parse_rows(rows)
    assert len(out) == 1
    assert out[0]["name"] == "李" and out[0]["spark"] == 88


def test_time_and_message_lines_never_become_names():
    rows = [{"lines": ["11:25", "[早上好]", "火花 30天"], "aria": "王姐，火花30天", "title": "", "cls": ""}]
    out = parse_rows(rows)
    assert len(out) == 1
    assert out[0]["name"] == "王姐"


def test_duplicates_deduplicated():
    rows = [
        {"lines": ["侯", "392"], "aria": "侯，火花392天", "title": "", "cls": ""},
        {"lines": ["侯", "392"], "aria": "侯，火花392天", "title": "", "cls": ""},
    ]
    assert len(parse_rows(rows)) == 1


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"{len(fns)} tests passed")
