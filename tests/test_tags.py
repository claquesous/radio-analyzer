from analyzer.tags import counted_tags, style_tags


def test_style_tags_keeps_top_styles_and_parent_genre_at_half_weight():
    tags = style_tags({"Rock---Post-Punk": 0.6, "Electronic---Synth-pop": 0.3, "Jazz---Bop": 0.05})
    assert tags == {"post punk": 0.6, "rock": 0.3, "synth pop": 0.3, "electronic": 0.15}


def test_counted_tags_scales_relative_to_top_vote():
    tags = counted_tags([{"name": "Shoegaze", "count": 4}, {"name": "dream pop", "count": 2}, {"name": "x", "count": 0}], 0.8)
    assert tags == {"shoegaze": 0.8, "dream pop": 0.4}
    assert counted_tags(None) == {}
