from ext_mgr import parse_depends


def test_parse_depends_empty():
    ext_deps, path_deps = parse_depends([])
    assert ext_deps == []
    assert path_deps == []


def test_parse_depends_strings_only():
    ext_deps, path_deps = parse_depends(["ext-a", "ext-b"])
    assert ext_deps == ["ext-a", "ext-b"]
    assert path_deps == []


def test_parse_depends_dicts_only():
    ext_deps, path_deps = parse_depends([
        {"source": "a.md", "target": "b.md"},
        {"source": "c.md", "target": "d.md"},
    ])
    assert ext_deps == []
    assert path_deps == [
        {"source": "a.md", "target": "b.md"},
        {"source": "c.md", "target": "d.md"},
    ]


def test_parse_depends_mixed():
    ext_deps, path_deps = parse_depends([
        "ext-a",
        {"source": "a.md", "target": "a.md"},
        "ext-b",
        {"source": "b.md", "target": "c.md"},
    ])
    assert ext_deps == ["ext-a", "ext-b"]
    assert path_deps == [
        {"source": "a.md", "target": "a.md"},
        {"source": "b.md", "target": "c.md"},
    ]


def test_parse_depends_ignores_unknown_types():
    ext_deps, path_deps = parse_depends([123, True])
    assert ext_deps == []
    assert path_deps == []
