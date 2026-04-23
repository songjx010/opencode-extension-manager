import json
import os
import pytest
from ext_mgr import parse_depends, ConfigManager, ConfigError


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
        "version": 2,
        "extensions": {
            "brainstorming": {
                "type": "skill",
                "enabled": True,
                "description": "头脑风暴",
                "depends": [
                    {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
                ],
            },
            "kernel-dev": {
                "type": "agent",
                "enabled": False,
                "description": "Kernel开发",
                "depends": [
                    "brainstorming",
                    {"source": "agents/kernel.md", "target": "agents/kernel.md"},
                ],
            },
        },
    }


def test_validate_version2_ok(tmp_path):
    p = _write_config(tmp_path, _valid_config())
    mgr = ConfigManager(p)
    config = mgr.load()
    assert config["version"] == 2
    assert config["warnings"] == []


def test_validate_version1_rejected(tmp_path):
    cfg = _valid_config()
    cfg["version"] = 1
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="不支持的 version: 1"):
        ConfigManager(p).load()


def test_validate_missing_version(tmp_path):
    cfg = _valid_config()
    del cfg["version"]
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="缺少 version 字段"):
        ConfigManager(p).load()


def test_validate_type_skill_ok(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["brainstorming"]["type"] = "skill"
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert config["extensions"]["brainstorming"]["type"] == "skill"


def test_validate_type_plugin_ok(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["brainstorming"]["type"] = "plugin"
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert config["extensions"]["brainstorming"]["type"] == "plugin"


def test_validate_type_unknown_rejected(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["brainstorming"]["type"] = "unknown"
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="type 'unknown' 不合法"):
        ConfigManager(p).load()


def test_validate_missing_type(tmp_path):
    cfg = _valid_config()
    del cfg["extensions"]["brainstorming"]["type"]
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="缺少 type 字段"):
        ConfigManager(p).load()


def test_validate_key_with_slash_rejected(tmp_path):
    cfg = _valid_config()
    ext = cfg["extensions"].pop("brainstorming")
    cfg["extensions"]["skills/brainstorming"] = ext
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="格式错误"):
        ConfigManager(p).load()


def test_validate_key_with_dotdot_rejected(tmp_path):
    cfg = _valid_config()
    ext = cfg["extensions"].pop("brainstorming")
    cfg["extensions"]["../evil"] = ext
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="非法字符"):
        ConfigManager(p).load()


def test_validate_depends_path_dep_missing_source(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["brainstorming"]["depends"] = [
        {"target": "skills/brainstorming.md"}
    ]
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="缺少 source 或 target 字段"):
        ConfigManager(p).load()


def test_validate_depends_invalid_type(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["brainstorming"]["depends"] = [123]
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="依赖类型不合法"):
        ConfigManager(p).load()


def test_validate_ext_dep_not_exist_warning(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["brainstorming"]["depends"] = ["nonexistent"]
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert any("nonexistent" in w for w in config["warnings"])


def test_validate_ext_dep_with_slash_rejected(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["brainstorming"]["depends"] = ["skills/other"]
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="格式错误"):
        ConfigManager(p).load()


def test_validate_ext_dep_empty_rejected(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["brainstorming"]["depends"] = [""]
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="扩展依赖名称不能为空"):
        ConfigManager(p).load()


def test_validate_empty_extensions_ok(tmp_path):
    cfg = {"version": 2, "extensions": {}}
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert config["extensions"] == {}


def test_validate_empty_depends_ok(tmp_path):
    cfg = _valid_config()
    cfg["extensions"]["brainstorming"]["depends"] = []
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert config["warnings"] == []


def test_no_cycle(tmp_path):
    cfg = _valid_config()
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert config["warnings"] == []


def test_simple_cycle(tmp_path):
    cfg = {
        "version": 2,
        "extensions": {
            "a": {
                "type": "skill",
                "enabled": True,
                "description": "A",
                "depends": ["b"],
            },
            "b": {
                "type": "agent",
                "enabled": True,
                "description": "B",
                "depends": ["a"],
            },
        },
    }
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="循环依赖"):
        ConfigManager(p).load()


def test_three_node_cycle(tmp_path):
    cfg = {
        "version": 2,
        "extensions": {
            "a": {
                "type": "skill",
                "enabled": True,
                "description": "A",
                "depends": ["b"],
            },
            "b": {
                "type": "agent",
                "enabled": True,
                "description": "B",
                "depends": ["c"],
            },
            "c": {
                "type": "command",
                "enabled": True,
                "description": "C",
                "depends": [
                    "a",
                    {"source": "c.md", "target": "c.md"},
                ],
            },
        },
    }
    p = _write_config(tmp_path, cfg)
    with pytest.raises(ConfigError, match="循环依赖"):
        ConfigManager(p).load()


def test_cycle_with_path_deps_no_false_positive(tmp_path):
    cfg = {
        "version": 2,
        "extensions": {
            "a": {
                "type": "skill",
                "enabled": True,
                "description": "A",
                "depends": [
                    {"source": "a.md", "target": "a.md"},
                ],
            },
            "b": {
                "type": "agent",
                "enabled": True,
                "description": "B",
                "depends": [
                    "a",
                    {"source": "b.md", "target": "b.md"},
                ],
            },
        },
    }
    p = _write_config(tmp_path, cfg)
    config = ConfigManager(p).load()
    assert config["warnings"] == []


from ext_mgr import DependencyResolver


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
    resolver = DependencyResolver()
    exts = _extensions_for_resolver()
    result = resolver.resolve(["standalone"], exts)
    assert result["to_enable"] == ["standalone"]
    assert "standalone" not in result["to_disable"]


def test_resolve_with_ext_dep():
    resolver = DependencyResolver()
    exts = _extensions_for_resolver()
    result = resolver.resolve(["a"], exts)
    assert "a" in result["to_enable"]
    assert "b" in result["to_enable"]


def test_resolve_transitive_deps():
    resolver = DependencyResolver()
    exts = _extensions_for_resolver()
    result = resolver.resolve(["a"], exts)
    assert sorted(result["to_enable"]) == ["a", "b", "c"]


def test_resolve_disable_no_cascade():
    resolver = DependencyResolver()
    exts = _extensions_for_resolver()
    result = resolver.resolve(["a"], exts)
    assert "standalone" in result["to_disable"]


def test_resolve_reject_if_depended():
    resolver = DependencyResolver()
    exts = _extensions_for_resolver()
    exts["a"]["enabled"] = True
    result = resolver.resolve(["a"], exts)
    rejected_names = [r["name"] for r in result["rejected"]]
    assert "b" not in rejected_names
    assert "c" not in rejected_names


def test_resolve_ext_dep_not_in_extensions():
    resolver = DependencyResolver()
    exts = {
        "a": {
            "type": "skill",
            "enabled": False,
            "description": "A",
            "depends": ["nonexistent"],
        }
    }
    result = resolver.resolve(["a"], exts)
    assert result["to_enable"] == ["a"]


def test_resolve_all_enabled_no_disable():
    resolver = DependencyResolver()
    exts = _extensions_for_resolver()
    result = resolver.resolve(["a", "standalone"], exts)
    assert result["to_enable"] == ["a", "b", "c", "standalone"]
    assert result["to_disable"] == []
    assert result["rejected"] == []


from ext_mgr import SymlinkManager


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
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
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
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
    results = mgr.apply_for_extension("brainstorming", exts, "create")
    assert results[0]["status"] == "skipped"


def test_create_symlink_conflict(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "target" / "skills").mkdir()
    conflict = tmp_path / "target" / "skills" / "brainstorming.md"
    conflict.write_text("other")
    mgr = SymlinkManager(source, target)
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
    results = mgr.apply_for_extension("brainstorming", exts, "create")
    assert results[0]["status"] == "conflict"


def test_remove_symlink_success(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "source" / "skills").mkdir()
    src_file = tmp_path / "source" / "skills" / "brainstorming.md"
    src_file.write_text("skill")
    (tmp_path / "target" / "skills").mkdir()
    os.symlink(str(src_file), os.path.join(target, "skills", "brainstorming.md"))
    mgr = SymlinkManager(source, target)
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
    results = mgr.apply_for_extension("brainstorming", exts, "remove")
    assert results[0]["status"] == "success"
    assert not os.path.exists(os.path.join(target, "skills", "brainstorming.md"))


def test_remove_symlink_not_exist(tmp_path):
    source, target = _setup_dirs(tmp_path)
    mgr = SymlinkManager(source, target)
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
    results = mgr.apply_for_extension("brainstorming", exts, "remove")
    assert results[0]["status"] == "skipped"


def test_apply_for_extension_multiple_paths(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "source" / "skills").mkdir()
    (tmp_path / "source" / "skills" / "main.md").write_text("main")
    (tmp_path / "source" / "skills" / "helper.md").write_text("helper")
    mgr = SymlinkManager(source, target)
    exts = {
        "multi": {
            "type": "skill",
            "enabled": True,
            "description": "Multi",
            "depends": [
                {"source": "skills/main.md", "target": "skills/main.md"},
                {"source": "skills/helper.md", "target": "skills/helper.md"},
            ],
        }
    }
    results = mgr.apply_for_extension("multi", exts, "create")
    assert len(results) == 2
    assert all(r["status"] == "success" for r in results)


def test_apply_for_extension_no_path_deps(tmp_path):
    source, target = _setup_dirs(tmp_path)
    mgr = SymlinkManager(source, target)
    exts = {
        "pure-dep": {
            "type": "skill",
            "enabled": True,
            "description": "PureDep",
            "depends": ["other-ext"],
        }
    }
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
    exts = {
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
    }
    results = mgr.apply_changes(["ext-a"], ["ext-b"], exts)
    success_names = [r["name"] for r in results if r["status"] == "success"]
    assert "skills/a.md" in success_names
    assert "skills/b.md" in success_names


from ext_mgr import Validator


def test_validate_enabled_ok(tmp_path):
    source, target = _setup_dirs(tmp_path)
    (tmp_path / "source" / "skills").mkdir()
    src_file = tmp_path / "source" / "skills" / "brainstorming.md"
    src_file.write_text("skill")
    (tmp_path / "target" / "skills").mkdir()
    os.symlink(str(src_file), os.path.join(target, "skills", "brainstorming.md"))
    validator = Validator(source, target)
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
    results = validator.validate(exts)
    assert len(results) == 1
    assert results[0]["status"] == "ok"


def test_validate_enabled_missing(tmp_path):
    source, target = _setup_dirs(tmp_path)
    validator = Validator(source, target)
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
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
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": False,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
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
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
    results = validator.validate(exts)
    assert any(r["status"] == "broken" for r in results)


def test_validate_no_target_dir(tmp_path):
    source = str(tmp_path / "source")
    target = str(tmp_path / "nonexistent_target")
    os.makedirs(source)
    validator = Validator(source, target)
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
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
    exts = {
        "multi": {
            "type": "skill",
            "enabled": True,
            "description": "Multi",
            "depends": [
                {"source": "skills/main.md", "target": "skills/main.md"},
                {"source": "skills/helper.md", "target": "skills/helper.md"},
            ],
        }
    }
    results = validator.validate(exts)
    statuses = [r["status"] for r in results]
    assert "ok" not in statuses
    assert any(s == "missing" for s in statuses)


from unittest.mock import MagicMock
from ext_mgr import DialogUI


def _make_ui(source_dir="/fake"):
    adapter = MagicMock()
    config_mgr = MagicMock()
    ui = DialogUI(adapter, config_mgr, source_dir)
    return ui


def test_check_availability_all_present(tmp_path):
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "brainstorming.md").write_text("skill")
    ui = _make_ui(str(tmp_path))
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
    missing = ui._check_availability("brainstorming", exts)
    assert missing == []


def test_check_availability_ext_dep_missing(tmp_path):
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "brainstorming.md").write_text("skill")
    ui = _make_ui(str(tmp_path))
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": ["nonexistent-ext"],
        }
    }
    missing = ui._check_availability("brainstorming", exts)
    assert "nonexistent-ext" in missing


def test_check_availability_path_dep_source_missing(tmp_path):
    ui = _make_ui(str(tmp_path))
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
            "depends": [
                {"source": "skills/brainstorming.md", "target": "skills/brainstorming.md"}
            ],
        }
    }
    missing = ui._check_availability("brainstorming", exts)
    assert "skills/brainstorming.md" in missing


def test_build_checklist_items_filters_by_type():
    ui = _make_ui()
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
    items, unavailable = ui._build_checklist_items(exts, "skill")
    names = [i[0] for i in items]
    assert "brainstorming" in names
    assert "kernel-dev" not in names


def test_build_checklist_items_filters_agent():
    ui = _make_ui()
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
    items, unavailable = ui._build_checklist_items(exts, "agent")
    names = [i[0] for i in items]
    assert "kernel-dev" in names
    assert "brainstorming" not in names


def test_count_stats_by_type():
    ui = _make_ui()
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
    total, enabled, ok = ui._count_stats(exts, "skill")
    assert total == 2
    assert enabled == 1


def test_count_stats_empty_type():
    ui = _make_ui()
    exts = {
        "brainstorming": {
            "type": "skill",
            "enabled": True,
            "description": "Brainstorm",
        },
    }
    total, enabled, ok = ui._count_stats(exts, "plugin")
    assert total == 0
    assert enabled == 0


def test_cascade_simple():
    resolver = DependencyResolver()
    exts = {
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
    }
    result = resolver.resolve(["b"], exts)
    assert result["to_enable"] == ["b"]
    assert result["to_disable"] == ["a"]
    assert result["cascade_disabled"] == []
    assert result["rejected"] == []


def test_cascade_recursive():
    resolver = DependencyResolver()
    exts = {
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
    }
    result = resolver.resolve([], exts)
    assert result["to_disable"] == ["a"]
    assert sorted(result["cascade_disabled"]) == ["b", "c"]


def test_cascade_stopped_by_other_dependent():
    resolver = DependencyResolver()
    exts = {
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
    }
    result = resolver.resolve(["d"], exts)
    assert "a" in result["to_disable"]
    assert "b" in result["cascade_disabled"]
    assert "c" not in result["cascade_disabled"]


def test_cascade_respects_user_selection():
    resolver = DependencyResolver()
    exts = {
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
    }
    result = resolver.resolve(["b"], exts)
    assert result["to_enable"] == ["b"]
    assert "b" not in result["cascade_disabled"]


def test_cascade_shared_dep_disabled_together():
    resolver = DependencyResolver()
    exts = {
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
    }
    result = resolver.resolve([], exts)
    assert sorted(result["to_disable"]) == ["a", "b"]
    assert result["cascade_disabled"] == ["c"]


def test_cascade_no_cascade_when_dep_in_selected():
    resolver = DependencyResolver()
    exts = {
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
    }
    result = resolver.resolve(["standalone"], exts)
    assert "b" in result["cascade_disabled"]
    assert "standalone" in result["to_enable"]


def test_cascade_with_existing_test_data():
    resolver = DependencyResolver()
    exts = _extensions_for_resolver()
    result = resolver.resolve([], exts)
    assert "a" in result["to_disable"]
    assert sorted(result["cascade_disabled"]) == ["b", "c"]
    assert "standalone" in result["to_disable"]


def test_cascade_disabled_in_result_for_no_change():
    resolver = DependencyResolver()
    exts = _extensions_for_resolver()
    result = resolver.resolve(["a", "standalone"], exts)
    assert result["cascade_disabled"] == []


def _make_ui_for_summary():
    adapter = MagicMock()
    config_mgr = MagicMock()
    adapter.run_yesno.return_value = 0
    ui = DialogUI(adapter, config_mgr, "/fake")
    return ui, adapter


def test_show_change_summary_with_cascade():
    ui, adapter = _make_ui_for_summary()
    changes = {
        "to_enable": ["x"],
        "to_disable": ["a"],
        "cascade_disabled": ["b", "c"],
        "rejected": [],
    }
    ui.show_change_summary(changes)
    call_args = adapter.run_yesno.call_args
    text = call_args[0][1]
    assert "禁用" in text
    assert "级联禁用" in text
    assert "b" in text
    assert "c" in text


def test_show_change_summary_no_cascade():
    ui, adapter = _make_ui_for_summary()
    changes = {
        "to_enable": ["x"],
        "to_disable": ["a"],
        "cascade_disabled": [],
        "rejected": [],
    }
    ui.show_change_summary(changes)
    call_args = adapter.run_yesno.call_args
    text = call_args[0][1]
    assert "级联禁用" not in text


def test_show_change_summary_cascade_before_rejected():
    ui, adapter = _make_ui_for_summary()
    changes = {
        "to_enable": [],
        "to_disable": ["a"],
        "cascade_disabled": ["b"],
        "rejected": [{"name": "c", "reason": "被依赖", "dependents": ["d"]}],
    }
    ui.show_change_summary(changes)
    call_args = adapter.run_yesno.call_args
    text = call_args[0][1]
    lines = text.split("\n")
    cascade_idx = next(i for i, l in enumerate(lines) if "级联禁用" in l)
    rejected_idx = next(i for i, l in enumerate(lines) if "拒绝禁用" in l)
    assert cascade_idx < rejected_idx


def test_cascade_disable_deps_disables_child():
    ui = _make_ui()
    exts = {
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
    }
    ui._cascade_disable_deps({"parent"}, exts)
    assert exts["child"]["enabled"] is False


def test_cascade_disable_deps_keeps_child_if_other_parent_enabled():
    ui = _make_ui()
    exts = {
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
    }
    ui._cascade_disable_deps({"parent-a"}, exts)
    assert exts["shared-child"]["enabled"] is True


def test_cascade_disable_deps_transitive():
    ui = _make_ui()
    exts = {
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
    }
    ui._cascade_disable_deps({"parent"}, exts)
    assert exts["mid"]["enabled"] is False
    assert exts["leaf"]["enabled"] is False


def test_show_type_checklist_cascades_disable_across_types():
    adapter = MagicMock()
    config_mgr = MagicMock()
    ui = DialogUI(adapter, config_mgr, "/fake")
    exts = {
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
    }
    adapter.run_checklist.return_value = (0, [], [])
    ui._show_type_checklist(exts, "skill")
    assert exts["parent"]["enabled"] is False
    assert exts["child-agent"]["enabled"] is False
