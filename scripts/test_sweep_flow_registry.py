#!/usr/bin/env python3
"""測試 sweep_flow_registry.py：驗證邏輯與讀寫行為（不打真後端、用臨時 fake repo）。"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

THIS = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(THIS, "sweep_flow_registry.py")

# 直接 import 純函數 sweep() 來測分類邏輯
_spec = importlib.util.spec_from_file_location("sweep_flow_registry", SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _make_repo(tmp, defs):
    """建一個 fake repo，QATest/src/steps.py 裡放給定的 def。回 repo 路徑。"""
    src = os.path.join(tmp, "QATest", "src")
    os.makedirs(src, exist_ok=True)
    body = "\n".join(f"def {name}():\n    pass\n" for name in defs)
    with open(os.path.join(src, "steps.py"), "w", encoding="utf-8") as f:
        f.write(body or "# empty\n")
    return tmp


def _make_registry(path, names):
    data = {
        "_note": "test",
        "updated_at": "2020-01-01T00:00:00+00:00",
        "entries": [
            {"id": n, "name": n, "location": f"steps.py:1", "status": "verified"}
            for n in names
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


class TestSweepPure(unittest.TestCase):
    def test_splits_kept_and_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(tmp, ["alive_one", "alive_two"])
            entries = [
                {"name": "alive_one"},
                {"name": "gone_flow"},  # repo 裡沒有 → stale
                {"name": "alive_two"},
            ]
            kept, stale = _mod.sweep(entries, repo)
            self.assertEqual({e["name"] for e in kept}, {"alive_one", "alive_two"})
            self.assertEqual({e["name"] for e in stale}, {"gone_flow"})

    def test_nameless_entry_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(tmp, ["x"])
            kept, stale = _mod.sweep([{"name": ""}, {"id": "no-name"}], repo)
            self.assertEqual(kept, [])
            self.assertEqual(len(stale), 2)


class TestSweepCLI(unittest.TestCase):
    def _run(self, args):
        return subprocess.run(
            [sys.executable, SCRIPT, *args],
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(tmp, ["alive"])
            reg = os.path.join(tmp, "registry.json")
            _make_registry(reg, ["alive", "gone"])
            r = self._run(["--repo-path", repo, "--registry", reg])
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("stale=1", r.stdout)
            # dry-run 不得改檔：gone 仍在
            with open(reg, encoding="utf-8") as fh:
                after = json.load(fh)
            self.assertEqual(len(after["entries"]), 2)

    def test_apply_removes_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(tmp, ["alive"])
            reg = os.path.join(tmp, "registry.json")
            _make_registry(reg, ["alive", "gone"])
            r = self._run(["--repo-path", repo, "--registry", reg, "--apply"])
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(reg, encoding="utf-8") as fh:
                after = json.load(fh)
            self.assertEqual([e["name"] for e in after["entries"]], ["alive"])
            self.assertNotEqual(after["updated_at"], "2020-01-01T00:00:00+00:00")

    def test_mark_only_keeps_but_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(tmp, ["alive"])
            reg = os.path.join(tmp, "registry.json")
            _make_registry(reg, ["alive", "gone"])
            r = self._run(
                ["--repo-path", repo, "--registry", reg, "--apply", "--mark-only"]
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(reg, encoding="utf-8") as fh:
                after = json.load(fh)
            self.assertEqual(len(after["entries"]), 2)  # 全留
            by_name = {e["name"]: e for e in after["entries"]}
            self.assertEqual(by_name["gone"]["status"], "stale")
            self.assertEqual(by_name["alive"]["status"], "verified")

    def test_missing_repo_path_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = os.path.join(tmp, "registry.json")
            _make_registry(reg, ["x"])
            r = self._run(["--repo-path", os.path.join(tmp, "nope"), "--registry", reg])
            self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
