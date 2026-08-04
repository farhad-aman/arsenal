import dl


def test_version_is_a_dotted_string():
    parts = dl.__version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
