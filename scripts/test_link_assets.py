#!/usr/bin/env python3
"""link_assets.sh 與 session_autopull.sh 的 symlink 行為測試。

鎖住的 regression：新增的 skill/agent 對「早就裝過」的人不會出現在 ~/.claude
（symlink 只讓已連上的檔案跟著 git pull，新增檔案不會自己冒出來）。
"""
import os
import subprocess
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}


def write(path: str, body: str = "x") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)


def fake_repo(root: str) -> str:
    """做一個假 clone：只放測試要用的 assets + 真的 link_assets.sh / session_autopull.sh。"""
    repo = os.path.join(os.path.realpath(root), "repo")  # macOS /tmp 是 symlink，先解掉
    write(os.path.join(repo, "agents", "foo-agent.md"), "---\nname: foo\n---\n")
    write(os.path.join(repo, "skills", "tools", "foo-skill", "SKILL.md"), "# foo\n")
    write(os.path.join(repo, "skills", "workflows", "foo-skill", "SKILL.md"), "# dup\n")
    for s in ("link_assets.sh", "session_autopull.sh"):
        with open(os.path.join(SCRIPTS, s), encoding="utf-8") as f:
            write(os.path.join(repo, "scripts", s), f.read())
    return repo


def run(script: str, repo: str, claude_dir: str, *args: str, cwd: str | None = None):
    env = {**os.environ, **GIT_ENV, "CLAUDE_CONFIG_DIR": claude_dir}
    return subprocess.run(["bash", os.path.join(repo, "scripts", script), *args],
                          capture_output=True, text=True, env=env, cwd=cwd or repo)


class TestLinkAssets(unittest.TestCase):
    def test_links_agents_and_dedupes_skills(self):
        with tempfile.TemporaryDirectory() as root:
            repo, claude = fake_repo(root), os.path.join(root, "claude")
            self.assertEqual(run("link_assets.sh", repo, claude).returncode, 0)
            agent = os.path.join(claude, "agents", "foo-agent.md")
            skill = os.path.join(claude, "skills", "foo-skill")
            self.assertTrue(os.path.islink(agent))
            self.assertEqual(os.path.realpath(agent),
                             os.path.join(repo, "agents", "foo-agent.md"))
            # 同名 skill 兩處都有時取 tools 版（first-wins）
            self.assertEqual(os.path.realpath(skill),
                             os.path.join(repo, "skills", "tools", "foo-skill"))

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            repo, claude = fake_repo(root), os.path.join(root, "claude")
            run("link_assets.sh", repo, claude)
            r = run("link_assets.sh", repo, claude)
            self.assertEqual(r.returncode, 0)
            self.assertIn("relink foo-agent.md", r.stdout)
            self.assertTrue(os.path.islink(os.path.join(claude, "agents", "foo-agent.md")))

    def test_does_not_clobber_real_file(self):
        with tempfile.TemporaryDirectory() as root:
            repo, claude = fake_repo(root), os.path.join(root, "claude")
            write(os.path.join(claude, "agents", "foo-agent.md"), "MINE")
            run("link_assets.sh", repo, claude)
            with open(os.path.join(claude, "agents", "foo-agent.md"), encoding="utf-8") as f:
                self.assertEqual(f.read(), "MINE")

    def test_quiet_prints_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            repo, claude = fake_repo(root), os.path.join(root, "claude")
            r = run("link_assets.sh", repo, claude, "--quiet")
            self.assertEqual(r.stdout, "")


class TestAutopullRelinks(unittest.TestCase):
    """autopull 必須 (1) 對『本 script 的 clone』動作、(2) 沒更新也補 symlink。"""

    def _git_repo(self, root: str) -> str:
        repo = fake_repo(root)
        env = {**os.environ, **GIT_ENV}
        for cmd in (["git", "init", "-q", "-b", "master"], ["git", "add", "-A"],
                    ["git", "commit", "-qm", "init"]):
            subprocess.run(cmd, cwd=repo, env=env, check=True, capture_output=True)
        return repo

    def test_relinks_when_already_up_to_date(self):
        with tempfile.TemporaryDirectory() as root:
            repo, claude = self._git_repo(root), os.path.join(root, "claude")
            other = os.path.join(root, "other_project")  # session 開在別的 repo
            os.makedirs(other)
            # 無 remote → pull 失敗（等同「HEAD 已最新／離線」），symlink 仍該補上
            r = run("session_autopull.sh", repo, claude, cwd=other)
            self.assertEqual(r.returncode, 0)
            self.assertTrue(os.path.islink(os.path.join(claude, "agents", "foo-agent.md")))
            self.assertEqual(r.stdout, "")  # 無更新時不可有輸出（hook stdout 有協定）


if __name__ == "__main__":
    unittest.main()
