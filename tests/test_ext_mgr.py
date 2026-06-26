import json
import os
import subprocess
import pytest
from unittest.mock import MagicMock, patch
from ext_mgr import (
    parse_depends,
    ConfigManager,
    ConfigError,
    ExtensionStore,
    ChangeSet,
    SymlinkManager,
    NpmDependencyManager,
    Validator,
    DialogUI,
    Extension,
    PathDep,
    Config,
    Status,
    Format,
    DialogAdapter,
    DEFAULT_TARGET_DIR,
    resolve_target_dir,
    _to_posix_drive_path,
)


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


def _write_config(tmp_path, config_dict):
    p = tmp_path / "extensions.json"
    p.write_text(json.dumps(config_dict, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _valid_config():
    return {
        "version": 4,
        "extensions": {
            "skills": {
                "brainstorming": {
                    "enabled": True,
                    "visible": True,
                    "description": "头脑风暴",
                    "depends": [
                        {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
                    ],
                },
            },
            "agents": {
                "kernel-dev": {
                    "enabled": False,
                    "visible": True,
                    "description": "Kernel开发",
                    "depends": [
                        "skills/brainstorming",
                        {"source": "agents/kernel.md", "target": "agents/kernel.md"},
                    ],
                },
            },
            "commands": {},
            "plugins": {},
        },
    }


def test_validate_version4_ok(tmp_path):
    p = _write_config(tmp_path, _valid_config())
    mgr = ConfigManager(p)
    config = mgr.load()
    assert config.version == 4
    assert config.warnings == []


def test_validate_version2_rejected(tmp_path):
    cfg = _valid_config()
    cfg["version"] = 2
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="Unsupported version: 2"):
        ConfigManager(p).load()


def test_validate_missing_version(tmp_path):
    cfg = _valid_config()
    del cfg["version"]
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="Missing 'version' field"):
        ConfigManager(p).load()


def test_validate_type_derived_from_group(tmp_path):
    cfg = _valid_config()
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert config.extensions["brainstorming"].type == "skill"
    assert config.extensions["kernel-dev"].type == "agent"


def test_validate_move_to_plugins_group(tmp_path):
    cfg = _valid_config()
    ext = cfg["extensions"]["skills"].pop("brainstorming")
    cfg["extensions"]["plugins"]["brainstorming"] = ext
    cfg["extensions"]["agents"]["kernel-dev"]["depends"] = [
        "plugins/brainstorming",
        {"source": "agents/kernel.md", "target": "agents/kernel.md"},
    ]
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert config.extensions["brainstorming"].type == "plugin"


def test_validate_unknown_group_rejected(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["unknown"] = {}
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="Unknown extension category 'unknown'"):
        ConfigManager(p).load()


def test_validate_missing_groups_ok(tmp_path):
    cfg = {
        "version": 4,
        "extensions": {
            "skills": {
                "brainstorming": {
                    "enabled": True,
                    "visible": True,
                    "description": "头脑风暴",
                }
            }
        },
    }
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert config.extensions["brainstorming"].type == "skill"


def test_validate_visible_present_ok(tmp_path):
    cfg = _valid_config()
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert config.extensions["brainstorming"].visible is True


def test_validate_visible_missing_rejected(tmp_path):
    cfg = _valid_config()
    del cfg["extensions"]["skills"]["brainstorming"]["visible"]
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="missing 'visible' field"):
        ConfigManager(p).load()


def test_validate_visible_false_ok(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["skills"]["brainstorming"]["visible"] = False
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert config.extensions["brainstorming"].visible is False


def test_validate_visible_non_bool_rejected(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["skills"]["brainstorming"]["visible"] = "yes"
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="'visible' must be a boolean"):
        ConfigManager(p).load()


def test_validate_duplicate_name_across_groups_rejected(tmp_path):
    cfg = _valid_config()
    ext = cfg["extensions"]["skills"]["brainstorming"]
    cfg["extensions"]["agents"]["brainstorming"] = dict(ext)
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="duplicated across multiple categories"):
        ConfigManager(p).load()


def test_save_produces_nested_format(tmp_path):
    p = _write_config(tmp_path, _valid_config())
    mgr = ConfigManager(p)
    config = mgr.load()
    mgr.save(config)
    with open(p, "r", encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["version"] == 4
    assert "skills" in raw["extensions"]
    assert "brainstorming" in raw["extensions"]["skills"]
    assert "type" not in raw["extensions"]["skills"]["brainstorming"]


def test_save_always_writes_visible(tmp_path):
    p = _write_config(tmp_path, _valid_config())
    mgr = ConfigManager(p)
    config = mgr.load()
    mgr.save(config)
    with open(p, "r", encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["extensions"]["skills"]["brainstorming"]["visible"] is True


def test_save_preserves_key_order(tmp_path):
    raw = {
        "version": 4,
        "extensions": {
            "skills": {
                "visible-default": {
                    "enabled": True,
                    "visible": True,
                    "description": "D",
                    "depends": [{"source": "a.md", "target": "a.md"}],
                },
                "hidden": {
                    "enabled": False,
                    "visible": False,
                    "description": "H",
                    "depends": [{"source": "b.md", "target": "b.md"}],
                },
            },
            "agents": {}, "commands": {}, "plugins": {},
        },
    }
    p = _write_config(tmp_path, raw)
    config = ConfigManager(str(p)).load()
    # toggle nothing; save back
    ConfigManager(str(p)).save(config)
    with open(p, "r", encoding="utf-8") as f:
        saved = json.load(f)
    skills = saved["extensions"]["skills"]
    assert list(skills["visible-default"].keys()) == ["enabled", "visible", "description", "depends"]
    assert list(skills["hidden"].keys()) == ["enabled", "visible", "description", "depends"]


def test_validate_key_with_slash_rejected(tmp_path):
    cfg = _valid_config()
    ext = cfg["extensions"]["skills"].pop("brainstorming")
    cfg["extensions"]["skills"]["skills/brainstorming"] = ext
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="invalid format"):
        ConfigManager(p).load()


def test_validate_key_with_dotdot_rejected(tmp_path):
    cfg = _valid_config()
    ext = cfg["extensions"]["skills"].pop("brainstorming")
    cfg["extensions"]["skills"]["../evil"] = ext
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="illegal characters"):
        ConfigManager(p).load()


def test_validate_depends_path_dep_missing_source(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["skills"]["brainstorming"]["depends"] = [
        {"target": "skills/brainstorming.md"}
    ]
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="missing 'source' or 'target' field"):
        ConfigManager(p).load()


def test_validate_depends_invalid_type(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["skills"]["brainstorming"]["depends"] = [123]
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="invalid dependency type"):
        ConfigManager(p).load()


def test_validate_ext_dep_group_name_ok(tmp_path):
    cfg = _valid_config()
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert config.extensions["kernel-dev"].ext_deps == ["brainstorming"]


def test_validate_ext_dep_not_exist_rejected(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["skills"]["brainstorming"]["depends"] = ["skills/nonexistent"]
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="does not exist"):
        ConfigManager(p).load()


def test_validate_ext_dep_bare_name_rejected(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["agents"]["kernel-dev"]["depends"] = ["brainstorming"]
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="invalid format"):
        ConfigManager(p).load()


def test_validate_ext_dep_invalid_group_rejected(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["agents"]["kernel-dev"]["depends"] = ["unknown/brainstorming"]
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="invalid category 'unknown'"):
        ConfigManager(p).load()


def test_validate_ext_dep_group_mismatch_rejected(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["agents"]["kernel-dev"]["depends"] = ["agents/brainstorming"]
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="category mismatch"):
        ConfigManager(p).load()


def test_validate_ext_dep_empty_rejected(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["skills"]["brainstorming"]["depends"] = [""]
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="empty dependency name"):
        ConfigManager(p).load()


def test_validate_empty_extensions_ok(tmp_path):
    cfg = {"version": 4, "extensions": {}}
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert config.extensions == {}


def test_validate_empty_depends_ok(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["skills"]["brainstorming"]["depends"] = []
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert config.warnings == []


def test_no_cycle(tmp_path):
    cfg = _valid_config()
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert config.warnings == []


def test_simple_cycle(tmp_path):
    cfg = {
        "version": 4,
        "extensions": {
            "skills": {
                "a": {
                    "enabled": True,
                    "visible": True,
                    "description": "A",
                    "depends": ["agents/b"],
                },
            },
            "agents": {
                "b": {
                    "enabled": True,
                    "visible": True,
                    "description": "B",
                    "depends": ["skills/a"],
                },
            },
        },
    }
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="Circular dependency"):
        ConfigManager(p).load()


def test_three_node_cycle(tmp_path):
    cfg = {
        "version": 4,
        "extensions": {
            "skills": {
                "a": {
                    "enabled": True,
                    "visible": True,
                    "description": "A",
                    "depends": ["agents/b"],
                },
            },
            "agents": {
                "b": {
                    "enabled": True,
                    "visible": True,
                    "description": "B",
                    "depends": ["commands/c"],
                },
            },
            "commands": {
                "c": {
                    "enabled": True,
                    "visible": True,
                    "description": "C",
                    "depends": [
                        "skills/a",
                        {"source": "c.md", "target": "c.md"},
                    ],
                },
            },
        },
    }
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="Circular dependency"):
        ConfigManager(p).load()


def test_cycle_with_path_deps_no_false_positive(tmp_path):
    cfg = {
        "version": 4,
        "extensions": {
            "skills": {
                "a": {
                    "enabled": True,
                    "visible": True,
                    "description": "A",
                    "depends": [
                        {"source": "a.md", "target": "a.md"},
                    ],
                },
            },
            "agents": {
                "b": {
                    "enabled": True,
                    "visible": True,
                    "description": "B",
                    "depends": [
                        "skills/a",
                        {"source": "b.md", "target": "b.md"},
                    ],
                },
            },
        },
    }
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert config.warnings == []


def _extensions_for_resolver():
    return {
        "a": {
            "type": "skill",
            "enabled": False,
            "description": "A",
            "depends": ["b", {"source": "a.md", "target": "a.md"}],
        },
        "b": {
            "type": "agent",
            "enabled": False,
            "description": "B",
            "depends": [
                "c",
                {"source": "b.md", "target": "b.md"},
            ],
        },
        "c": {
            "type": "agent",
            "enabled": False,
            "description": "C",
            "depends": [{"source": "c.md", "target": "c.md"}],
        },
        "standalone": {
            "type": "skill",
            "enabled": False,
            "description": "Standalone",
            "depends": [{"source": "s.md", "target": "s.md"}],
        },
    }


def test_resolve_single_ext():
    exts = make_extensions_from_raw(_extensions_for_resolver())
    cs = ExtensionStore(exts).resolve_changes(["standalone"])
    assert cs.to_enable == ["standalone"]
    assert "standalone" not in cs.to_disable


def test_resolve_with_ext_dep():
    exts = make_extensions_from_raw(_extensions_for_resolver())
    cs = ExtensionStore(exts).resolve_changes(["a"])
    assert "a" in cs.to_enable
    assert "b" in cs.to_enable


def test_resolve_transitive_deps():
    exts = make_extensions_from_raw(_extensions_for_resolver())
    cs = ExtensionStore(exts).resolve_changes(["a"])
    assert sorted(cs.to_enable) == ["a", "b", "c"]


def test_resolve_disable_no_cascade():
    exts = make_extensions_from_raw(_extensions_for_resolver())
    cs = ExtensionStore(exts).resolve_changes(["a"])
    assert "standalone" in cs.to_disable


def test_resolve_reject_if_depended():
    exts = make_extensions_from_raw(_extensions_for_resolver())
    cs = ExtensionStore(exts).resolve_changes(["a"])
    rejected_names = [r["name"] for r in cs.rejected]
    assert "b" not in rejected_names
    assert "c" not in rejected_names


def test_resolve_ext_dep_not_in_extensions():
    exts = make_extensions_from_raw({
        "a": {
            "type": "skill",
            "enabled": False,
            "description": "A",
            "depends": ["nonexistent"],
        }
    })
    cs = ExtensionStore(exts).resolve_changes(["a"])
    assert cs.to_enable == ["a"]


def test_resolve_all_enabled_no_disable():
    exts = make_extensions_from_raw(_extensions_for_resolver())
    cs = ExtensionStore(exts).resolve_changes(["a", "standalone"])
    assert cs.to_enable == ["a", "b", "c", "standalone"]
    assert cs.to_disable == []
    assert cs.rejected == []


def _setup_dirs(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    return str(source), str(target)


def test_create_symlink_success(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "source" / "skills").mkdir()
    (tmp_path / "source" / "skills" / "brainstorming.md").write_text("skill")
    mgr = SymlinkManager(source, target)
    exts = make_extensions_from_raw({
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    })
    results = mgr.apply_for_extension("brainstorming", exts, "create")
    assert len(results) == 1
    assert results[0]["status"] == "success"
    link = os.path.join(target, "skills", "brainstorming.md")
    assert os.path.islink(link)


def test_create_symlink_already_exists_correct(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "source" / "skills").mkdir()
    src_file = tmp_path / "source" / "skills" / "brainstorming.md"
    src_file.write_text("skill")
    (tmp_path / "target" / "skills").mkdir()
    os.symlink(str(src_file), os.path.join(target, "skills", "brainstorming.md"))
    mgr = SymlinkManager(source, target)
    exts = make_extensions_from_raw({
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    })
    results = mgr.apply_for_extension("brainstorming", exts, "create")
    # force-recreate: even an already-correct link is rebuilt -> SUCCESS
    assert results[0]["status"] == "success"
    link = os.path.join(target, "skills", "brainstorming.md")
    assert os.path.islink(link)
    assert os.readlink(link) == str(src_file)


def test_create_symlink_force_overwrites_conflict(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "target" / "skills").mkdir()
    conflict = tmp_path / "target" / "skills" / "brainstorming.md"
    conflict.write_text("other")
    mgr = SymlinkManager(source, target)
    exts = make_extensions_from_raw({
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    })
    results = mgr.apply_for_extension("brainstorming", exts, "create")
    # existing real file is removed and replaced by the symlink -> SUCCESS
    assert results[0]["status"] == "success"
    link = os.path.join(target, "skills", "brainstorming.md")
    assert os.path.islink(link)


def test_create_symlink_force_overwrites_mismatched_link(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "source" / "skills").mkdir()
    src_file = tmp_path / "source" / "skills" / "brainstorming.md"
    src_file.write_text("skill")
    (tmp_path / "target" / "skills").mkdir()
    other = tmp_path / "source" / "skills" / "other.md"
    other.write_text("x")
    os.symlink(str(other), os.path.join(target, "skills", "brainstorming.md"))
    mgr = SymlinkManager(source, target)
    exts = make_extensions_from_raw({
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    })
    results = mgr.apply_for_extension("brainstorming", exts, "create")
    # link pointing elsewhere is replaced -> SUCCESS, now points to correct source
    assert results[0]["status"] == "success"
    link = os.path.join(target, "skills", "brainstorming.md")
    assert os.readlink(link) == str(src_file)


def test_remove_symlink_success(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "source" / "skills").mkdir()
    src_file = tmp_path / "source" / "skills" / "brainstorming.md"
    src_file.write_text("skill")
    (tmp_path / "target" / "skills").mkdir()
    os.symlink(str(src_file), os.path.join(target, "skills", "brainstorming.md"))
    mgr = SymlinkManager(source, target)
    exts = make_extensions_from_raw({
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    })
    results = mgr.apply_for_extension("brainstorming", exts, "remove")
    assert results[0]["status"] == "success"
    assert not os.path.exists(os.path.join(target, "skills", "brainstorming.md"))


def test_remove_symlink_force_removes_mismatched(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "source" / "skills").mkdir()
    other = tmp_path / "source" / "skills" / "other.md"
    other.write_text("x")
    (tmp_path / "target" / "skills").mkdir()
    os.symlink(str(other), os.path.join(target, "skills", "brainstorming.md"))
    mgr = SymlinkManager(source, target)
    exts = make_extensions_from_raw({
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    })
    results = mgr.apply_for_extension("brainstorming", exts, "remove")
    # force-remove: link pointing elsewhere is still removed -> SUCCESS
    assert results[0]["status"] == "success"
    assert not os.path.exists(os.path.join(target, "skills", "brainstorming.md"))


def test_remove_symlink_force_removes_real_file(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "target" / "skills").mkdir()
    (tmp_path / "target" / "skills" / "brainstorming.md").write_text("plain")
    mgr = SymlinkManager(source, target)
    exts = make_extensions_from_raw({
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    })
    results = mgr.apply_for_extension("brainstorming", exts, "remove")
    # force-remove: a real file at the target is removed -> SUCCESS
    assert results[0]["status"] == "success"
    assert not os.path.exists(os.path.join(target, "skills", "brainstorming.md"))


def test_remove_symlink_not_exist(tmp_path):
    source, target = _setup_dirs(tmp_path)
    mgr = SymlinkManager(source, target)
    exts = make_extensions_from_raw({
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    })
    results = mgr.apply_for_extension("brainstorming", exts, "remove")
    assert results[0]["status"] == "skipped"


def test_symlink_manager_preserves_posix_target_on_windows():
    """On Windows, target_dir already in /C/ POSIX form must survive __init__
    verbatim (os.path.abspath would corrupt it to C:\\C\\..)."""
    with patch("ext_mgr.os.name", "nt"):
        mgr = SymlinkManager(r"C:\proj\src", "/C/Users/name/.config/opencode")
    assert mgr._target_dir == "/C/Users/name/.config/opencode"


def test_create_symlink_converts_paths_to_posix_on_windows():
    """Under os.name=='nt', _create_symlink must hand os.symlink source and
    target in /C/ POSIX form rather than C:\\.. backslash form."""
    mgr = SymlinkManager.__new__(SymlinkManager)
    mgr._source_dir = r"C:\proj\src"
    mgr._target_dir = r"C:\proj\tgt"
    symlink_mock = MagicMock()
    with patch("ext_mgr.os.name", "nt"), \
         patch.object(SymlinkManager, "_ensure_subdir"), \
         patch("ext_mgr.os.symlink", symlink_mock), \
         patch("ext_mgr.os.path.islink", return_value=False), \
         patch("ext_mgr.os.path.exists", return_value=False):
        result = mgr._create_symlink("skills/a.md", "skills/a.md")
    assert result["status"] == Status.SUCCESS
    symlink_mock.assert_called_once_with(
        "/C/proj/src/skills/a.md", "/C/proj/tgt/skills/a.md"
    )


def test_remove_symlink_converts_target_to_posix_on_windows():
    """Under os.name=='nt', _remove_symlink must operate on the /C/ POSIX form."""
    mgr = SymlinkManager.__new__(SymlinkManager)
    mgr._source_dir = r"C:\proj\src"
    mgr._target_dir = r"C:\proj\tgt"
    remove_mock = MagicMock()
    with patch("ext_mgr.os.name", "nt"), \
         patch.object(SymlinkManager, "_remove_existing", remove_mock), \
         patch("ext_mgr.os.path.islink", return_value=True):
        result = mgr._remove_symlink("skills/a.md", "skills/a.md")
    assert result["status"] == Status.SUCCESS
    remove_mock.assert_called_once_with("/C/proj/tgt/skills/a.md")


def test_apply_for_extension_multiple_paths(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "source" / "skills").mkdir()
    (tmp_path / "source" / "skills" / "main.md").write_text("main")
    (tmp_path / "source" / "skills" / "helper.md").write_text("helper")
    mgr = SymlinkManager(source, target)
    exts = make_extensions_from_raw({
        "multi": {
            "type": "skill",
            "enabled": True,
            "description": "Multi",
            "depends": [
                {"source": "skills/main.md", "target": "skills/main.md"},
                {"source": "skills/helper.md", "target": "skills/helper.md"},
            ],
        }
    })
    results = mgr.apply_for_extension("multi", exts, "create")
    assert len(results) == 2
    assert all(r["status"] == "success" for r in results)


def test_apply_for_extension_no_path_deps(tmp_path):
    source, target = _setup_dirs(tmp_path)
    mgr = SymlinkManager(source, target)
    exts = make_extensions_from_raw({
        "pure-dep": {
            "type": "skill",
            "enabled": True,
            "description": "PureDep",
            "depends": ["other-ext"],
        }
    })
    results = mgr.apply_for_extension("pure-dep", exts, "create")
    assert len(results) == 1
    assert results[0]["status"] == "skipped"


def test_apply_changes_with_extensions(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "source" / "skills").mkdir()
    (tmp_path / "source" / "skills" / "a.md").write_text("a")
    src_b = tmp_path / "source" / "skills" / "b.md"
    src_b.write_text("b")
    (tmp_path / "target" / "skills").mkdir(parents=True, exist_ok=True)
    os.symlink(str(src_b), os.path.join(target, "skills", "b.md"))
    mgr = SymlinkManager(source, target)
    exts = make_extensions_from_raw({
        "ext-a": {
            "type": "skill",
            "enabled": True,
            "description": "A",
            "depends": [{"source": "skills/a.md", "target": "skills/a.md"}],
        },
        "ext-b": {
            "type": "skill",
            "enabled": True,
            "description": "B",
            "depends": [{"source": "skills/b.md", "target": "skills/b.md"}],
        },
    })
    results = mgr.apply_changes(["ext-a"], ["ext-b"], exts)
    success_names = [r["name"] for r in results if r["status"] == "success"]
    assert "skills/a.md" in success_names
    assert "skills/b.md" in success_names


def test_validate_enabled_ok(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "source" / "skills").mkdir()
    src_file = tmp_path / "source" / "skills" / "brainstorming.md"
    src_file.write_text("skill")
    (tmp_path / "target" / "skills").mkdir()
    os.symlink(str(src_file), os.path.join(target, "skills", "brainstorming.md"))
    validator = Validator(source, target)
    exts = make_extensions_from_raw({
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    })
    results = validator.validate(exts)
    assert len(results) == 1
    assert results[0]["status"] == "ok"


def test_validate_enabled_missing(tmp_path):
    source, target = _setup_dirs(tmp_path)
    validator = Validator(source, target)
    exts = make_extensions_from_raw({
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    })
    results = validator.validate(exts)
    assert any(r["status"] == "missing" for r in results)


def test_validate_disabled_unexpected(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "source" / "skills").mkdir()
    src_file = tmp_path / "source" / "skills" / "brainstorming.md"
    src_file.write_text("skill")
    (tmp_path / "target" / "skills").mkdir()
    os.symlink(str(src_file), os.path.join(target, "skills", "brainstorming.md"))
    validator = Validator(source, target)
    exts = make_extensions_from_raw({
        "brainstorming": {
            "type": "skill",
            "enabled": False,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    })
    results = validator.validate(exts)
    assert any(r["status"] == "unexpected" for r in results)


def test_validate_enabled_broken_link(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "source" / "skills").mkdir()
    src_file = tmp_path / "source" / "skills" / "brainstorming.md"
    src_file.write_text("skill")
    (tmp_path / "target" / "skills").mkdir()
    os.symlink("/nonexistent/path", os.path.join(target, "skills", "brainstorming.md"))
    validator = Validator(source, target)
    exts = make_extensions_from_raw({
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    })
    results = validator.validate(exts)
    assert any(r["status"] == "broken" for r in results)


def test_validate_no_target_dir(tmp_path):
    source = str(tmp_path / "source")
    target = str(tmp_path / "nonexistent_target")
    os.makedirs(source)
    validator = Validator(source, target)
    exts = make_extensions_from_raw({
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    })
    results = validator.validate(exts)
    assert any(r["status"] == "missing" for r in results)


def test_validate_multiple_paths_per_extension(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "source" / "skills").mkdir()
    (tmp_path / "source" / "skills" / "main.md").write_text("main")
    (tmp_path / "target" / "skills").mkdir()
    src_main = tmp_path / "source" / "skills" / "main.md"
    os.symlink(str(src_main), os.path.join(target, "skills", "main.md"))
    validator = Validator(source, target)
    exts = make_extensions_from_raw({
        "multi": {
            "type": "skill",
            "enabled": True,
            "description": "Multi",
            "depends": [
                {"source": "skills/main.md", "target": "skills/main.md"},
                {"source": "skills/helper.md", "target": "skills/helper.md"},
            ],
        }
    })
    results = validator.validate(exts)
    statuses = [r["status"] for r in results]
    assert "ok" not in statuses
    assert any(s == "missing" for s in statuses)


def _make_ui(extensions, source_dir="/fake"):
    adapter = MagicMock()
    config_mgr = MagicMock()
    store = ExtensionStore(make_extensions_from_raw(extensions), source_dir=source_dir)
    ui = DialogUI(adapter, store, config_mgr)
    return ui, store


def test_check_availability_all_present(tmp_path):
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "brainstorming.md").write_text("skill")
    exts = make_extensions_from_raw({
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    })
    store = ExtensionStore(exts, source_dir=str(tmp_path))
    missing = store.check_availability("brainstorming")
    assert missing == []


def test_check_availability_ext_dep_missing(tmp_path):
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "brainstorming.md").write_text("skill")
    exts = make_extensions_from_raw({
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": ["nonexistent-ext"],
        }
    })
    store = ExtensionStore(exts, source_dir=str(tmp_path))
    missing = store.check_availability("brainstorming")
    assert "nonexistent-ext" in missing


def test_check_availability_path_dep_source_missing(tmp_path):
    exts = make_extensions_from_raw({
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    })
    store = ExtensionStore(exts, source_dir=str(tmp_path))
    missing = store.check_availability("brainstorming")
    assert "skills/brainstorming.md" in missing


def test_build_checklist_items_filters_by_type():
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
        },
        "kernel-dev": {
            "type": "agent",
            "enabled": False,
            "description": "Kernel Dev",
        },
    }
    ui, _ = _make_ui(exts)
    items, unavailable = ui._build_checklist_items("skill")
    names = [i[0] for i in items]
    assert "brainstorming" in names
    assert "kernel-dev" not in names


def test_build_checklist_items_filters_agent():
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
        },
        "kernel-dev": {
            "type": "agent",
            "enabled": False,
            "description": "Kernel Dev",
        },
    }
    ui, _ = _make_ui(exts)
    items, unavailable = ui._build_checklist_items("agent")
    names = [i[0] for i in items]
    assert "kernel-dev" in names
    assert "brainstorming" not in names


def test_build_checklist_items_filters_invisible():
    exts = {
        "visible-skill": {
            "type": "skill",
            "enabled": True,
            "description": "Visible",
            "visible": True,
        },
        "hidden-skill": {
            "type": "skill",
            "enabled": True,
            "description": "Hidden",
            "visible": False,
        },
    }
    ui, _ = _make_ui(exts)
    items, unavailable = ui._build_checklist_items("skill")
    names = [i[0] for i in items]
    assert "visible-skill" in names
    assert "hidden-skill" not in names


def test_count_stats_excludes_invisible():
    exts = {
        "visible-skill": {
            "type": "skill",
            "enabled": True,
            "description": "Visible",
            "visible": True,
        },
        "hidden-skill": {
            "type": "skill",
            "enabled": True,
            "description": "Hidden",
            "visible": False,
        },
    }
    ui, _ = _make_ui(exts)
    total, enabled, ok = ui._count_stats("skill")
    assert total == 1
    assert enabled == 1


def test_show_type_checklist_does_not_toggle_invisible():
    adapter = MagicMock()
    config_mgr = MagicMock()
    exts = make_extensions_from_raw({
        "visible-ext": {
            "type": "skill",
            "enabled": True,
            "description": "Visible",
            "visible": True,
        },
        "hidden-ext": {
            "type": "skill",
            "enabled": True,
            "description": "Hidden",
            "visible": False,
        },
    })
    store = ExtensionStore(exts)
    ui = DialogUI(adapter, store, config_mgr)
    adapter.run_checklist.return_value = (0, [], [])
    ui._show_type_checklist("skill")
    assert exts["hidden-ext"].enabled is True


def test_count_stats_by_type():
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
        },
        "code-review": {
            "type": "skill",
            "enabled": False,
            "description": "Code Review",
        },
        "kernel-dev": {
            "type": "agent",
            "enabled": True,
            "description": "Kernel Dev",
        },
    }
    ui, _ = _make_ui(exts)
    total, enabled, ok = ui._count_stats("skill")
    assert total == 2
    assert enabled == 1


def test_count_stats_empty_type():
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
        },
    }
    ui, _ = _make_ui(exts)
    total, enabled, ok = ui._count_stats("plugin")
    assert total == 0
    assert enabled == 0


def test_cascade_simple():
    exts = make_extensions_from_raw({
        "a": {
            "type": "skill",
            "enabled": True,
            "description": "A",
            "depends": ["b", {"source": "a.md", "target": "a.md"}],
        },
        "b": {
            "type": "agent",
            "enabled": True,
            "description": "B",
            "depends": [{"source": "b.md", "target": "b.md"}],
        },
    })
    cs = ExtensionStore(exts).resolve_changes(["b"])
    assert cs.to_enable == ["b"]
    assert cs.to_disable == ["a"]
    assert cs.cascade_disabled == []
    assert cs.rejected == []


def test_cascade_recursive():
    exts = make_extensions_from_raw({
        "a": {
            "type": "skill",
            "enabled": True,
            "description": "A",
            "depends": ["b", {"source": "a.md", "target": "a.md"}],
        },
        "b": {
            "type": "agent",
            "enabled": True,
            "description": "B",
            "depends": ["c", {"source": "b.md", "target": "b.md"}],
        },
        "c": {
            "type": "agent",
            "enabled": True,
            "description": "C",
            "depends": [{"source": "c.md", "target": "c.md"}],
        },
    })
    cs = ExtensionStore(exts).resolve_changes([])
    assert cs.to_disable == ["a"]
    assert sorted(cs.cascade_disabled) == ["b", "c"]


def test_cascade_stopped_by_other_dependent():
    exts = make_extensions_from_raw({
        "a": {
            "type": "skill",
            "enabled": True,
            "description": "A",
            "depends": ["b", {"source": "a.md", "target": "a.md"}],
        },
        "b": {
            "type": "agent",
            "enabled": True,
            "description": "B",
            "depends": ["c", {"source": "b.md", "target": "b.md"}],
        },
        "c": {
            "type": "agent",
            "enabled": True,
            "description": "C",
            "depends": [{"source": "c.md", "target": "c.md"}],
        },
        "d": {
            "type": "skill",
            "enabled": True,
            "description": "D",
            "depends": ["c", {"source": "d.md", "target": "d.md"}],
        },
    })
    cs = ExtensionStore(exts).resolve_changes(["d"])
    assert "a" in cs.to_disable
    assert "b" in cs.cascade_disabled
    assert "c" not in cs.cascade_disabled


def test_cascade_respects_user_selection():
    exts = make_extensions_from_raw({
        "a": {
            "type": "skill",
            "enabled": True,
            "description": "A",
            "depends": ["b", {"source": "a.md", "target": "a.md"}],
        },
        "b": {
            "type": "agent",
            "enabled": True,
            "description": "B",
            "depends": [{"source": "b.md", "target": "b.md"}],
        },
    })
    cs = ExtensionStore(exts).resolve_changes(["b"])
    assert cs.to_enable == ["b"]
    assert "b" not in cs.cascade_disabled


def test_cascade_shared_dep_disabled_together():
    exts = make_extensions_from_raw({
        "a": {
            "type": "skill",
            "enabled": True,
            "description": "A",
            "depends": ["c", {"source": "a.md", "target": "a.md"}],
        },
        "b": {
            "type": "skill",
            "enabled": True,
            "description": "B",
            "depends": ["c", {"source": "b.md", "target": "b.md"}],
        },
        "c": {
            "type": "agent",
            "enabled": True,
            "description": "C",
            "depends": [{"source": "c.md", "target": "c.md"}],
        },
    })
    cs = ExtensionStore(exts).resolve_changes([])
    assert sorted(cs.to_disable) == ["a", "b"]
    assert cs.cascade_disabled == ["c"]


def test_cascade_no_cascade_when_dep_in_selected():
    exts = make_extensions_from_raw({
        "a": {
            "type": "skill",
            "enabled": True,
            "description": "A",
            "depends": ["b", {"source": "a.md", "target": "a.md"}],
        },
        "b": {
            "type": "agent",
            "enabled": True,
            "description": "B",
            "depends": [{"source": "b.md", "target": "b.md"}],
        },
        "standalone": {
            "type": "skill",
            "enabled": True,
            "description": "Standalone",
            "depends": [{"source": "s.md", "target": "s.md"}],
        },
    })
    cs = ExtensionStore(exts).resolve_changes(["standalone"])
    assert "b" in cs.cascade_disabled
    assert "standalone" in cs.to_enable


def test_cascade_with_existing_test_data():
    exts = make_extensions_from_raw(_extensions_for_resolver())
    cs = ExtensionStore(exts).resolve_changes([])
    assert "a" in cs.to_disable
    assert sorted(cs.cascade_disabled) == ["b", "c"]
    assert "standalone" in cs.to_disable


def test_cascade_disabled_in_result_for_no_change():
    exts = make_extensions_from_raw(_extensions_for_resolver())
    cs = ExtensionStore(exts).resolve_changes(["a", "standalone"])
    assert cs.cascade_disabled == []


def _make_ui_for_summary():
    adapter = MagicMock()
    config_mgr = MagicMock()
    adapter.run_yesno.return_value = 0
    store = ExtensionStore({})
    ui = DialogUI(adapter, store, config_mgr)
    return ui, adapter


def test_show_change_summary_with_cascade():
    ui, adapter = _make_ui_for_summary()
    changes = ChangeSet(
        to_enable=["x"],
        to_disable=["a"],
        cascade_disabled=["b", "c"],
        rejected=[],
    )
    ui.show_change_summary(changes)
    call_args = adapter.run_yesno.call_args
    text = call_args[0][1]
    assert "Disable" in text
    assert "Cascade disabled" in text
    assert "b" in text
    assert "c" in text


def test_show_change_summary_no_cascade():
    ui, adapter = _make_ui_for_summary()
    changes = ChangeSet(
        to_enable=["x"],
        to_disable=["a"],
        cascade_disabled=[],
        rejected=[],
    )
    ui.show_change_summary(changes)
    call_args = adapter.run_yesno.call_args
    text = call_args[0][1]
    assert "Cascade disabled" not in text


def test_show_change_summary_cascade_before_rejected():
    ui, adapter = _make_ui_for_summary()
    changes = ChangeSet(
        to_enable=[],
        to_disable=["a"],
        cascade_disabled=["b"],
        rejected=[{"name": "c", "reason": "required by others", "dependents": ["d"]}],
    )
    ui.show_change_summary(changes)
    call_args = adapter.run_yesno.call_args
    text = call_args[0][1]
    lines = text.split("\n")
    cascade_idx = next(i for i, l in enumerate(lines) if "Cascade disabled" in l)
    rejected_idx = next(i for i, l in enumerate(lines) if "Refused to disable" in l)
    assert cascade_idx < rejected_idx


def test_cascade_disable_disables_child():
    exts = make_extensions_from_raw({
        "parent": {
            "type": "skill",
            "enabled": True,
            "description": "Parent",
            "depends": ["child"],
        },
        "child": {
            "type": "agent",
            "enabled": True,
            "description": "Child",
            "depends": [],
        },
    })
    store = ExtensionStore(exts)
    store.cascade_disable({"parent"})
    assert exts["child"].enabled is False


def test_cascade_disable_keeps_child_if_other_parent_enabled():
    exts = make_extensions_from_raw({
        "parent-a": {
            "type": "skill",
            "enabled": False,
            "description": "Parent A",
            "depends": ["shared-child"],
        },
        "parent-b": {
            "type": "skill",
            "enabled": True,
            "description": "Parent B",
            "depends": ["shared-child"],
        },
        "shared-child": {
            "type": "agent",
            "enabled": True,
            "description": "Shared Child",
            "depends": [],
        },
    })
    store = ExtensionStore(exts)
    store.cascade_disable({"parent-a"})
    assert exts["shared-child"].enabled is True


def test_cascade_disable_transitive():
    exts = make_extensions_from_raw({
        "parent": {
            "type": "skill",
            "enabled": True,
            "description": "Parent",
            "depends": ["mid"],
        },
        "mid": {
            "type": "agent",
            "enabled": True,
            "description": "Mid",
            "depends": ["leaf"],
        },
        "leaf": {
            "type": "agent",
            "enabled": True,
            "description": "Leaf",
            "depends": [],
        },
    })
    store = ExtensionStore(exts)
    store.cascade_disable({"parent"})
    assert exts["mid"].enabled is False
    assert exts["leaf"].enabled is False


def test_show_type_checklist_cascades_disable_across_types():
    adapter = MagicMock()
    config_mgr = MagicMock()
    exts = make_extensions_from_raw({
        "parent": {
            "type": "skill",
            "enabled": True,
            "description": "Parent skill",
            "depends": ["child-agent"],
        },
        "child-agent": {
            "type": "agent",
            "enabled": True,
            "description": "Child agent",
            "depends": [],
        },
    })
    store = ExtensionStore(exts)
    ui = DialogUI(adapter, store, config_mgr)
    adapter.run_checklist.return_value = (0, [], [])
    ui._show_type_checklist("skill")
    assert exts["parent"].enabled is False
    assert exts["child-agent"].enabled is False


def make_extensions(spec):
    """从紧凑 spec 构造 dict[name -> Extension]。

    spec[name] = {
        "type": str,              # 必填
        "enabled": bool,          # 必填
        "description": str,       # 可选，默认 ""
        "ext_deps": [str, ...],   # 可选，默认 []
        "path_deps": [(source, target), ...],  # 可选，默认 []
        "visible": bool,          # 可选，默认 True
    }
    """
    extensions = {}
    for name, attrs in spec.items():
        extensions[name] = Extension(
            name=name,
            type=attrs["type"],
            enabled=attrs["enabled"],
            description=attrs.get("description", ""),
            ext_deps=list(attrs.get("ext_deps", [])),
            path_deps=[PathDep(s, t) for s, t in attrs.get("path_deps", [])],
            visible=attrs.get("visible", True),
        )
    return extensions


def make_extensions_from_raw(raw):
    """把旧式 raw dict[name -> {type,enabled,description,depends,visible}]
    转为 dict[name -> Extension]。depends 用 parse_depends 拆分。
    用于把既有测试的 raw fixture 喂给新 API。"""
    extensions = {}
    for name, attrs in raw.items():
        ext_deps, path_deps = parse_depends(attrs.get("depends", []))
        extensions[name] = Extension(
            name=name,
            type=attrs.get("type", "skill"),
            enabled=attrs.get("enabled", False),
            description=attrs.get("description", ""),
            ext_deps=ext_deps,
            path_deps=[PathDep(p["source"], p["target"]) for p in path_deps],
            visible=attrs.get("visible", True),
        )
    return extensions


def test_pathdep_fields():
    d = PathDep(source="a.md", target="b.md")
    assert d.source == "a.md"
    assert d.target == "b.md"


def test_extension_defaults():
    e = Extension(name="x", type="skill", enabled=True, description="d")
    assert e.ext_deps == []
    assert e.path_deps == []
    assert e.visible is True


def test_extension_with_deps():
    e = Extension(
        name="x", type="skill", enabled=True, description="d",
        ext_deps=["y"], path_deps=[PathDep("s", "t")], visible=False,
    )
    assert e.ext_deps == ["y"]
    assert e.path_deps == [PathDep("s", "t")]
    assert e.visible is False


def test_config_defaults():
    c = Config(version=4, extensions={})
    assert c.warnings == []
    assert c.extra == {}


def test_changeset_is_frozen():
    cs = ChangeSet(to_enable=["a"], to_disable=[], cascade_disabled=[], rejected=[])
    assert cs.to_enable == ["a"]
    import pytest
    with pytest.raises(Exception):
        cs.to_enable = ["b"]   # frozen dataclass 不可赋值


def test_status_constants():
    assert Status.SUCCESS == "success"
    assert Status.MISSING == "missing"
    assert Status.OK == "ok"


def test_format_constants():
    assert Format.BOLD == "\\Zb"
    assert Format.RESET == "\\Zn"


def test_default_target_dir():
    assert DEFAULT_TARGET_DIR == "~/.config/opencode"


def test_resolve_target_dir_posix_no_drive():
    """On POSIX without a Windows drive path, just expand ~."""
    with patch("ext_mgr.os.name", "posix"), \
         patch("ext_mgr.os.path.expanduser", return_value="/home/user/.config/opencode"):
        assert resolve_target_dir("~/.config/opencode") == "/home/user/.config/opencode"


def test_resolve_target_dir_msys_win_home():
    """Under MSYS2/Git Bash with HOME=C:\\Users\\name, convert to /C/Users/name."""
    win_path = r"C:\Users\songj\.config\opencode"
    with patch("ext_mgr.os.name", "posix"), \
         patch("ext_mgr.os.path.expanduser", return_value=win_path):
        assert resolve_target_dir("~/.config/opencode") == "/C/Users/songj/.config/opencode"


def test_resolve_target_dir_msys_win_home_mixed_slash():
    """expanduser may produce mixed-slash paths like C:\\Users\\name/.config/..."""
    mixed = r"C:\Users\songj/.config/opencode"
    with patch("ext_mgr.os.name", "posix"), \
         patch("ext_mgr.os.path.expanduser", return_value=mixed):
        assert resolve_target_dir("~/.config/opencode") == "/C/Users/songj/.config/opencode"


def test_resolve_target_dir_native_windows_drive_to_posix():
    """Drive paths convert to POSIX /C/ form regardless of os.name (Git Bash
    consumes the result), so native Windows Python must convert too."""
    win_path = r"C:\Users\songj\.config\opencode"
    with patch("ext_mgr.os.name", "nt"), \
         patch("ext_mgr.os.path.expanduser", return_value=win_path):
        assert resolve_target_dir("~/.config/opencode") == "/C/Users/songj/.config/opencode"


def test_resolve_target_dir_native_windows_mixed_slash_to_posix():
    """expanduser('~/.config/opencode') under native Windows yields a mixed-slash
    path; it must still convert to /C/ form (the reported bug)."""
    mixed = r"C:\Users\songj/.config/opencode"
    with patch("ext_mgr.os.name", "nt"), \
         patch("ext_mgr.os.path.expanduser", return_value=mixed):
        assert resolve_target_dir("~/.config/opencode") == "/C/Users/songj/.config/opencode"


def test_resolve_target_dir_already_posix_drive():
    """Already-converted /C/Users/... should pass through unchanged."""
    with patch("ext_mgr.os.name", "posix"), \
         patch("ext_mgr.os.path.expanduser", return_value="/C/Users/songj/.config/opencode"):
        assert resolve_target_dir("/C/Users/songj/.config/opencode") == "/C/Users/songj/.config/opencode"


def test_to_posix_drive_path():
    """Pure converter: drive paths -> /C/ POSIX; others unchanged; idempotent."""
    assert _to_posix_drive_path(r"C:\Users\name\.config\opencode") == "/C/Users/name/.config/opencode"
    assert _to_posix_drive_path("C:/Users/name") == "/C/Users/name"
    # mixed separators as produced by expanduser under native Windows
    assert _to_posix_drive_path(r"C:\Users\name/.config/opencode") == "/C/Users/name/.config/opencode"
    # drive letter is upper-cased
    assert _to_posix_drive_path(r"d:\foo\bar") == "/D/foo/bar"
    # already-POSIX and non-drive paths pass through verbatim (idempotent)
    assert _to_posix_drive_path("/C/Users/name") == "/C/Users/name"
    assert _to_posix_drive_path("/home/user/.config") == "/home/user/.config"
    assert _to_posix_drive_path("relative/path") == "relative/path"
    # stray backslashes (e.g. os.path.join on Windows inserts one) are normalized
    assert _to_posix_drive_path(
        "/C/Users/songj/.config/opencode\\skills/skill-name"
    ) == "/C/Users/songj/.config/opencode/skills/skill-name"


def test_make_extensions_basic():
    exts = make_extensions({
        "a": {"type": "skill", "enabled": True, "description": "A",
              "ext_deps": ["b"], "path_deps": [("a.md", "a.md")]},
        "b": {"type": "agent", "enabled": False, "description": "B", "visible": False},
    })
    assert isinstance(exts["a"], Extension)
    assert exts["a"].ext_deps == ["b"]
    assert exts["a"].path_deps == [PathDep("a.md", "a.md")]
    assert exts["a"].visible is True
    assert exts["b"].type == "agent"
    assert exts["b"].visible is False
    assert exts["b"].ext_deps == []
    assert exts["b"].path_deps == []


def test_make_extensions_defaults():
    exts = make_extensions({"x": {"type": "command", "enabled": True}})
    assert exts["x"].description == ""
    assert exts["x"].visible is True


def _store_a():
    """a→b→c 链 + standalone，全部 enabled=False。"""
    return ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": False, "description": "A",
              "ext_deps": ["b"], "path_deps": [("a.md", "a.md")]},
        "b": {"type": "agent", "enabled": False, "description": "B",
              "ext_deps": ["c"], "path_deps": [("b.md", "b.md")]},
        "c": {"type": "agent", "enabled": False, "description": "C",
              "path_deps": [("c.md", "c.md")]},
        "standalone": {"type": "skill", "enabled": False, "description": "S",
                       "path_deps": [("s.md", "s.md")]},
    }))


def test_store_get_and_names():
    s = _store_a()
    assert s.get("a").name == "a"
    assert s.get("missing") is None
    assert set(s.names()) == {"a", "b", "c", "standalone"}


def test_store_by_type():
    s = _store_a()
    skills = s.by_type("skill")
    assert {e.name for e in skills} == {"a", "standalone"}


def test_store_adjacency():
    s = _store_a()
    assert s.deps_of("a") == {"b"}
    assert s.deps_of("b") == {"c"}
    assert s.dependents_of("b") == {"a"}
    assert s.dependents_of("c") == {"b"}
    assert s.dependents_of("standalone") == set()


def test_store_check_availability_all_present(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("x")
    s = ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": True, "description": "A",
              "ext_deps": ["b"], "path_deps": [("a.md", "a.md")]},
        "b": {"type": "agent", "enabled": True, "description": "B"},
    }), source_dir=str(src))
    assert s.check_availability("a") == []


def test_store_check_availability_ext_dep_missing():
    s = ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": True, "description": "A",
              "ext_deps": ["ghost"]},
    }))
    assert s.check_availability("a") == ["ghost"]


def test_store_check_availability_path_source_missing(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    s = ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": True, "description": "A",
              "path_deps": [("missing.md", "missing.md")]},
    }), source_dir=str(src))
    assert s.check_availability("a") == ["missing.md"]


def test_store_cascade_simple():
    # a→b，禁 a 后 b 无其他依赖者，应级联禁用 b
    s = ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": True, "description": "A", "ext_deps": ["b"]},
        "b": {"type": "agent", "enabled": True, "description": "B"},
    }))
    disabled = s.cascade_disable({"a"})
    assert disabled == {"a", "b"}
    assert s.get("a").enabled is False
    assert s.get("b").enabled is False


def test_store_cascade_keeps_if_other_dependent():
    # a→b, c→b；禁 a 时 b 仍被 c 依赖，不应级联
    s = ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": True, "description": "A", "ext_deps": ["b"]},
        "c": {"type": "skill", "enabled": True, "description": "C", "ext_deps": ["b"]},
        "b": {"type": "agent", "enabled": True, "description": "B"},
    }))
    disabled = s.cascade_disable({"a"})
    assert disabled == {"a"}
    assert s.get("b").enabled is True


def test_store_cascade_transitive():
    # a→b→c，禁 a 应级联禁 b、c
    s = ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": True, "description": "A", "ext_deps": ["b"]},
        "b": {"type": "agent", "enabled": True, "description": "B", "ext_deps": ["c"]},
        "c": {"type": "agent", "enabled": True, "description": "C"},
    }))
    disabled = s.cascade_disable({"a"})
    assert disabled == {"a", "b", "c"}


def test_store_resolve_single_ext():
    s = ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": False, "description": "A",
              "ext_deps": ["b"], "path_deps": [("a.md", "a.md")]},
        "b": {"type": "agent", "enabled": False, "description": "B",
              "ext_deps": ["c"], "path_deps": [("b.md", "b.md")]},
        "c": {"type": "agent", "enabled": False, "description": "C",
              "path_deps": [("c.md", "c.md")]},
        "standalone": {"type": "skill", "enabled": False, "description": "S",
                       "path_deps": [("s.md", "s.md")]},
    }))
    cs = s.resolve_changes(["standalone"])
    assert cs.to_enable == ["standalone"]
    assert "standalone" not in cs.to_disable


def test_store_resolve_transitive():
    s = ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": False, "description": "A", "ext_deps": ["b"]},
        "b": {"type": "agent", "enabled": False, "description": "B", "ext_deps": ["c"]},
        "c": {"type": "agent", "enabled": False, "description": "C"},
    }))
    cs = s.resolve_changes(["a"])
    assert cs.to_enable == ["a", "b", "c"]


def test_store_resolve_cascade_classification():
    # 全启用 → 选 []：a 显式禁，b/c 因 dependent 全禁而归入 cascade
    s = ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": True, "description": "A", "ext_deps": ["b"]},
        "b": {"type": "agent", "enabled": True, "description": "B", "ext_deps": ["c"]},
        "c": {"type": "agent", "enabled": True, "description": "C"},
    }))
    cs = s.resolve_changes([])
    assert cs.to_disable == ["a"]
    assert cs.cascade_disabled == ["b", "c"]
    assert cs.rejected == []


def test_store_resolve_reject_if_depended():
    # b/c 仍被 a 需要（a 在 to_enable）→ 不进入 to_disable
    s = ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": False, "description": "A",
              "ext_deps": ["b", "c"]},
        "b": {"type": "agent", "enabled": False, "description": "B"},
        "c": {"type": "agent", "enabled": False, "description": "C"},
    }))
    cs = s.resolve_changes(["a"])
    assert cs.to_enable == ["a", "b", "c"]
    assert cs.to_disable == []
    assert cs.rejected == []


def test_store_resolve_syncs_state():
    s = ExtensionStore(make_extensions({
        "a": {"type": "skill", "enabled": True, "description": "A"},
        "b": {"type": "skill", "enabled": False, "description": "B"},
    }))
    s.resolve_changes(["b"])
    assert s.get("a").enabled is False
    assert s.get("b").enabled is True


# ---------- NpmDependencyManager ----------

def test_install_for_skipped_on_windows(tmp_path):
    mgr = NpmDependencyManager(str(tmp_path))
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(tmp_path / "p"), "plugins/p")]},
    })
    with patch("ext_mgr.os.name", "nt"):
        results = mgr.install_for(["p"], exts)
    assert len(results) == 1
    assert results[0]["status"] == Status.SKIPPED


def test_npm_install_dir_source_success(tmp_path):
    pkg_dir = tmp_path / "myplugin"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text('{"name":"myplugin"}')
    exts = make_extensions({
        "myplugin": {"type": "plugin", "enabled": False,
                     "path_deps": [(str(pkg_dir), "plugins/myplugin")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    fake_proc = MagicMock(returncode=0, stderr="", stdout="")
    with patch("ext_mgr.subprocess.run", return_value=fake_proc) as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        results = mgr.install_for(["myplugin"], exts)
    assert len(results) == 1
    assert results[0]["status"] == Status.SUCCESS
    assert results[0]["name"] == "myplugin"
    assert mock_run.call_count == 1
    assert mock_run.call_args.kwargs["cwd"] == str(pkg_dir)


def test_npm_install_file_source_uses_dirname(tmp_path):
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text("{}")
    (pkg_dir / "index.js").write_text("// plugin")
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(pkg_dir / "index.js"), "plugins/p.js")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    fake = MagicMock(returncode=0, stderr="", stdout="")
    with patch("ext_mgr.subprocess.run", return_value=fake) as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        mgr.install_for(["p"], exts)
    assert mock_run.call_args.kwargs["cwd"] == str(pkg_dir)


def test_npm_install_no_package_json_skipped(tmp_path):
    pkg_dir = tmp_path / "nopkg"
    pkg_dir.mkdir()
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(pkg_dir), "plugins/p")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    with patch("ext_mgr.subprocess.run") as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        results = mgr.install_for(["p"], exts)
    assert results == []
    assert mock_run.call_count == 0


def test_npm_install_filters_non_plugin_types(tmp_path):
    pkg_dir = tmp_path / "sk"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text("{}")
    exts = make_extensions({
        "sk": {"type": "skill", "enabled": False,
               "path_deps": [(str(pkg_dir), "skills/sk")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    with patch("ext_mgr.subprocess.run") as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        results = mgr.install_for(["sk"], exts)
    assert results == []
    assert mock_run.call_count == 0


def test_npm_install_dedup_single_plugin_two_deps(tmp_path):
    pkg_dir = tmp_path / "p"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text("{}")
    (pkg_dir / "index.js").write_text("")
    (pkg_dir / "extra.js").write_text("")
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(pkg_dir / "index.js"), "a.js"),
                            (str(pkg_dir / "extra.js"), "b.js")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    fake = MagicMock(returncode=0, stderr="", stdout="")
    with patch("ext_mgr.subprocess.run", return_value=fake) as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        mgr.install_for(["p"], exts)
    assert mock_run.call_count == 1


def test_npm_install_dedup_two_plugins_shared_dir(tmp_path):
    pkg_dir = tmp_path / "shared"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text("{}")
    exts = make_extensions({
        "p1": {"type": "plugin", "enabled": False,
               "path_deps": [(str(pkg_dir), "plugins/p1")]},
        "p2": {"type": "plugin", "enabled": False,
               "path_deps": [(str(pkg_dir), "plugins/p2")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    fake = MagicMock(returncode=0, stderr="", stdout="")
    with patch("ext_mgr.subprocess.run", return_value=fake) as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        mgr.install_for(["p1", "p2"], exts)
    assert mock_run.call_count == 1


def test_npm_install_failure_returns_error(tmp_path):
    pkg_dir = tmp_path / "p"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text("{}")
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(pkg_dir), "plugins/p")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    fake = MagicMock(returncode=1, stderr="some install error", stdout="")
    with patch("ext_mgr.subprocess.run", return_value=fake), \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        results = mgr.install_for(["p"], exts)
    assert results[0]["status"] == Status.ERROR
    assert "some install error" in results[0]["detail"]


def test_npm_install_timeout_returns_error(tmp_path):
    pkg_dir = tmp_path / "p"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text("{}")
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(pkg_dir), "plugins/p")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    with patch("ext_mgr.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="npm", timeout=300)), \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        results = mgr.install_for(["p"], exts)
    assert results[0]["status"] == Status.ERROR
    assert "timed out" in results[0]["detail"]


def test_npm_install_npm_missing_returns_error(tmp_path):
    pkg_dir = tmp_path / "p"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text("{}")
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(pkg_dir), "plugins/p")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    with patch("ext_mgr.subprocess.run") as mock_run, \
         patch("ext_mgr.shutil.which", return_value=None):
        results = mgr.install_for(["p"], exts)
    assert results[0]["status"] == Status.ERROR
    assert "npm not installed" in results[0]["detail"]
    assert mock_run.call_count == 0


def test_npm_install_external_absolute_path(tmp_path):
    ext_dir = tmp_path / "external" / "dist"
    ext_dir.mkdir(parents=True)
    (ext_dir / "package.json").write_text("{}")
    (ext_dir / "index.js").write_text("")
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(ext_dir / "index.js"), "plugins/p.js")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    fake = MagicMock(returncode=0, stderr="", stdout="")
    with patch("ext_mgr.subprocess.run", return_value=fake) as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        results = mgr.install_for(["p"], exts)
    assert results[0]["status"] == Status.SUCCESS
    assert mock_run.call_args.kwargs["cwd"] == str(ext_dir)


def test_npm_install_verifies_deps_present_succeeds(tmp_path):
    pkg_dir = tmp_path / "p"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text(
        '{"name":"p","dependencies":{"lodash":"^4.0.0"}}')
    (pkg_dir / "node_modules" / "lodash").mkdir(parents=True)
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(pkg_dir), "plugins/p")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    fake = MagicMock(returncode=0, stderr="", stdout="")
    with patch("ext_mgr.subprocess.run", return_value=fake) as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        results = mgr.install_for(["p"], exts)
    assert results[0]["status"] == Status.SUCCESS
    assert mock_run.call_count == 1


def test_npm_install_retries_when_deps_missing_then_succeeds(tmp_path):
    pkg_dir = tmp_path / "p"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text(
        '{"name":"p","dependencies":{"lodash":"^4.0.0"}}')
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(pkg_dir), "plugins/p")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    state = {"n": 0}

    def fake_run(*args, **kwargs):
        state["n"] += 1
        if state["n"] >= 2:
            (pkg_dir / "node_modules" / "lodash").mkdir(parents=True, exist_ok=True)
        return MagicMock(returncode=0, stderr="", stdout="")

    with patch("ext_mgr.subprocess.run", side_effect=fake_run) as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        results = mgr.install_for(["p"], exts)
    assert results[0]["status"] == Status.SUCCESS
    assert mock_run.call_count == 2


def test_npm_install_verify_missing_all_retries(tmp_path):
    pkg_dir = tmp_path / "p"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text(
        '{"name":"p","dependencies":{"lodash":"^4.0.0"}}')
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(pkg_dir), "plugins/p")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    fake = MagicMock(returncode=0, stderr="", stdout="")
    with patch("ext_mgr.subprocess.run", return_value=fake) as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        results = mgr.install_for(["p"], exts)
    assert results[0]["status"] == Status.ERROR
    assert "verification" in results[0]["detail"]
    assert mock_run.call_count == 4


def test_npm_install_failure_retries_max_attempts(tmp_path):
    pkg_dir = tmp_path / "p"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text("{}")
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(pkg_dir), "plugins/p")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    fake = MagicMock(returncode=1, stderr="boom", stdout="")
    with patch("ext_mgr.subprocess.run", return_value=fake) as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        results = mgr.install_for(["p"], exts)
    assert results[0]["status"] == Status.ERROR
    assert "boom" in results[0]["detail"]
    assert mock_run.call_count == 4


def test_npm_install_timeout_does_not_retry(tmp_path):
    pkg_dir = tmp_path / "p"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text("{}")
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(pkg_dir), "plugins/p")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    with patch("ext_mgr.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="npm", timeout=300)) as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        results = mgr.install_for(["p"], exts)
    assert results[0]["status"] == Status.ERROR
    assert "timed out" in results[0]["detail"]
    assert mock_run.call_count == 1


def test_npm_install_verifies_scoped_dep(tmp_path):
    pkg_dir = tmp_path / "p"
    pkg_dir.mkdir()
    (pkg_dir / "package.json").write_text(
        '{"name":"p","dependencies":{"@types/node":"^20.0.0"}}')
    (pkg_dir / "node_modules" / "@types" / "node").mkdir(parents=True)
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(pkg_dir), "plugins/p")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    fake = MagicMock(returncode=0, stderr="", stdout="")
    with patch("ext_mgr.subprocess.run", return_value=fake) as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        results = mgr.install_for(["p"], exts)
    assert results[0]["status"] == Status.SUCCESS
    assert mock_run.call_count == 1


# ---------- 安装进度提示 ----------

def test_show_installing_progress_calls_infobox():
    adapter = MagicMock()
    store = ExtensionStore({}, source_dir="/fake")
    ui = DialogUI(adapter, store, MagicMock())
    ui.show_installing_progress("/some/pkg/dir")
    adapter.run_infobox.assert_called_once()
    args, _ = adapter.run_infobox.call_args
    assert "Installing" in args[0] and "dependencies" in args[0]
    assert "/some/pkg/dir" in args[0]


def test_npm_install_finds_package_json_in_parent_dir(tmp_path):
    # source 直接所在目录无 package.json，父目录有 -> 在父目录安装
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "package.json").write_text("{}")
    dist_dir = project_root / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.js").write_text("// plugin")
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(dist_dir / "index.js"), "plugins/p.js")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    fake = MagicMock(returncode=0, stderr="", stdout="")
    with patch("ext_mgr.subprocess.run", return_value=fake) as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        mgr.install_for(["p"], exts)
    assert mock_run.call_args.kwargs["cwd"] == str(project_root)


def test_npm_install_finds_package_json_in_grandparent_dir(tmp_path):
    # source 在 project/a/b/index.js，package.json 在 project（祖父目录）
    project_root = tmp_path / "projroot"
    project_root.mkdir()
    (project_root / "package.json").write_text("{}")
    sub = project_root / "a" / "b"
    sub.mkdir(parents=True)
    (sub / "index.js").write_text("// plugin")
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "path_deps": [(str(sub / "index.js"), "plugins/p.js")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    fake = MagicMock(returncode=0, stderr="", stdout="")
    with patch("ext_mgr.subprocess.run", return_value=fake) as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        mgr.install_for(["p"], exts)
    assert mock_run.call_args.kwargs["cwd"] == str(project_root)


def test_npm_install_invokes_progress_callback_per_dir(tmp_path):
    d1 = tmp_path / "p1"
    d1.mkdir()
    (d1 / "package.json").write_text("{}")
    d2 = tmp_path / "p2"
    d2.mkdir()
    (d2 / "package.json").write_text("{}")
    exts = make_extensions({
        "p1": {"type": "plugin", "enabled": False, "path_deps": [(str(d1), "a")]},
        "p2": {"type": "plugin", "enabled": False, "path_deps": [(str(d2), "b")]},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    fake = MagicMock(returncode=0, stderr="", stdout="")
    seen = []
    with patch("ext_mgr.subprocess.run", return_value=fake), \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        mgr.install_for(["p1", "p2"], exts, on_progress=seen.append)
    assert seen == [str(d1), str(d2)]


def test_run_infobox_builds_dialog_args(tmp_path):
    adapter = DialogAdapter()
    with patch("ext_mgr.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        adapter.run_infobox("正在安装插件依赖，请稍候...")
    cmd = mock_run.call_args.args[0]
    assert "--infobox" in cmd
    assert "正在安装插件依赖，请稍候..." in cmd


def test_npm_install_plugin_with_only_ext_deps_is_noop(tmp_path):
    exts = make_extensions({
        "p": {"type": "plugin", "enabled": False,
              "ext_deps": ["other"], "path_deps": []},
    })
    mgr = NpmDependencyManager(str(tmp_path))
    with patch("ext_mgr.subprocess.run") as mock_run, \
         patch("ext_mgr.shutil.which", return_value="/usr/bin/npm"):
        results = mgr.install_for(["p"], exts)
    assert results == []
    assert mock_run.call_count == 0
