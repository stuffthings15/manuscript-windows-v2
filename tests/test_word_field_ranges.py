from types import SimpleNamespace

from echo_link_core.insertion import _word_field_safe_edits


class Document:
    def __init__(self):
        self.text = "This is page \x13 PAGE \x141\x15 of the bundle .\rThe figures was wrong.\r"
        self.Fields = [SimpleNamespace(
            Code=SimpleNamespace(Start=14, End=20),
            Result=SimpleNamespace(Start=21, End=22, Text="1"),
        )]

    def Range(self, start, end):
        document = self

        class Range:
            @property
            def Text(self):
                return document.text[start:end].replace("\x13 PAGE \x141\x15", "1")

            @Text.setter
            def Text(self, value):
                document.text = document.text[:start] + value + document.text[end:]

        return Range()


def test_surrounding_correction_preserves_native_field():
    doc = Document()
    raw = doc.Range(0, len(doc.text)).Text
    end = raw.index("\r")
    edits = _word_field_safe_edits(doc, SimpleNamespace(Start=0), raw, 0, end,
                                   "This is page 1 of the bundle.")
    assert edits is not None
    for target, value in edits:
        target.Text = value
    assert doc.text == "This is page \x13 PAGE \x141\x15 of the bundle.\rThe figures was wrong.\r"


def test_correction_after_field_uses_native_coordinates():
    doc = Document()
    raw = doc.Range(0, len(doc.text)).Text
    start = raw.index("The figures")
    edits = _word_field_safe_edits(doc, SimpleNamespace(Start=0), raw, start, len(raw)-1,
                                   "The figures were wrong.")
    assert edits is not None
    for target, value in edits:
        target.Text = value
    assert doc.text.endswith("The figures were wrong.\r")
    assert "\x13 PAGE \x141\x15" in doc.text


def test_changed_field_result_refuses_all_edits():
    doc = Document()
    original = doc.text
    raw = doc.Range(0, len(doc.text)).Text
    assert _word_field_safe_edits(doc, SimpleNamespace(Start=0), raw, 0, len(raw),
                                  raw.replace("page 1", "page 2")) is None
    assert doc.text == original
