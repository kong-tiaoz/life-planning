"""Prepare a byte-preserving guide update; publication is handled by the workflow."""

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess


SOURCE_URL = "https://github.com/eternity4719/HowToLiveBetter.git"
SOURCE_WEB = "https://github.com/eternity4719/HowToLiveBetter"
SOURCE_REF = "refs/remotes/guide-sync/main"
GUIDE = "guides/how-to-live-better"
STATE = "sync/how-to-live-better.json"
START = "<!-- guide-sync:start -->"
END = "<!-- guide-sync:end -->"


def git(root, *args, raw=False):
    result = subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True,
        timeout=300,
    )
    return result.stdout if raw else result.stdout.decode("utf-8").strip()


def prepare_update(root, upstream_url=SOURCE_URL, today=None):
    """Stage only the guide and its provenance; refuse to overwrite personal edits."""
    root = Path(root).resolve()
    if git(root, "status", "--porcelain=v1", "--untracked-files=all", "-z", raw=True):
        raise RuntimeError("Worktree has local changes; refusing to overwrite files.")
    state = json.loads((root / STATE).read_text(encoding="utf-8"))
    if not all(isinstance(state.get(key), str) and re.fullmatch(r"[0-9a-f]{40}", state[key])
               for key in ("commit", "tree")):
        raise ValueError("Invalid guide version record.")
    if git(root, "rev-parse", f"HEAD:{GUIDE}") != state["tree"]:
        raise RuntimeError("Guide has been modified locally; keep personal notes outside the guide.")
    provenance = (root / "UPSTREAM.md").read_text(encoding="utf-8")
    if provenance.count(START) != 1 or provenance.count(END) != 1:
        raise ValueError("UPSTREAM.md must contain exactly one sync status block.")
    start, end = provenance.index(START), provenance.index(END)
    if start >= end:
        raise ValueError("Invalid sync status block order.")
    synced_on = date.fromisoformat(today or datetime.now(
        timezone(timedelta(hours=8))).date().isoformat()).isoformat()

    git(root, "fetch", "--no-tags", "--depth=1", "--", upstream_url,
        f"refs/heads/main:{SOURCE_REF}")
    upstream_commit = git(root, "rev-parse", SOURCE_REF)
    upstream_tree = git(root, "rev-parse", f"{SOURCE_REF}^{{tree}}")
    if upstream_tree == state["tree"]:
        return {"changed": False}
    entries = git(root, "ls-tree", "-rz", "--full-tree", SOURCE_REF, raw=True)
    file_count = sum(entry.split(b" ", 2)[1] == b"blob"
                     for entry in entries.split(b"\0") if entry)
    if not file_count:
        raise RuntimeError("Upstream guide is empty; refusing to remove the existing guide.")

    # create-pull-request stages the files again. Its checkout/add must not apply
    # text, filter or encoding rules from the imported project's .gitattributes.
    # info/attributes has higher precedence and only affects this local checkout.
    attributes = Path(git(root, "rev-parse", "--git-path", "info/attributes"))
    if not attributes.is_absolute():
        attributes = root / attributes
    attributes.parent.mkdir(parents=True, exist_ok=True)
    existing = attributes.read_bytes() if attributes.exists() else b""
    separator = b"\n" if existing and not existing.endswith(b"\n") else b""
    attributes.write_bytes(existing + separator +
        b"guides/how-to-live-better/** -text -filter -ident -working-tree-encoding\n")

    # The index imports the source tree directly, preserving bytes and Git file modes.
    git(root, "rm", "-r", "--", GUIDE)
    git(root, "-c", "core.autocrlf=false", "read-tree", f"--prefix={GUIDE}/", "-u", SOURCE_REF)
    new_state = {"commit": upstream_commit, "tree": upstream_tree,
                 "file_count": file_count, "synced_on": synced_on}
    (root / STATE).write_text(json.dumps(new_state, indent=2) + "\n", encoding="utf-8")
    block = (
        f"{START}\n"
        f"- 当前同步提交：`{upstream_commit}`\n"
        f"- 固定版本：{SOURCE_WEB}/tree/{upstream_commit}\n"
        f"- 最新同步日期：{synced_on}（Asia/Shanghai）\n"
        f"- 文件数：{file_count}\n"
        f"- 目录 Git tree SHA：`{upstream_tree}`\n"
        f"{END}"
    )
    (root / "UPSTREAM.md").write_text(
        provenance[:start] + block + provenance[end + len(END):], encoding="utf-8",
    )
    git(root, "add", "--", STATE, "UPSTREAM.md")
    changed_paths = git(root, "diff", "--cached", "--name-only", "-z", raw=True)
    for raw_path in changed_paths.split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8")
        if not (path.startswith(GUIDE + "/") or path in (STATE, "UPSTREAM.md")):
            raise RuntimeError(f"Update escaped the allowed paths: {path}")
    index_tree = git(root, "write-tree")
    if git(root, "rev-parse", f"{index_tree}:{GUIDE}") != upstream_tree:
        raise RuntimeError("Imported tree does not match upstream.")
    statuses = git(root, "diff", "--cached", "--no-renames", "--name-status", "-z",
                   "--", GUIDE, raw=True).split(b"\0")
    counts = {"added": 0, "modified": 0, "deleted": 0}
    for status in statuses[0:-1:2]:
        key = {b"A": "added", b"D": "deleted"}.get(status[:1], "modified")
        counts[key] += 1
    return {"changed": True, "old_commit": state["commit"],
            "upstream_commit": upstream_commit, "upstream_short": upstream_commit[:7],
            "tree": upstream_tree, "file_count": file_count, **counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--body-file", type=Path)
    args = parser.parse_args()
    try:
        result = prepare_update(args.repo)
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        else:
            detail = str(exc)
        parser.exit(1, f"Guide sync stopped: {detail}\n")
    if result["changed"]:
        body = (
            "## 《高性价比人生指南》更新提案\n\n"
            f"- 原项目：{SOURCE_WEB}\n"
            f"- 当前版本：`{result['old_commit']}`\n"
            f"- 新版本：`{result['upstream_commit']}`\n"
            f"- [查看作者的更新]({SOURCE_WEB}/compare/"
            f"{result['old_commit']}...{result['upstream_commit']})\n"
            f"- 指南文件变化：新增 {result['added']}，修改 {result['modified']}，"
            f"删除 {result['deleted']}；更新后共 {result['file_count']} 个文件。\n"
            f"- 已核对完整目录 Git tree：`{result['tree']}`。\n\n"
            "本提案只更新指南及来源记录，原作者署名与许可证随原文件保留。"
            "个人规划、笔记和其他目录不在同步范围内。请查看 Files changed 后自行决定是否合并。\n"
        )
        if args.body_file:
            args.body_file.parent.mkdir(parents=True, exist_ok=True)
            args.body_file.write_text(body, encoding="utf-8")
        summary = body
    else:
        summary = "指南内容没有变化，无需创建新的更新提案。\n"
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"changed={str(result['changed']).lower()}\n")
            if result["changed"]:
                stream.write(f"upstream_short={result['upstream_short']}\n")
    if summary_file := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary_file, "a", encoding="utf-8") as stream:
            stream.write(summary)
    print(summary)


if __name__ == "__main__":
    main()
