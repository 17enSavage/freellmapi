#!/usr/bin/env python3
"""Сверка команд из промтов агентов с реальным argparse скриптов pipeline/.

  python3 scripts/check_prompt_cli.py            # из корня проекта betting-agents
  python3 scripts/check_prompt_cli.py --root X   # корень проекта X

Находит в .claude/agents/*.md, .claude/commands/*.md и CLAUDE.md все строки вида
`python3 pipeline/<script>.py [подкоманда] --флаги ...`, запускает `--help`
скрипта (и подкоманды) и проверяет, что КАЖДЫЙ флаг и подкоманда там описаны.
Строгая сверка по тексту help ловит то, что argparse молча прощает: сокращения
(`--league` вместо `--leagues`), несуществующие подкоманды (`value.py size`).

Код возврата 0 = расхождений нет, 1 = есть. Подходит для smoke_test.py и CI.
Это та же идея, что и ваш урок №17 из FINDINGS: ошибку, которую нельзя
предотвратить дисциплиной, нужно сделать обнаруживаемой автоматически.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

CMD = re.compile(r"python3\s+(pipeline/[\w]+\.py)([^\n`]*)")
PLACEHOLDER = re.compile(r"[{<\[]")


def help_text(root: Path, script: str, sub: str | None, cache: dict) -> str | None:
    key = (script, sub)
    if key not in cache:
        cmd = [sys.executable, "-I", script] + ([sub] if sub else []) + ["--help"]
        try:
            p = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            cache[key] = None
            return None
        out = (p.stdout or "") + (p.stderr or "")
        cache[key] = out if p.returncode == 0 else None
    return cache[key]


def has_flag(text: str, flag: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", text) is not None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    root = Path(ap.parse_args().root).resolve()
    files = sorted((root / ".claude" / "agents").glob("*.md")) + sorted((root / ".claude" / "commands").glob("*.md"))
    if (root / "CLAUDE.md").exists():
        files.append(root / "CLAUDE.md")

    cache: dict = {}
    problems, checked = [], 0
    for f in files:
        for ln, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for m in CMD.finditer(line):
                script, rest = m.group(1), m.group(2)
                if not (root / script).exists():
                    problems.append(f"{f.name}:{ln}: нет файла {script}")
                    continue
                toks = [t.strip("`\"',;)") for t in rest.split()]
                flags = [t for t in toks if t.startswith("--")]
                first = toks[0] if toks else ""
                sub = first if re.fullmatch(r"[a-z][a-z0-9-]*", first) else None
                base = help_text(root, script, None, cache)
                if base is None:
                    problems.append(f"{f.name}:{ln}: `{script} --help` не отработал")
                    continue
                text = base
                if sub:
                    sub_help = help_text(root, script, sub, cache)
                    if sub_help is None:
                        # у скрипта без подкоманд первое слово — просто значение; допустимо, если слово есть в help
                        if not re.search(rf"(?<![\w-]){re.escape(sub)}(?![\w-])", base):
                            problems.append(f"{f.name}:{ln}: подкоманда `{sub}` не найдена у {script}")
                            continue
                    else:
                        text = base + "\n" + sub_help
                checked += 1
                for fl in flags:
                    if PLACEHOLDER.search(fl):
                        continue
                    if not has_flag(text, fl):
                        problems.append(f"{f.name}:{ln}: флаг {fl} не описан в help {script}" + (f" {sub}" if sub else ""))
    for p in problems:
        print("MISMATCH", p)
    print(f"проверено команд: {checked}, расхождений: {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
