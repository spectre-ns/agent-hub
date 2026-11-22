"""A running hub runs a module only as it was reviewed and installed, and swaps modules in whole."""
import shutil

from conftest import M


def _served(tmp_path, monkeypatch):
    """A served hub and a command line on the same database, with a copy of the dms module that the test may edit."""
    from practicehub import pack as packmod
    from practicehub.hub import Hub
    monkeypatch.setenv("PRACTICEHUB_SECRET", "test-secret")
    mods = tmp_path / "modules"
    shutil.copytree(packmod.MODULES_DIR / "dms", mods / "dms", ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(packmod, "MODULES_DIR", mods)
    served = Hub(tmp_path / "served", pack="general-practice")
    served.install("general-practice", demo=True)
    return served, Hub(tmp_path / "cli"), mods / "dms"


def test_a_module_whose_definitions_changed_on_disk_is_not_loaded(tmp_path, monkeypatch):
    """pack.py is the same, but a definition was edited after the review: the module is not run, and it says why."""
    from practicehub.hub import Hub
    served, cli, d = _served(tmp_path, monkeypatch)
    cli.approve_module_code(M(cli, "dr-amara"), "dms")
    cli.install_module(M(cli, "rana"), "dms")
    wf = d / "workflows" / "dms-review.toml"
    wf.write_text(wf.read_text() + "\n# edited after the review\n")
    st = served.module_status("dms")
    assert st["state"] == "not_running" and "reviewed" in st["why"]
    assert "dms" not in {m.id for m in served.pack.modules}
    assert any(e["action"] == "module.refused" for e in served.track.list(limit=20))
    again = Hub(tmp_path / "again")  # a hub that starts now refuses it too
    assert "dms" in again.module_problems and "dms" not in {m.id for m in again.pack.modules}


def test_a_reload_puts_the_new_modules_in_place_whole(tmp_path, monkeypatch):
    """Another thread holding the practice while modules reload sees the old modules or the new, never half."""
    served, cli, _ = _served(tmp_path, monkeypatch)
    before = served.pack
    had = (list(before.modules), dict(before.actions), dict(before.guards))
    cli.approve_module_code(M(cli, "dr-amara"), "dms")
    cli.install_module(M(cli, "rana"), "dms")
    assert served.sync_modules()
    assert served.pack is not before and "dms" in {m.id for m in served.pack.modules}
    assert (before.modules, before.actions, before.guards) == had  # what a reader held is as it was
    loaded = served.pack
    cli.uninstall_module(M(cli, "rana"), "dms", "not used")
    assert served.sync_modules()
    assert "dms" in {m.id for m in loaded.modules} and "dms" not in {m.id for m in served.pack.modules}
