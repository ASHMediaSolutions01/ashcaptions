"""Tests for ash_captions.languages.glossary."""
from __future__ import annotations

from ash_captions.languages.glossary import (
    GlossaryEntry,
    apply_glossary,
    load_glossary,
    parse_glossary,
)


# -- parse_glossary -----------------------------------------------------


def test_parse_glossary_bare_term_maps_to_itself():
    entries = parse_glossary("Ash Media Solutions\n")
    assert entries == (GlossaryEntry("Ash Media Solutions", "Ash Media Solutions"),)


def test_parse_glossary_correction_pair():
    entries = parse_glossary("Gazi => Ghazi\n")
    assert entries == (GlossaryEntry("Gazi", "Ghazi"),)


def test_parse_glossary_ignores_blank_lines_and_comments():
    text = "\n# a comment\n\nAsh Captions\n   # another comment\n"
    entries = parse_glossary(text)
    assert entries == (GlossaryEntry("Ash Captions", "Ash Captions"),)


def test_parse_glossary_skips_malformed_correction_pairs_without_crashing():
    text = "wrong =>\n=> right\n=>\nGood Term\n"
    entries = parse_glossary(text)
    assert entries == (GlossaryEntry("Good Term", "Good Term"),)


def test_parse_glossary_strips_surrounding_whitespace():
    entries = parse_glossary("  Gazi  =>  Ghazi  \n")
    assert entries == (GlossaryEntry("Gazi", "Ghazi"),)


def test_parse_glossary_empty_text_returns_empty_tuple():
    assert parse_glossary("") == ()
    assert parse_glossary("\n\n  \n") == ()


# -- load_glossary --------------------------------------------------------


def test_load_glossary_missing_file_returns_empty_tuple(tmp_path):
    missing = tmp_path / "does-not-exist.txt"
    assert load_glossary(missing) == ()


def test_load_glossary_none_path_returns_empty_tuple():
    assert load_glossary(None) == ()
    assert load_glossary("") == ()


def test_load_glossary_directory_instead_of_file_returns_empty_tuple(tmp_path):
    assert load_glossary(tmp_path) == ()


def test_load_glossary_reads_valid_file(tmp_path):
    path = tmp_path / "glossary.txt"
    path.write_text("Gazi => Ghazi\nAsh Media Solutions\n", encoding="utf-8")
    entries = load_glossary(path)
    assert GlossaryEntry("Gazi", "Ghazi") in entries
    assert GlossaryEntry("Ash Media Solutions", "Ash Media Solutions") in entries


def test_load_glossary_bad_encoding_returns_empty_tuple_not_raise(tmp_path):
    path = tmp_path / "glossary.txt"
    # invalid UTF-8 byte sequence
    path.write_bytes(b"\xff\xfe\x00\x81broken")
    assert load_glossary(path) == ()


def test_load_glossary_accepts_str_path(tmp_path):
    path = tmp_path / "glossary.txt"
    path.write_text("Term\n", encoding="utf-8")
    assert load_glossary(str(path)) == (GlossaryEntry("Term", "Term"),)


# -- apply_glossary -------------------------------------------------------


def test_apply_glossary_correction_pair_is_case_insensitive():
    entries = (GlossaryEntry("gazi", "Ghazi"),)
    text, protected = apply_glossary("I spoke with GAZI yesterday.", entries)
    assert text == "I spoke with Ghazi yesterday."
    assert protected == frozenset({"Ghazi"})


def test_apply_glossary_forces_exact_casing_of_bare_term():
    entries = (GlossaryEntry("Ash Media Solutions", "Ash Media Solutions"),)
    text, protected = apply_glossary("welcome to ash media solutions today", entries)
    assert text == "welcome to Ash Media Solutions today"
    assert protected == frozenset({"Ash Media Solutions"})


def test_apply_glossary_is_word_boundary_aware():
    entries = (GlossaryEntry("cat", "CAT"),)
    text, _ = apply_glossary("The category is not a cat.", entries)
    assert text == "The category is not a CAT."


def test_apply_glossary_returns_unchanged_text_and_empty_protected_when_no_match():
    entries = (GlossaryEntry("nomatch", "NoMatch"),)
    text, protected = apply_glossary("nothing relevant here", entries)
    assert text == "nothing relevant here"
    assert protected == frozenset()


def test_apply_glossary_empty_entries_is_noop():
    text, protected = apply_glossary("some text", ())
    assert text == "some text"
    assert protected == frozenset()


def test_apply_glossary_empty_text_is_noop():
    entries = (GlossaryEntry("term", "Term"),)
    text, protected = apply_glossary("", entries)
    assert text == ""
    assert protected == frozenset()


def test_apply_glossary_prefers_longer_phrase_over_shorter_substring():
    entries = (
        GlossaryEntry("York", "YORK"),
        GlossaryEntry("New York", "New York City"),
    )
    text, protected = apply_glossary("I live in New York.", entries)
    assert text == "I live in New York City."
    assert protected == frozenset({"New York City"})


# -- compiled-pattern cache (review item 4) ---------------------------------


def test_apply_glossary_reuses_compiled_pattern_for_same_entries_object():
    from ash_captions.languages import glossary as module

    entries = (GlossaryEntry("gazi", "Ghazi"),)
    module._COMPILED.clear()
    apply_glossary("gazi one", entries)
    row = module._COMPILED[id(entries)]
    apply_glossary("gazi two", entries)
    assert module._COMPILED[id(entries)] is row
    assert len(module._COMPILED) == 1


def test_apply_glossary_cache_is_bounded():
    from ash_captions.languages import glossary as module

    module._COMPILED.clear()
    keep = [(GlossaryEntry(f"t{i}", f"T{i}"),) for i in range(module._COMPILED_MAX + 5)]
    for entries in keep:
        apply_glossary("x", entries)
    assert len(module._COMPILED) == module._COMPILED_MAX


def test_apply_glossary_per_word_is_fast_with_preloaded_entries():
    """15k words against a 200-term glossary must take a fraction of a
    second, not the 18s that a file read + regex build per word cost."""
    import time

    entries = tuple(GlossaryEntry(f"term{i}", f"Term{i}") for i in range(200))
    words = ["hello", "term7", "world", "term199"] * 3750
    start = time.perf_counter()
    for w in words:
        apply_glossary(w, entries)
    assert time.perf_counter() - start < 2.0


# -- per-client glossaries ----------------------------------------------


def test_client_slug_and_path():
    import pytest

    from ash_captions.languages.glossary import client_glossary_path, client_slug

    assert client_slug("Acme") == "acme"
    assert client_slug("  Acme   Corp ") == "acme-corp"
    assert client_glossary_path("C:/g", "Acme Corp").name == "acme-corp.txt"
    for bad in ("", "   ", "..", "a/b", "a\\b", "glossary", "GLOSSARY"):
        with pytest.raises(ValueError):
            client_glossary_path("C:/g", bad)


def test_merge_gives_the_client_the_last_word_on_the_same_match():
    from ash_captions.languages.glossary import merge_glossary_entries

    shared = (GlossaryEntry("gazi", "Ghazi"), GlossaryEntry("broker", "Agent"))
    client = (GlossaryEntry("Broker", "Brokerage"), GlossaryEntry("widget", "Acme Widget"))

    merged = merge_glossary_entries(shared, client)

    assert merged == (
        GlossaryEntry("gazi", "Ghazi"),
        GlossaryEntry("Broker", "Brokerage"),
        GlossaryEntry("widget", "Acme Widget"),
    )
    assert apply_glossary("the broker met gazi", merged)[0] == "the Brokerage met Ghazi"


def test_load_glossary_entries_for_merges_shared_and_client_files(tmp_path, caplog):
    from ash_captions.languages import load_glossary_entries_for

    (tmp_path / "glossary.txt").write_text("gazi => Ghazi\nbroker => Agent\n", encoding="utf-8")
    (tmp_path / "acme-corp.txt").write_text("broker => Brokerage\n", encoding="utf-8")

    with caplog.at_level("INFO", logger="ash_captions.languages.glossary"):
        acme = load_glossary_entries_for(tmp_path, "Acme Corp")
        nobody = load_glossary_entries_for(tmp_path, None)
        unknown = load_glossary_entries_for(tmp_path, "Globex")
        unusable = load_glossary_entries_for(tmp_path, "../x")

    assert apply_glossary("broker gazi", acme)[0] == "Brokerage Ghazi"
    assert apply_glossary("broker gazi", nobody)[0] == "Agent Ghazi"
    assert unknown == nobody and unusable == nobody
    assert "acme-corp.txt (1 entries)" in caplog.text
    assert "glossary.txt (2 entries)" in caplog.text
    assert "no client file for '../x'" in caplog.text


def test_load_glossary_entries_for_survives_a_missing_folder(tmp_path):
    from ash_captions.languages import load_glossary_entries_for

    assert load_glossary_entries_for(tmp_path / "nope", "Acme") == ()


def test_validate_glossary_text_names_the_broken_lines():
    from ash_captions.languages.glossary import validate_glossary_text

    assert validate_glossary_text("") == []
    assert validate_glossary_text("# c\n\nAcme\nGazi => Ghazi\n") == []
    problems = validate_glossary_text("ok => fine\nwrong =>\n=> right\n=>\na => b => c\n")
    assert [p.split(":")[0] for p in problems] == ["line 2", "line 3", "line 4", "line 5"]
    assert "nothing after" in problems[0]
    assert "nothing before" in problems[1]
    assert "either side" in problems[2]
    assert "more than one" in problems[3]
    # everything the validator passes, the parser keeps (and vice versa)
    text = "ok => fine\nwrong =>\nBare Term\n"
    assert len(parse_glossary(text)) == 2 and len(validate_glossary_text(text)) == 1
