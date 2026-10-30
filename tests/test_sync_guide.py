"""Integration tests: preserve the original guide and personal files using real Git."""

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync_guide.py"
GUIDE = "guides/how-to-live-better"
STATE = "sync/how-to-live-better.json"


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True,
        encoding="utf-8",
    ).stdout.strip()


def init_repo(root):
    root.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "core.autocrlf", "false")
    git(root, "config", "user.name", "Sync Test")
    git(root, "config", "user.email", "sync-test@example.invalid")


def commit(root, message):
    git(root, "add", "-A")
    git(root, "commit", "--quiet", "--allow-empty", "-m", message)
    return git(root, "rev-parse", "HEAD")


class SyncGuideTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.exists(), "sync implementation is missing")
        spec = importlib.util.spec_from_file_location("sync_guide", SCRIPT)
        self.sync = importlib.util.module_from_spec(spec)
        sys.modules["sync_guide"] = self.sync
        spec.loader.exec_module(self.sync)
        self.temp = tempfile.TemporaryDirectory(prefix="guide sync ")
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.source = base / "upstream"
        self.repo = base / "personal"
        init_repo(self.source)
        (self.source / "README.md").write_bytes(b"old guide\n")
        (self.source / "obsolete.md").write_bytes(b"remove later\n")
        self.old_commit = commit(self.source, "original")
        self.old_tree = git(self.source, "rev-parse", "HEAD^{tree}")
        init_repo(self.repo)
        destination = self.repo / GUIDE
        shutil.copytree(self.source, destination, ignore=shutil.ignore_patterns(".git"))
        (self.repo / "notes").mkdir()
        (self.repo / "notes" / "my-plan.md").write_bytes(b"my private draft stays here\r\n")
        (self.repo / "sync").mkdir()
        (self.repo / STATE).write_text(json.dumps({
            "commit": self.old_commit, "tree": self.old_tree,
            "file_count": 2, "synced_on": "2026-10-30",
        }) + "\n", encoding="utf-8")
        (self.repo / "UPSTREAM.md").write_text(
            "# Source\n\n<!-- guide-sync:start -->\nold status\n"
            "<!-- guide-sync:end -->\n\nPersonal provenance note.\n", encoding="utf-8",
        )
        commit(self.repo, "personal repository")

    def update_source(self):
        (self.source / "README.md").write_bytes(b"new guide\r\n")
        (self.source / "obsolete.md").unlink()
        (self.source / ".hidden").write_bytes(b"hidden\n")
        (self.source / "image.bin").write_bytes(b"\x00\xff\r\n\x01")
        return commit(self.source, "new chapter and files")

    def run_sync(self, url=None):
        return self.sync.prepare_update(
            self.repo, upstream_url=url or self.source.as_uri(), today="2026-11-02",
        )

    def test_update_preserves_exact_tree_personal_notes_and_provenance(self):
        new_commit = self.update_source()
        result = self.run_sync()
        self.assertTrue(result["changed"])
        self.assertEqual(result["upstream_commit"], new_commit)
        self.assertEqual(result["old_commit"], self.old_commit)
        self.assertEqual((result["added"], result["modified"], result["deleted"]), (2, 1, 1))
        index_tree = git(self.repo, "write-tree")
        self.assertEqual(git(self.repo, "rev-parse", f"{index_tree}:{GUIDE}"),
                         git(self.source, "rev-parse", "HEAD^{tree}"))
        self.assertEqual((self.repo / GUIDE / "README.md").read_bytes(), b"new guide\r\n")
        self.assertEqual((self.repo / GUIDE / "image.bin").read_bytes(), b"\x00\xff\r\n\x01")
        self.assertEqual((self.repo / "notes/my-plan.md").read_bytes(), b"my private draft stays here\r\n")
        self.assertIn("Personal provenance note.", (self.repo / "UPSTREAM.md").read_text(encoding="utf-8"))
        state = json.loads((self.repo / STATE).read_text(encoding="utf-8"))
        self.assertEqual((state["commit"], state["file_count"], state["synced_on"]),
                         (new_commit, 3, "2026-11-02"))
        changed = git(self.repo, "diff", "--cached", "--name-only").splitlines()
        self.assertTrue(all(p.startswith(GUIDE + "/") or p in {STATE, "UPSTREAM.md"} for p in changed))

    def test_no_update_leaves_no_changes_or_new_date(self):
        before = (self.repo / STATE).read_bytes()
        self.assertFalse(self.run_sync()["changed"])
        self.assertEqual((self.repo / STATE).read_bytes(), before)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_action_restage_preserves_source_despite_new_text_attributes(self):
        self.update_source()
        (self.source / ".gitattributes").write_bytes(b"*.md text eol=lf\n")
        git(self.source, "add", ".gitattributes")
        git(self.source, "commit", "--quiet", "-m", "add text attributes")
        # Source README was committed before the new attribute and still contains CRLF.
        original = subprocess.run(
            ["git", "-C", str(self.source), "show", "HEAD:README.md"],
            check=True, capture_output=True,
        ).stdout
        self.assertEqual(original, b"new guide\r\n")
        attributes = self.repo / ".git/info/attributes"
        attributes.write_bytes(b"notes/** -text")  # Preserve an existing unterminated rule.
        self.assertTrue(self.run_sync()["changed"])
        git(self.repo, "checkout", "--", ".")
        git(self.repo, "add", "-A", "--", GUIDE, STATE, "UPSTREAM.md")
        index_tree = git(self.repo, "write-tree")
        self.assertEqual(git(self.repo, "rev-parse", f"{index_tree}:{GUIDE}"),
                         git(self.source, "rev-parse", "HEAD^{tree}"))
        self.assertEqual((self.repo / GUIDE / "README.md").read_bytes(), original)
        self.assertTrue(attributes.read_bytes().startswith(b"notes/** -text\n"))

    def test_source_encoding_attributes_cannot_transform_imported_binary(self):
        self.update_source()
        (self.source / ".gitattributes").write_bytes(b"*.bin working-tree-encoding=UTF-16LE\n")
        git(self.source, "add", ".gitattributes")
        git(self.source, "commit", "--quiet", "-m", "add encoding attributes")
        self.assertTrue(self.run_sync()["changed"])
        git(self.repo, "add", "-A", "--", GUIDE, STATE, "UPSTREAM.md")
        index_tree = git(self.repo, "write-tree")
        self.assertEqual(git(self.repo, "rev-parse", f"{index_tree}:{GUIDE}"),
                         git(self.source, "rev-parse", "HEAD^{tree}"))
        self.assertEqual((self.repo / GUIDE / "image.bin").read_bytes(), b"\x00\xff\r\n\x01")

    def test_second_run_after_import_is_a_noop(self):
        self.update_source()
        self.run_sync()
        commit(self.repo, "accept guide update")
        self.assertFalse(self.run_sync()["changed"])
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_new_commit_without_content_change_is_a_noop(self):
        commit(self.source, "metadata only")
        self.assertFalse(self.run_sync()["changed"])
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_committed_personal_guide_edits_are_not_overwritten(self):
        (self.repo / GUIDE / "README.md").write_bytes(b"my annotations\n")
        commit(self.repo, "annotate guide")
        self.update_source()
        with self.assertRaises(RuntimeError):
            self.run_sync()
        self.assertEqual((self.repo / GUIDE / "README.md").read_bytes(), b"my annotations\n")
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_dirty_personal_files_stop_before_changes(self):
        self.update_source()
        (self.repo / "notes/my-plan.md").write_bytes(b"unfinished note\n")
        with self.assertRaises(RuntimeError):
            self.run_sync()
        self.assertEqual((self.repo / GUIDE / "README.md").read_bytes(), b"old guide\n")
        self.assertEqual((self.repo / "notes/my-plan.md").read_bytes(), b"unfinished note\n")

    def test_fetch_failure_does_not_remove_existing_guide(self):
        before = (self.repo / STATE).read_bytes()
        with self.assertRaises((RuntimeError, subprocess.CalledProcessError)):
            self.run_sync((Path(self.temp.name) / "missing-source").as_uri())
        self.assertEqual((self.repo / GUIDE / "README.md").read_bytes(), b"old guide\n")
        self.assertEqual((self.repo / STATE).read_bytes(), before)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_invalid_provenance_stops_before_changes(self):
        self.update_source()
        (self.repo / "UPSTREAM.md").write_text("manual provenance\n", encoding="utf-8")
        commit(self.repo, "change provenance layout")
        with self.assertRaises((RuntimeError, ValueError)):
            self.run_sync()
        self.assertEqual((self.repo / GUIDE / "README.md").read_bytes(), b"old guide\n")
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_empty_upstream_is_rejected_before_removing_guide(self):
        git(self.source, "rm", "-r", ".")
        commit(self.source, "empty source")
        with self.assertRaises(RuntimeError):
            self.run_sync()
        self.assertEqual((self.repo / GUIDE / "README.md").read_bytes(), b"old guide\n")
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
