from analyzer.identify import best_recording

SONG = {"title": "Atmosphere", "artist_name": "Joy Division", "album_title": "Substance"}


def rec(id_, title, artist, groups=()):
    return {"id": id_, "title": title, "artists": [{"name": artist}],
            "releasegroups": [{"title": g} for g in groups]}


def test_prefers_recording_on_our_album():
    candidates = [(0.95, rec("single", "Atmosphere", "Joy Division", ["Atmosphere"])),
                  (0.95, rec("comp", "Atmosphere", "Joy Division", ["Substance"]))]
    assert best_recording(SONG, candidates) == "comp"


def test_rejects_recordings_whose_names_disagree():
    candidates = [(0.99, rec("cover", "Atmosphere", "Russ Abbot")), (0.99, rec("x", "Ceremony", "Joy Division"))]
    assert best_recording(SONG, candidates) is None


def test_skips_recordings_without_metadata():
    assert best_recording(SONG, [(0.9, {"id": "bare"})]) is None


def test_acoustid_errors_are_raised_not_treated_as_misses(monkeypatch):
    import pytest
    from analyzer import identify

    monkeypatch.setattr(identify.config, "ACOUSTID_API_KEY", "bad")
    monkeypatch.setattr(identify.acoustid, "fingerprint_file", lambda *a, **k: (200, "fp"))
    monkeypatch.setattr(identify.acoustid, "lookup", lambda *a, **k: {"status": "error", "error": {"message": "invalid API key"}})
    with pytest.raises(identify.AcoustIDError, match="invalid API key"):
        identify.lookup_fingerprint({"path": "/x.mp3"})


def test_search_skips_live_demo_and_video_versions():
    from analyzer.identify import is_unwanted_variant
    assert is_unwanted_variant("Teenage Dirtbag", {"title": "Teenage Dirtbag", "disambiguation": "music video"})
    assert is_unwanted_variant("You", {"title": "You", "disambiguation": "demo"})
    assert not is_unwanted_variant("You", {"title": "You", "disambiguation": "album version"})
    assert not is_unwanted_variant("Turn My Head (Live)", {"title": "Turn My Head", "disambiguation": "live, 1997"})


def test_accepts_featured_artist_credits():
    song = {"title": "Somebody That I Used To Know", "artist_name": "Gotye", "album_title": "Making Mirrors"}
    candidates = [(0.99, rec("cover", "Somebody That I Used to Know (Tribute to Gotye feat. Kimbra)", "Cover Nation")),
                  (0.99, {"id": "real", "title": "Somebody That I Used to Know",
                          "artists": [{"name": "Gotye"}, {"name": "Kimbra"}], "releasegroups": []})]
    assert best_recording(song, candidates) == "real"


def test_prefers_original_over_remaster_with_same_fingerprint():
    song = {"title": "What If", "artist_name": "Creed", "album_title": "Human Clay"}
    candidates = [(0.98, rec("remaster", "What If (remastered 2024)", "Creed", ["Human Clay"])),
                  (0.98, rec("original", "What If", "Creed", ["Human Clay"]))]
    assert best_recording(song, candidates) == "original"


def test_falls_back_to_remaster_when_it_is_the_only_match():
    song = {"title": "What If", "artist_name": "Creed", "album_title": "Human Clay"}
    assert best_recording(song, [(0.98, rec("remaster", "What If (remastered 2024)", "Creed"))]) == "remaster"


def test_prefers_the_widely_released_recording_on_a_near_tie():
    song = {"title": "The Scientist", "artist_name": "Coldplay", "album_title": "A Rush of Blood to the Head"}
    atmos = rec("atmos", "The Scientist", "Coldplay", ["A Rush of Blood to the Head"])
    original = rec("original", "The Scientist", "Coldplay",
                   ["A Rush of Blood to the Head", "The Scientist", "Parachutes / Rush", "Live 2003", "Hits"])
    assert best_recording(song, [(0.98, atmos), (0.97, original)]) == "original"


def test_album_version_is_not_treated_as_a_variant():
    from analyzer.identify import is_unwanted_variant
    assert not is_unwanted_variant("You", {"title": "You", "disambiguation": "album version"})


def test_relaxed_pass_accepts_strong_fingerprints_with_differing_names():
    from analyzer.identify import relaxed_recording

    def r(id_, title, artist, duration):
        return {"id": id_, "title": title, "artists": [{"name": artist}], "duration": duration}

    aerials = {"title": "Aerials", "artist_name": "System of a Down", "album_title": "Toxicity"}
    candidates = [(0.993, {"id": "cover", "title": "Aerials", "artists": [{"name": "Bruno Hrabovsky"}]}),
                  (0.993, r("real", "Aerials / Der Voghormia", "System of a Down", 371.04)),
                  (0.993, r("hidden", "Der Voghormia", "System of a Down", 133.6))]
    assert best_recording(aerials, candidates) is None
    assert relaxed_recording(aerials, candidates, 371) == "real"

    bent = {"title": "Bent", "artist_name": "Matchbox 20", "album_title": "Mad Season"}
    candidates = [(0.984, r("fade", "Bent (early fade)", "Matchbox Twenty", 239.0)),
                  (0.984, r("album", "Bent", "Matchbox Twenty", 256.1))]
    assert relaxed_recording(bent, candidates, 256) == "album"

    world = {"title": "World", "artist_name": "Sick Puppies", "album_title": None}
    assert relaxed_recording(world, [(0.968, r("my", "My World", "Sick Puppies", 239.0))], 239) == "my"


def test_relaxed_pass_still_rejects_weak_fingerprints_and_wrong_durations():
    from analyzer.identify import relaxed_recording
    song = {"title": "World", "artist_name": "Sick Puppies", "album_title": None}
    rec_ = {"id": "x", "title": "My World", "artists": [{"name": "Sick Puppies"}], "duration": 239.0}
    assert relaxed_recording(song, [(0.80, rec_)], 239) is None
    assert relaxed_recording(song, [(0.99, rec_)], 260) is None


def test_featured_artist_in_our_tag():
    from analyzer.match import artist_similarity
    assert artist_similarity("Bad Omens f./Poppy", ["Bad Omens", "Poppy"]) == 1.0
    assert artist_similarity("Noah Kahan f./Post Malone", ["Noah Kahan"]) == 1.0
