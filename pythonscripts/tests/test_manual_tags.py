import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import manual_tags
import tagdataset


def _write(path, data):
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _read(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_snapshot_captures_only_listed_fields(tmp_path):
    _write(tmp_path / "a.yml", {
        "title": "A",
        "description_tags": ["Agriculture"],
        "anova_tags": {"good-for-one-way-anova": True},
        "manual_tags": ["description_tags"],
    })
    _write(tmp_path / "b.yml", {"title": "B", "description_tags": ["Transportation"]})
    snap = manual_tags.snapshot(tmp_path)
    assert snap == {"a.yml": {"description_tags": ["Agriculture"]}}


def test_restore_reverts_pipeline_overwrites(tmp_path):
    path = tmp_path / "a.yml"
    _write(path, {"title": "A", "description_tags": ["Agriculture"], "manual_tags": ["description_tags"]})
    snap = manual_tags.snapshot(tmp_path)
    _write(path, {"title": "A", "description_tags": ["Transportation"], "manual_tags": ["description_tags"]})

    changed = manual_tags.restore(tmp_path, snap)

    assert changed == ["a.yml"]
    assert _read(path)["description_tags"] == ["Agriculture"]


def test_restore_leaves_untouched_files_alone(tmp_path):
    path = tmp_path / "a.yml"
    _write(path, {"description_tags": ["Agriculture"], "manual_tags": ["description_tags"]})
    before = path.read_bytes()
    assert manual_tags.restore(tmp_path, manual_tags.snapshot(tmp_path)) == []
    assert path.read_bytes() == before


def test_is_manual():
    assert manual_tags.is_manual({"manual_tags": ["description_tags"]}, "description_tags")
    assert not manual_tags.is_manual({}, "description_tags")
    assert not manual_tags.is_manual({"manual_tags": "description_tags"}, "description_tags")


def test_tagdataset_skips_manual_description_tags(tmp_path, monkeypatch):
    monkeypatch.setattr(tagdataset, "TAG_RULES", {"Transportation": ["bus"]})
    path = tmp_path / "a.yml"
    _write(path, {"title": "bus routes", "description_tags": ["Agriculture"], "manual_tags": ["description_tags"]})
    tagdataset.process_file(path=path, dry_run=False)
    assert _read(path)["description_tags"] == ["Agriculture"]


def test_tagdataset_still_tags_when_not_manual(tmp_path, monkeypatch):
    monkeypatch.setattr(tagdataset, "TAG_RULES", {"Transportation": ["bus"]})
    path = tmp_path / "a.yml"
    _write(path, {"title": "bus routes", "description_tags": ["Agriculture"]})
    tagdataset.process_file(path=path, dry_run=False)
    assert _read(path)["description_tags"] == ["Transportation"]
