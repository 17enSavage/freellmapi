#!/usr/bin/env python3
"""Патчи к pipeline/*.py по результатам ревью (см. REVIEW.md, раздел C).

  python3 proposed-patches/apply_code_patches.py --root /путь/к/betting-agents           # проверка, ничего не пишет
  python3 proposed-patches/apply_code_patches.py --root /путь/к/betting-agents --apply   # применить

Защита: каждый патч ищет якорь по регулярному выражению (отступы сохраняются), требует
РОВНО одно совпадение и пропускает уже применённый патч (идемпотентно). Не нашёл якорь —
печатает MANUAL и ничего не меняет в этом файле. Перед записью делает копию `<файл>.bak`.
Файлы model.py / value.py / backtest.py по вашему CLAUDE.md меняются «только человеком и с
новой версией в db.model_params» — этот скрипт запускаете вы; после применения прогоните
`python3 scripts/smoke_test.py --full` и `python3 pipeline/model.py selftest`.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

W = r"[ \t]*"
PATCHES = [
    # (id, file, anchor regex, replacement (str or callable(match)->str), already-applied marker, description)
    ("P1", "pipeline/run_pipeline_risk.py",
     r'bank\["open"\]\["stake"\]\s*\+\s*per_league_stake\[lg\]\s*>=\s*0\.04\s*\*\s*bank\["bankroll"\]',
     'obl.get(lg, 0.0) >= 0.04 * bank["bankroll"]',
     'obl.get(lg, 0.0) >= 0.04',
     "лимит 4% на лигу считался от открытых ставок по ВСЕМ лигам (obl уже учитывает одобренное в этом прогоне)"),

    ("P2", "pipeline/run_pipeline_risk.py",
     r'(?P<i>[ \t]*)if bank\["mode"\] == "halted":\s*\n[ \t]*reject\("bank_halted"\);\s*continue\s*\n',
     lambda m: (m.group(0)
                + f'{m.group("i")}if (bank.get("clv_last100_n") or 0) >= 100 and (bank.get("clv_last100") or 0) < -0.02:\n'
                + f'{m.group("i")}    reject("clv_negative_degradation"); continue\n'),
     'clv_negative_degradation',
     "правило «CLV за 100 ставок < −2% → ставок нет» было только в промте"),

    ("P3", "pipeline/run_pipeline_risk.py",
     r'(?P<i>[ \t]*)out = RAW / run_id / "bet_slip\.json"\n',
     lambda m: (f'{m.group("i")}_stage = Path(__file__).resolve().parent.parent / "docs" / "STAGE.json"\n'
                f'{m.group("i")}try:\n'
                f'{m.group("i")}    slip["paper_mode"] = not bool(json.loads(_stage.read_text(encoding="utf-8")).get("real_money"))\n'
                f'{m.group("i")}except (OSError, ValueError):\n'
                f'{m.group("i")}    slip["paper_mode"] = True  # нет STAGE.json → бумага\n'
                + m.group(0)),
     'paper_mode',
     "bet_slip.json помечается paper_mode (читает docs/STAGE.json; нет файла → бумага)"),

    ("P4", "pipeline/run_pipeline.py",
     r'seasons\s*=\s*","\.join\(\s*f"\{asof\[2:4\]\}\{str\(int\(asof\[:4\]\)\s*\+\s*1\)\[2:\]\}"\s*for _ in range\(1\)\)',
     'seasons = (lambda y: f"{str(y)[2:]}{str(y + 1)[2:]}")(int(asof[:4]) if int(asof[5:7]) >= 8 else int(asof[:4]) - 1)',
     'int(asof[5:7]) >= 8',
     "сезон FDC: с января это ПРЕДЫДУЩИЙ год (2027-02 давал «2728» вместо «2627»)"),

    ("P5", "pipeline/run_pipeline.py",
     r'(?P<i>[ \t]*)# --- 3\. модель ---\n',
     lambda m: (f'{m.group("i")}g1 = validate(run_id, "GATE_1", log)\n'
                f'{m.group("i")}if g1.get("result") != "pass":  # fail и unknown: сомнение трактуется как fail\n'
                f'{m.group("i")}    errfile = RAW / run_id / "gate1_errors.json"\n'
                f'{m.group("i")}    errfile.write_text(json.dumps(g1.get("errors", []), ensure_ascii=False), encoding="utf-8")\n'
                f'{m.group("i")}    R.error_report(run_id, "GATE_1", str(errfile))\n'
                f'{m.group("i")}    _finish(run_id, asof, log, stopped_at="GATE_1")\n'
                f'{m.group("i")}    return\n\n'
                + m.group(0)),
     '"GATE_1"',
     "детерминированный конвейер не запускал GATE_1 (bundle собирался «для гейта», но гейт не вызывался)"),

    ("P6", "pipeline/run_pipeline.py",
     r'(?P<i>[ \t]*)# --- 7\. отчёт ---\n',
     lambda m: (f'{m.group("i")}g5 = validate(run_id, "GATE_5_BET_SLIP", log)\n'
                f'{m.group("i")}if g5.get("result") != "pass":\n'
                f'{m.group("i")}    errfile = RAW / run_id / "gate5_errors.json"\n'
                f'{m.group("i")}    errfile.write_text(json.dumps(g5.get("errors", []), ensure_ascii=False), encoding="utf-8")\n'
                f'{m.group("i")}    R.error_report(run_id, "GATE_5_BET_SLIP", str(errfile))\n'
                f'{m.group("i")}    _finish(run_id, asof, log, stopped_at="GATE_5_BET_SLIP")\n'
                f'{m.group("i")}    return\n\n'
                + m.group(0)),
     '"GATE_5_BET_SLIP"',
     "bet_slip.json в cron-пути не проходил ни один гейт перед отчётом/Telegram"),

    ("P7", "pipeline/value.py",
     r'exclude:\s*tuple\s*=\s*\("understat_model",\)',
     'exclude: tuple = ("understat_model", "market_max", "market_avg")',
     '"market_max", "market_avg")',
     "scan считал агрегаторы market_max/market_avg «лучшим букмекером» (line_shopping их уже исключал)"),

    ("P8", "pipeline/model.py",
     r'(?P<a>asof\s*<=\s*\?\s*)ORDER BY created_at DESC',
     lambda m: m.group("a") + "ORDER BY asof DESC, created_at DESC",
     'ORDER BY asof DESC, created_at DESC',
     "после backtest боевой predict мог взять параметры «начала сезона» (свежий created_at). "
     "Аналогичный SQL в db.py (model-status) и report.py я не вычитывал — проверьте вручную: grep -n 'ORDER BY created_at' pipeline/*.py"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    root = Path(a.root)
    texts, changed, rc = {}, set(), 0
    for pid, rel, rx, rep, marker, desc in PATCHES:
        path = root / rel
        if not path.exists():
            print(f"{pid} MANUAL  {rel}: файла нет"); rc = 1; continue
        t = texts.setdefault(rel, path.read_text(encoding="utf-8"))
        if marker in t:
            print(f"{pid} SKIP    {rel}: уже применён"); continue
        ms = list(re.finditer(rx, t))
        if len(ms) != 1:
            print(f"{pid} MANUAL  {rel}: якорь найден {len(ms)} раз(а), нужно ровно 1 — {desc}"); rc = 1; continue
        m = ms[0]
        new = rep(m) if callable(rep) else rep
        texts[rel] = t[:m.start()] + new + t[m.end():]
        changed.add(rel)
        print(f"{pid} {'APPLY ' if a.apply else 'READY '} {rel}: {desc}")
    if a.apply:
        for rel in changed:
            p = root / rel
            shutil.copy2(p, str(p) + ".bak")
            p.write_text(texts[rel], encoding="utf-8")
        print(f"записано файлов: {len(changed)} (копии .bak рядом)")
    else:
        print("проверка: ничего не записано; добавьте --apply")
    return rc


if __name__ == "__main__":
    sys.exit(main())
