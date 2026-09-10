#!/usr/bin/env python3
"""`resolve_skills_version()` 的測試 —— 守的是「量不到版本這件事本身要看得見」。

背景：`skills_version` 9/07 上線後，`root@catherine.liu` 送出的 16 筆全是空字串。
空字串在 dashboard 上跟「舊 sender 沒送這欄」長得一模一樣（都是灰色的「未回報」），
所以兩個月沒人發現她那台量不到版本。這裡釘住三件事：

- 量不到時要講**原因**（no-git / not-a-repo / timeout），三種原因處理方式不同。
- 後綴（dirty `+`、遮蔽 `!N`）壞掉**不可以把 rev 一起丟掉**——那是改版前的真 bug：
  dirty 那步一噴例外，整個函式回 ""，明明版本量得到。
- 不管哪條路都要回得出字串，絕不 raise（它跑在 hook 裡）。

跑法：python3 scripts/test_telemetry_identity.py
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import telemetry_identity as ti  # noqa: E402

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


class _Run:
    """假的 subprocess.run：依 argv 決定要回什麼／噴什麼。"""

    def __init__(self, rev=("abc1234", 0), status=("", 0)):
        self.rev, self.status = rev, status

    def __call__(self, argv, **kw):
        which = self.rev if "rev-parse" in argv else self.status
        if isinstance(which, BaseException):
            raise which
        out, rc = which
        return subprocess.CompletedProcess(argv, rc, stdout=out, stderr="")


def _resolve(monkey_run, shadow=""):
    """清掉 module 級快取後跑一次；`shadow` 是 `_shadow_suffix` 的假回值。"""
    ti._SKILLS_VERSION = None
    real_run, real_shadow = subprocess.run, ti._shadow_suffix
    subprocess.run, ti._shadow_suffix = monkey_run, lambda: shadow
    try:
        return ti.resolve_skills_version()
    finally:
        subprocess.run, ti._shadow_suffix = real_run, real_shadow
        ti._SKILLS_VERSION = None


class TestSkillsVersion(unittest.TestCase):
    def test_乾淨的_clone_就是純_rev(self):
        self.assertEqual(_resolve(_Run()), "abc1234")

    def test_dirty_加號(self):
        self.assertEqual(_resolve(_Run(status=(" M scripts/x.py\n", 0))), "abc1234+")

    def test_被遮蔽的數量接在後面(self):
        self.assertEqual(_resolve(_Run(), shadow="!2"), "abc1234!2")
        self.assertEqual(
            _resolve(_Run(status=(" M x\n", 0)), shadow="!2"), "abc1234+!2"
        )

    def test_沒裝_git_講明是沒裝(self):
        self.assertEqual(
            _resolve(_Run(rev=FileNotFoundError("git"))), "unknown:no-git"
        )

    def test_不是_clone_講明不是_clone(self):
        """catherine 那台最可能的情況：download zip / rsync 過來，沒有 .git。"""
        self.assertEqual(_resolve(_Run(rev=("", 128))), "unknown:not-a-repo")

    def test_逾時講明逾時(self):
        self.assertEqual(
            _resolve(_Run(rev=subprocess.TimeoutExpired("git", 2))), "unknown:timeout"
        )

    def test_絕不回空字串(self):
        """空字串跟『舊 sender 沒送這欄』在 dashboard 上分不出來，這是原本的問題。"""
        for r in (FileNotFoundError(), subprocess.TimeoutExpired("git", 2),
                  RuntimeError(), ("", 128), ("", 0)):
            self.assertNotEqual(_resolve(_Run(rev=r)), "", f"{r!r} 回了空字串")

    def test_後綴壞掉不可以把_rev_一起丟掉(self):
        """改版前的真 bug：dirty 那步噴例外 ⇒ 整個函式回 ""，明明 rev 量得到。"""
        self.assertEqual(_resolve(_Run(status=RuntimeError("boom"))), "abc1234")
        self.assertEqual(
            _resolve(_Run(status=subprocess.TimeoutExpired("git", 3))), "abc1234"
        )

    def test_結果有快取_不會每筆遙測都去戳_git(self):
        calls = []

        def counting(argv, **kw):
            calls.append(argv)
            return _Run()(argv, **kw)

        ti._SKILLS_VERSION = None
        real = subprocess.run
        subprocess.run = counting
        try:
            ti.resolve_skills_version()
            n = len(calls)
            ti.resolve_skills_version()
            self.assertEqual(len(calls), n, "第二次呼叫不該再戳 git")
        finally:
            subprocess.run = real
            ti._SKILLS_VERSION = None


class TestShadowSuffix(unittest.TestCase):
    """`!N` 的來源是 link_assets.sh 寫的清單檔。"""

    def _with_conflicts(self, body):
        with tempfile.TemporaryDirectory() as root:
            if body is not None:
                os.makedirs(os.path.join(root, "harness"))
                with open(os.path.join(root, "harness", "link_conflicts.txt"), "w",
                          encoding="utf-8") as f:
                    f.write(body)
            old = os.environ.get("CLAUDE_CONFIG_DIR")
            os.environ["CLAUDE_CONFIG_DIR"] = root
            try:
                return ti._shadow_suffix()
            finally:
                if old is None:
                    del os.environ["CLAUDE_CONFIG_DIR"]
                else:
                    os.environ["CLAUDE_CONFIG_DIR"] = old

    def test_數行數(self):
        self.assertEqual(self._with_conflicts("/a/x\n/a/y\n"), "!2")

    def test_空檔就是沒有衝突(self):
        self.assertEqual(self._with_conflicts(""), "")

    def test_空白行不算(self):
        self.assertEqual(self._with_conflicts("/a/x\n\n\n"), "!1")

    def test_檔案不存在也不能噴(self):
        """還沒跑過新版 link_assets.sh 的機器。無依據說它有衝突 ⇒ 留空。"""
        self.assertEqual(self._with_conflicts(None), "")


if __name__ == "__main__":
    unittest.main()
