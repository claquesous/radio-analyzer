from analyzer.match import discogs_name, lucene_quote, normalize, similarity


def test_normalize_strips_case_punctuation_articles_and_parentheticals():
    assert normalize("The Beatles") == "beatles"
    assert normalize("Hey Jude (Remastered 2015)") == "hey jude"
    assert normalize("Simon & Garfunkel") == "simon and garfunkel"
    assert normalize("Beyoncé") == "beyonce"


def test_similarity():
    assert similarity("Love Will Tear Us Apart", "love will tear us apart") == 1.0
    assert similarity("Hey Jude", "Hey Jude (Remastered)") == 1.0
    assert similarity("Hey Jude", "Yesterday") < 0.5
    assert similarity(None, "x") == 0.0


def test_discogs_name_drops_disambiguation_suffix():
    assert discogs_name("John Smith (3)") == "John Smith"
    assert discogs_name("Prince") == "Prince"


def test_lucene_quote_escapes():
    assert lucene_quote('Say "Hi"') == '"Say \\"Hi\\""'
