from analyzer.credits import CreditSet, discogs_role_credits, mb_relation_credit


def rel(type_, attributes=(), target="artist"):
    return {"type": type_, "target-type": target, "attributes": list(attributes),
            "artist": {"id": "a1", "name": "Steve Albini"}}


def test_mb_relations_map_to_roles():
    assert mb_relation_credit(rel("producer")) == ("producer", None)
    assert mb_relation_credit(rel("instrument", ["guitar", "bass"])) == ("instrument", "guitar, bass")
    assert mb_relation_credit(rel("mix")) == ("mixer", None)
    assert mb_relation_credit(rel("lyricist")) == ("lyricist", None)
    assert mb_relation_credit(rel("mastering")) is None
    assert mb_relation_credit(rel("producer", target="label")) is None


def test_discogs_roles():
    assert discogs_role_credits("Producer") == [("producer", "Producer")]
    assert discogs_role_credits("Guitar, Vocals [Backing]") == [("instrument", "Guitar"), ("vocal", "Vocals [Backing]")]
    assert discogs_role_credits("Written-By") == [("writer", "Written-By")]
    assert discogs_role_credits("Engineer [Recording]") == [("engineer", "Engineer [Recording]")]
    assert discogs_role_credits("Strings [String Quartet]") == [("instrument", "Strings [String Quartet]")]


def test_discogs_non_musical_credits_are_skipped():
    for role in ["Photography By", "Mastered By", "Design", "Executive-Producer", "Liner Notes", "Art Direction"]:
        assert discogs_role_credits(role) == [], role


def test_credit_set_merges_details_per_person_and_role():
    credits = CreditSet()
    person = {"name": "Kim Deal", "mb_artist_id": "k1"}
    credits.add(person, "instrument", "bass")
    credits.add(person, "instrument", "guitar")
    credits.add(person, "vocal", "lead vocals")
    assert sorted((role, detail) for _, role, detail in credits) == [("instrument", "bass, guitar"), ("vocal", "lead vocals")]
    assert credits.roles() == {"instrument", "vocal"}


def test_discogs_commas_inside_brackets_do_not_split():
    assert discogs_role_credits("Recorded By [Bear Creek, Skip Saylor], Bass") == [
        ("engineer", "Recorded By [Bear Creek, Skip Saylor]"), ("instrument", "Bass")]


def test_discogs_more_non_musical_and_modifier_credits():
    for role in ["CGI Artist [3D Rendering]", "Concept By [Cover]", "Public Relations [For Information]", "Guest"]:
        assert discogs_role_credits(role) == [], role
    assert discogs_role_credits("Guest, Keyboards") == [("instrument", "Keyboards")]
    assert discogs_role_credits("Musician, Guitar") == [("performer", "Musician"), ("instrument", "Guitar")]


def test_discogs_assistant_engineers_are_dropped():
    assert discogs_role_credits("Engineer [Assistant]") == []
    assert discogs_role_credits("Assistant Engineer") == []
    assert discogs_role_credits("Engineer [Mix]") == [("engineer", "Engineer [Mix]")]
