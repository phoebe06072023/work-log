#!/usr/bin/env python3

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional


def installed_codex_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def session_cwd(payload: dict) -> Path:
    return Path(payload.get("cwd") or os.getcwd()).resolve()


def git_root(cwd: Path) -> Optional[Path]:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    return Path(output).resolve() if output else None


def workspace_root(payload: dict, cached: dict) -> Path:
    cached_root = cached.get("workspace_root")
    if cached_root:
        return Path(cached_root).resolve()
    root = git_root(session_cwd(payload))
    return root if root is not None else session_cwd(payload)


def codex_dir(payload: dict, cached: dict) -> Path:
    override = os.environ.get("CODEX_HOOK_BASE_DIR")
    if override:
        return Path(override).resolve()

    root = workspace_root(payload, cached)
    if (root / ".git").exists():
        target = root / ".codex"
        try:
            target.mkdir(parents=True, exist_ok=True)
            return target
        except OSError:
            return installed_codex_dir()
    return installed_codex_dir()


def shared_log_dir(payload: dict, cached: dict) -> Path:
    fixed_repo = os.environ.get("WORKLOG_REPO_DIR")
    if fixed_repo:
        return Path(fixed_repo).resolve()

    root = workspace_root(payload, cached)
    if (root / ".git").exists():
        return root / "work-log"
    return codex_dir(payload, cached) / "work-log"


def state_dir(payload: dict, cached: dict) -> Path:
    return codex_dir(payload, cached) / "work-log" / ".state"


def summary_schema_path() -> Path:
    return Path(__file__).resolve().with_name("summary_schema.json")


def async_job_dir(payload: dict, cached: dict) -> Path:
    return state_dir(payload, cached) / "jobs"


def sanitize_text(text: str) -> str:
    sanitized = text or ""
    replacements = [
        (r"sk-[A-Za-z0-9_-]{8,}", "[已脱敏]"),
        (r"(?i)\bBearer\s+[A-Za-z0-9._\-]+\b", "Bearer [已脱敏]"),
        (
            r"(?i)\b(api[_\- ]?key|token|secret|password|passwd)\b\s*[:=]\s*\S+",
            lambda match: f"{match.group(1)}=[已脱敏]",
        ),
        (r"[\w.\-]+@[\w.\-]+\.\w+", "[已脱敏邮箱]"),
        (r"(?<!\d)(?:\+?\d[\d \-]{7,}\d)(?!\d)", "[已脱敏电话]"),
    ]
    for pattern, replacement in replacements:
        sanitized = re.sub(pattern, replacement, sanitized)
    return sanitized.strip()


def truncate_text(text: str, limit: int = 1200) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


INTERNAL_PROMPT_MARKERS = (
    "your job is to provide a short title for a task that will be created from that prompt",
    "generate a concise ui title (18-36 characters) for this task",
    "generate a clear, informative task title based solely on the prompt provided",
    "你是一个工作日志摘要助手。请根据以下内容输出 json",
)


def is_internal_prompt(prompt: str) -> bool:
    compact = " ".join((prompt or "").split()).lower()
    return any(marker in compact for marker in INTERNAL_PROMPT_MARKERS)


def load_state(payload: dict) -> dict:
    session_id = payload.get("session_id", "unknown")
    override = os.environ.get("CODEX_HOOK_BASE_DIR")
    roots: list[Path] = []
    if override:
        roots.append(Path(override).resolve())

    repo_root = git_root(session_cwd(payload))
    if repo_root is not None:
        roots.append(repo_root / ".codex")

    roots.append(installed_codex_dir())

    for root in roots:
        candidate = root / "work-log" / ".state" / f"session-{session_id}.json"
        if not candidate.exists():
            continue
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    return {}


def save_state(payload: dict, cached: dict, updated: dict) -> None:
    state_root = state_dir(payload, cached)
    state_root.mkdir(parents=True, exist_ok=True)
    session_id = payload.get("session_id", "unknown")
    target = state_root / f"session-{session_id}.json"
    target.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")


def snapshot_git_status(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "-uall"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def status_map(lines: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in lines:
        if len(line) < 4:
            continue
        mapping[line[3:]] = line[:2]
    return mapping


def changed_files_since_snapshot(before: list[str], after: list[str]) -> list[str]:
    previous = status_map(before)
    current = status_map(after)
    changed: list[str] = []
    for path, state in current.items():
        if previous.get(path) != state:
            changed.append(path)
    return sorted(changed)


def extract_paths_from_text(root: Path, text: str) -> list[str]:
    matches = re.findall(r"(?:`|^|\s)([A-Za-z0-9_./-]+\.[A-Za-z0-9_]+)(?:`|$|\s)", text or "")
    results: list[str] = []
    for raw in matches:
        if raw.startswith(("http://", "https://")):
            continue
        candidate = (root / raw).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.exists():
            rel = str(candidate.relative_to(root))
            if rel not in results:
                results.append(rel)
    return results


def shutil_which(binary: str) -> Optional[str]:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / binary
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def summarize_with_codex(
    root: Path, prompt: str, assistant_message: str, candidate_files: list[str]
) -> Optional[dict]:
    if os.environ.get("CODEX_HOOK_DISABLE_AI") == "1":
        return None

    codex_binary = shutil_which("codex")
    if not codex_binary:
        return None

    candidate_block = "\n".join(f"- {path}" for path in candidate_files) or "- 无"
    prompt_text = f"""
你是一个工作日志摘要助手。请根据以下内容输出 JSON，且必须满足给定 schema。

要求：
1. 使用简体中文。
2. `question` 只写一个简短主题句，优先概括事项，而不是复述用户原话。
3. `solution` 只写 1-2 句短句，只保留关键决策、真实结果、重要 blocker 或明确下一步；忽略普通搜索、重复验证、格式整理、schema 转换、纯 JSON 改写等过程噪音。
4. 如果本轮只是格式转换、结论转述、闲聊，或没有新增事实，写“无实质性变更”。
5. `files` 只能从候选文件中挑选真正相关的关键文件；如果没有则输出空数组，最多 3 个。
6. 严禁输出敏感信息。任何 API Key、密码、Token、Secret、邮箱、手机号、地址、姓名等都要替换成 `[已脱敏]`。
7. 不要输出 markdown，不要输出代码块，只输出 JSON。

用户问题：
{truncate_text(prompt or "无", limit=600)}

AI 最终回答：
{truncate_text(assistant_message or "无", limit=2400)}

候选文件：
{candidate_block}
""".strip()

    with tempfile.TemporaryDirectory(prefix="codex-hook-summary-") as tmpdir:
        output_file = Path(tmpdir) / "summary.json"
        command = [
            codex_binary,
            "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "-C",
            str(root),
            "-c",
            "features.codex_hooks=false",
            "-c",
            "features.plugins=false",
            "-c",
            'model_reasoning_effort="low"',
            "--output-schema",
            str(summary_schema_path()),
            "-o",
            str(output_file),
            prompt_text,
        ]
        result = subprocess.run(
            command,
            cwd=root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if result.returncode != 0 or not output_file.exists():
            return None
        try:
            summary = json.loads(output_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    return summary if isinstance(summary, dict) else None


def worklog_mode() -> str:
    raw = os.environ.get("CODEX_WORKLOG_MODE", "").strip().lower()
    if raw in {"1", "true", "on", "optimized", "opt"}:
        return "optimized"
    return "legacy"


def fallback_summary(prompt: str, assistant_message: str, candidate_files: list[str]) -> dict:
    question = truncate_text(prompt or "本轮未识别到明确问题。", limit=90)
    solution = (
        "已根据本轮回答生成摘要并写入工作日志。"
        if assistant_message
        else "无实质性变更"
    )
    return {
        "question": question,
        "solution": truncate_text(solution, limit=160),
        "files": candidate_files[:5],
    }


def prepare_log_entry(
    mode: str, root: Path, prompt: str, assistant_message: str, candidate_files: list[str]
) -> tuple[dict, bool]:
    if mode == "optimized":
        return build_local_summary(prompt, assistant_message, candidate_files), True

    summary = summarize_with_codex(root, prompt, assistant_message, candidate_files)
    if summary:
        return normalize_summary(summary, candidate_files), False

    return fallback_summary(prompt, assistant_message, candidate_files), False


def summarize_text_locally(text: str, limit: int) -> str:
    compact = truncate_text(sanitize_text(text), limit=limit)
    if not compact:
        return ""

    chunks = [
        chunk.strip(" ;；。")
        for chunk in re.split(r"[。！？!?]\s*|\n+", compact)
        if chunk.strip()
    ]
    if not chunks:
        return compact

    selected: list[str] = []
    preferred = [
        chunk
        for chunk in chunks
        if any(keyword in chunk for keyword in ("建议", "下一步", "改成", "切到", "改为", "根因", "确认"))
    ]
    for chunk in chunks[:1] + preferred:
        if len(chunk) < 6 or chunk in selected:
            continue
        selected.append(chunk)
        if len("；".join(selected)) >= limit - 10 or len(selected) >= 2:
            break
    return truncate_text("；".join(selected), limit=limit)


def build_local_summary(prompt: str, assistant_message: str, candidate_files: list[str]) -> dict:
    question = truncate_text(sanitize_text(prompt or "本轮未识别到明确问题。"), limit=60)
    solution = summarize_text_locally(assistant_message, limit=120)
    if not solution:
        solution = "已记录本轮关键结论，AI 精修改为后台补充。"
    return {
        "question": question,
        "solution": solution,
        "files": candidate_files[:3],
    }


def normalize_summary(summary: dict, candidate_files: list[str]) -> dict:
    question = sanitize_text(str(summary.get("question", "")).strip()) or "本轮未识别到明确问题。"
    solution = sanitize_text(str(summary.get("solution", "")).strip()) or "无实质性变更"
    raw_files = summary.get("files", [])
    selected: list[str] = []
    if isinstance(raw_files, list):
        allowed = set(candidate_files)
        for item in raw_files:
            path = str(item).strip()
            if path in allowed and path not in selected:
                selected.append(path)
    return {
        "question": truncate_text(question, limit=60),
        "solution": truncate_text(solution, limit=120),
        "files": selected[:3],
    }


LOW_SIGNAL_PATTERNS = (
    "符合给定 schema",
    "中文 json",
    "只输出 json",
    "生成摘要",
    "工作日志摘要",
    "整理为 json",
    "schema 转换",
    "格式转换",
)


def should_skip_entry(entry: dict) -> bool:
    question = str(entry.get("question", "")).strip().lower()
    solution = str(entry.get("solution", "")).strip().lower()
    files = entry.get("files", [])
    if solution == "无实质性变更":
        return True
    if files:
        return False
    combined = f"{question}\n{solution}"
    return any(pattern in combined for pattern in LOW_SIGNAL_PATTERNS)


def turn_marker(turn_id: Optional[str]) -> str:
    return turn_id or "unknown"


def entry_block(now: datetime, turn_id: Optional[str], entry: dict) -> str:
    marker = turn_marker(turn_id)
    file_label = "、".join(entry["files"]) if entry["files"] else "无"
    return (
        "\n---\n"
        f"<!-- worklog-turn:{marker}:start -->\n"
        f"### [{now:%H:%M}]\n"
        f"**来源：** Codex\n"
        f"**问题：** {entry['question']}\n"
        f"**解决：** {entry['solution']}\n"
        f"**涉及文件：** {file_label}\n"
        f"<!-- worklog-turn:{marker}:end -->\n"
        "---\n"
    )


def append_log(payload: dict, cached: dict, entry: dict, log_root: Optional[Path] = None) -> Path:
    now = datetime.now().astimezone()
    log_root = log_root or shared_log_dir(payload, cached)
    log_root.mkdir(parents=True, exist_ok=True)
    target = log_root / f"raw-{now:%Y-%m-%d}.md"

    if not target.exists():
        target.write_text(f"# 工作日志 {now:%Y-%m-%d}\n", encoding="utf-8")

    block = entry_block(now, payload.get("turn_id"), entry)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(block)
    return target


def replace_log_entry(
    payload: dict, cached: dict, entry: dict, log_root: Optional[Path] = None
) -> bool:
    log_root = log_root or shared_log_dir(payload, cached)
    marker = turn_marker(payload.get("turn_id"))
    pattern = re.compile(
        rf"\n---\n<!-- worklog-turn:{re.escape(marker)}:start -->\n.*?\n<!-- worklog-turn:{re.escape(marker)}:end -->\n---\n",
        re.DOTALL,
    )

    for target in sorted(log_root.glob("raw-*.md"), reverse=True):
        content = target.read_text(encoding="utf-8")
        match = pattern.search(content)
        if not match:
            continue
        existing_time = datetime.now().astimezone()
        time_match = re.search(r"### \[(\d{2}):(\d{2})\]", match.group(0))
        if time_match:
            existing_time = existing_time.replace(
                hour=int(time_match.group(1)),
                minute=int(time_match.group(2)),
                second=0,
                microsecond=0,
            )
        updated = pattern.sub(entry_block(existing_time, payload.get("turn_id"), entry), content, count=1)
        target.write_text(updated, encoding="utf-8")
        return True
    return False


def display_path(root: Path, payload: dict, cached: dict, target: Path) -> str:
    for base in (root, codex_dir(payload, cached)):
        try:
            return str(target.relative_to(base))
        except ValueError:
            continue
    return str(target)


def queue_async_summary(
    payload: dict,
    cached: dict,
    root: Path,
    prompt: str,
    assistant_message: str,
    candidate_files: list[str],
    target: Path,
) -> bool:
    if os.environ.get("CODEX_HOOK_DISABLE_ASYNC_AI") == "1":
        return False
    if os.environ.get("CODEX_HOOK_DISABLE_AI") == "1":
        return False
    if not assistant_message.strip():
        return False
    codex_binary = shutil_which("codex")
    if not codex_binary:
        return False

    job_root = async_job_dir(payload, cached)
    job_root.mkdir(parents=True, exist_ok=True)
    marker = turn_marker(payload.get("turn_id"))
    job_path = job_root / f"{payload.get('session_id', 'unknown')}-{marker}.json"
    job = {
        "payload": payload,
        "cached": cached,
        "root": str(root),
        "prompt": prompt,
        "assistant_message": assistant_message,
        "candidate_files": candidate_files,
        "log_path": str(target),
    }
    job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--refine", str(job_path)],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return True


def run_async_refine(job_path: Path) -> int:
    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0

    payload = job.get("payload", {})
    cached = job.get("cached", {})
    root = Path(job.get("root", ".")).resolve()
    prompt = sanitize_text(job.get("prompt", ""))
    assistant_message = sanitize_text(job.get("assistant_message", ""))
    candidate_files = [str(item) for item in job.get("candidate_files", [])]
    summary = summarize_with_codex(root, prompt, assistant_message, candidate_files)
    if summary:
        entry = normalize_summary(summary, candidate_files)
        if not should_skip_entry(entry):
            replace_log_entry(payload, cached, entry, log_root=Path(job["log_path"]).resolve().parent)

    try:
        job_path.unlink()
    except OSError:
        pass
    return 0


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--refine":
        return run_async_refine(Path(sys.argv[2]))

    payload = json.load(sys.stdin)
    cached = load_state(payload)
    turn_id = payload.get("turn_id")
    if cached.get("logged_turn_id") and cached.get("logged_turn_id") == turn_id:
        print(json.dumps({"continue": True}, ensure_ascii=False))
        return 0

    root = workspace_root(payload, cached)
    prompt = sanitize_text(cached.get("prompt", ""))
    cached_turn_id = cached.get("turn_id")
    if not prompt or is_internal_prompt(prompt) or (turn_id and cached_turn_id != turn_id):
        print(json.dumps({"continue": True}, ensure_ascii=False))
        return 0
    assistant_message = sanitize_text(payload.get("last_assistant_message", ""))
    before = cached.get("git_status", [])
    after = snapshot_git_status(root)

    candidate_files = changed_files_since_snapshot(before, after)
    for path in extract_paths_from_text(root, assistant_message):
        if path not in candidate_files:
            candidate_files.append(path)

    mode = worklog_mode()
    entry, should_refine_async = prepare_log_entry(
        mode, root, prompt, assistant_message, candidate_files
    )

    updated_state = dict(cached)
    updated_state["logged_turn_id"] = turn_id
    updated_state["updated_at"] = datetime.now().astimezone().isoformat()
    save_state(payload, cached, updated_state)

    if should_skip_entry(entry):
        print(
            json.dumps(
                {
                    "continue": True,
                    "systemMessage": f"已跳过低价值 worklog：{entry['question']}",
                },
                ensure_ascii=False,
            )
        )
        return 0

    target = append_log(payload, cached, entry)
    queued_async = False
    if should_refine_async:
        queued_async = queue_async_summary(
            payload, updated_state, root, prompt, assistant_message, candidate_files, target
        )
    status = f"已写入 {display_path(root, payload, cached, target)}：{entry['question']}"
    if queued_async:
        status += "（AI 精修后台补充）"
    print(
        json.dumps(
            {
                "continue": True,
                "systemMessage": status,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
