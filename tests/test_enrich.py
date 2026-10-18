from analyzer.enrich import applies_to_track, parse_year, pick_release_group


def group(id_, title, primary="Album", secondary=(), date=None):
    return {"release-group": {"id": id_, "title": title, "primary-type": primary,
                              "secondary-types": list(secondary), "first-release-date": date}}


def test_pick_release_group_prefers_our_album_title():
    releases = [group("hits", "Greatest Hits", secondary=["Compilation"], date="1990"),
                group("orig", "Unknown Pleasures", date="1979-06-15")]
    assert pick_release_group({"album_title": "Unknown Pleasures"}, releases)["id"] == "orig"


def test_pick_release_group_falls_back_to_earliest_studio_album():
    releases = [group("live", "Live", secondary=["Live"], date="1975"),
                group("later", "Second", date="1985"),
                group("first", "First", date="1980")]
    assert pick_release_group({"album_title": "Something Else"}, releases)["id"] == "first"
    assert pick_release_group({"album_title": None}, []) is None


def test_applies_to_track():
    positions = ["A1", "A2", "A3", "B1", "B2"]
    assert applies_to_track("", "A2", positions)
    assert applies_to_track("A1 to A3, B2", "A2", positions)
    assert applies_to_track("A1 to A3, B2", "B2", positions)
    assert not applies_to_track("A1 to A3, B2", "B1", positions)
    assert not applies_to_track("A1", None, positions)


def test_parse_year():
    assert parse_year("1979-06-15") == 1979
    assert parse_year("1979") == 1979
    assert parse_year("") is None
    assert parse_year(None) is None
