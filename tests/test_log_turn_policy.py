from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_PATH = REPO_ROOT / "log_turn.py"


def _load_hook_module():
    spec = importlib.util.spec_from_file_location("worklog_log_turn", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


class WorklogHookPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_hook_module()

    def test_normalize_summary_keeps_entry_brief(self) -> None:
        entry = self.mod.normalize_summary(
            {
                "question": "用户要求继续推进当前改动，并总结本轮对任务概览面板与汇总接口收口的处理结果。",
                "solution": (
                    "已将任务概览面板进一步收口到汇总接口。"
                    "前端同步改为直接消费这些字段，并更新了相关映射说明文档。"
                    "已完成相关单元测试验证。"
                ),
                "files": ["tests/unit/test_summary_handlers.py", "extra.py"],
            },
            ["tests/unit/test_summary_handlers.py"],
        )
        self.assertLessEqual(len(entry["question"]), 60)
        self.assertLessEqual(len(entry["solution"]), 120)
        self.assertEqual(entry["files"], ["tests/unit/test_summary_handlers.py"])

    def test_repository_examples_do_not_contain_local_machine_paths(self) -> None:
        home_path = str(Path.home())
        home_user = Path.home().name
        for relative_path in ("README.md", "WORKLOG_POLICY.md", "hooks.json"):
            content = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
            self.assertNotIn(home_path, content)
            self.assertNotIn(home_user, content)

    def test_test_harness_loads_repo_hook_module(self) -> None:
        self.assertEqual(HOOK_PATH, REPO_ROOT / "log_turn.py")

    def test_low_signal_summary_without_files_is_skipped(self) -> None:
        self.assertTrue(
            self.mod.should_skip_entry(
                {
                    "question": "用户要求将本轮结果整理为符合给定 schema 的中文 JSON。",
                    "solution": "已根据现有结论整理为 JSON 输出，无新增事实。",
                    "files": [],
                }
            )
        )

    def test_architecture_summary_without_files_is_kept(self) -> None:
        self.assertFalse(
            self.mod.should_skip_entry(
                {
                    "question": "统一任务架构方案收口",
                    "solution": "明确了三大平面与执行模式分层，后续实现可按文档推进。",
                    "files": [],
                }
            )
        )

    def test_append_and_replace_log_entry_updates_same_turn_block(self) -> None:
        payload = {"turn_id": "turn-123"}
        cached = {}
        entry = {
            "question": "初始问题",
            "solution": "初始结论",
            "files": ["a.py"],
        }
        updated = {
            "question": "更新问题",
            "solution": "更新结论",
            "files": ["b.py"],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            log_root = Path(tmpdir)
            self.mod.append_log(payload, cached, entry, log_root=log_root)
            target = log_root / f"raw-{self.mod.datetime.now().astimezone():%Y-%m-%d}.md"
            original = target.read_text(encoding="utf-8")
            self.assertIn("初始问题", original)
            self.assertIn("worklog-turn:turn-123:start", original)

            replaced = self.mod.replace_log_entry(payload, cached, updated, log_root=log_root)
            self.assertTrue(replaced)

            content = target.read_text(encoding="utf-8")
            self.assertIn("更新问题", content)
            self.assertIn("更新结论", content)
            self.assertIn("b.py", content)
            self.assertNotIn("初始问题", content)
            self.assertEqual(content.count("worklog-turn:turn-123:start"), 1)

    def test_local_summary_prefers_assistant_result_over_generic_fallback(self) -> None:
        entry = self.mod.build_local_summary(
            "排查线程卡死原因",
            (
                "已确认卡顿主要发生在 Stop hook。"
                "worklog 会同步启动一次 codex exec 生成摘要，所以线程会在收尾阶段等待。"
                "建议先改成本地摘要同步写入，再把 AI 摘要放到后台。"
            ),
            ["hooks.json"],
        )
        self.assertIn("排查线程卡死原因", entry["question"])
        self.assertIn("Stop hook", entry["solution"])
        self.assertIn("后台", entry["solution"])
        self.assertEqual(entry["files"], ["hooks.json"])

    def test_worklog_mode_defaults_to_legacy(self) -> None:
        with mock.patch.dict(self.mod.os.environ, {}, clear=False):
            self.assertEqual(self.mod.worklog_mode(), "legacy")

    def test_worklog_mode_accepts_optimized_switch(self) -> None:
        with mock.patch.dict(self.mod.os.environ, {"CODEX_WORKLOG_MODE": "optimized"}, clear=False):
            self.assertEqual(self.mod.worklog_mode(), "optimized")

    def test_prepare_log_entry_optimized_uses_local_summary_and_async_refine(self) -> None:
        with mock.patch.object(
            self.mod,
            "build_local_summary",
            return_value={"question": "本地问题", "solution": "本地结论", "files": ["a.py"]},
        ) as build_local, mock.patch.object(self.mod, "summarize_with_codex") as summarize:
            entry, should_refine_async = self.mod.prepare_log_entry(
                "optimized",
                Path("/tmp"),
                "用户问题",
                "助手回答",
                ["a.py"],
            )

        self.assertEqual(entry["question"], "本地问题")
        self.assertTrue(should_refine_async)
        build_local.assert_called_once_with("用户问题", "助手回答", ["a.py"])
        summarize.assert_not_called()

    def test_prepare_log_entry_legacy_prefers_sync_codex_summary(self) -> None:
        with mock.patch.object(
            self.mod,
            "summarize_with_codex",
            return_value={"question": "AI问题", "solution": "AI结论", "files": ["a.py", "x.py"]},
        ) as summarize, mock.patch.object(self.mod, "build_local_summary") as build_local:
            entry, should_refine_async = self.mod.prepare_log_entry(
                "legacy",
                Path("/tmp"),
                "用户问题",
                "助手回答",
                ["a.py"],
            )

        self.assertEqual(entry["question"], "AI问题")
        self.assertEqual(entry["files"], ["a.py"])
        self.assertFalse(should_refine_async)
        summarize.assert_called_once()
        build_local.assert_not_called()

    def test_prepare_log_entry_legacy_falls_back_when_codex_summary_missing(self) -> None:
        with mock.patch.object(self.mod, "summarize_with_codex", return_value=None), mock.patch.object(
            self.mod,
            "fallback_summary",
            return_value={"question": "兜底问题", "solution": "兜底结论", "files": ["a.py"]},
        ) as fallback:
            entry, should_refine_async = self.mod.prepare_log_entry(
                "legacy",
                Path("/tmp"),
                "用户问题",
                "助手回答",
                ["a.py"],
            )

        self.assertEqual(entry["question"], "兜底问题")
        self.assertFalse(should_refine_async)
        fallback.assert_called_once_with("用户问题", "助手回答", ["a.py"])


if __name__ == "__main__":
    unittest.main()
