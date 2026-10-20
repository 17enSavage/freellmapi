# Установка (5 шагов)

1. Скопировать в корень проекта `betting-agents`: `.claude/` (agents, commands, settings.json), `CLAUDE.md`, `docs/STAGE.json`, `docs/BOOKMAKERS.json`. Ваши `docs/*.md` и `pipeline/` не трогать.
2. Заполнить `docs/BOOKMAKERS.json → available` (букмекеры, где у вас есть счёт). `docs/STAGE.json` оставить `real_money: false`.
3. Положить `proposed-patches/load_llm_artifacts.py` в `pipeline/`.
4. Патчи кода: `python3 proposed-patches/apply_code_patches.py --root .` (проверка) → `--apply` → `python3 scripts/smoke_test.py --full` и `python3 pipeline/model.py selftest`. Скопировать `scripts/check_prompt_cli.py` в `scripts/`, запустить: `python3 scripts/check_prompt_cli.py`.
5. Перезапустить Claude Code (каталог `.claude/agents/` подхватывается только на старте), затем `/pipeline status`, потом `/pipeline daily`.

Нужна версия Claude Code с вложенными субагентами (3 слоя; `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` не ниже 3).
