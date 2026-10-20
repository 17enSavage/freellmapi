"""Загрузка результатов LLM-этапов в БД. Положить в pipeline/ (код пишет человек, не агент).

  python3 pipeline/load_llm_artifacts.py --run-id {RUN_ID} --what qualitative
  python3 pipeline/load_llm_artifacts.py --run-id {RUN_ID} --what news,lineups

Зачем: `model.py adjust` НЕ читает qualitative.json — он берёт строки из таблицы
`adjustments` (analyst='aggregated'), а `run_pipeline_bundle.py` считает news_raw / lineups
в БД. Ни один скрипт проекта эти таблицы не заполнял, поэтому LLM-ветка тихо
вырождалась в «множители = 1.00».

Скрипт детерминированный: не оценивает и не исправляет числа. Множитель вне [0.85, 1.15],
неизвестный match_key или отсутствие источника у множителя != 1.00 — ошибка, строка не пишется.
Идемпотентен: повторный запуск для того же run_id заменяет строки qualitative и игнорирует дубли news/lineups.
Колонки таблиц определяются через PRAGMA table_info — пишутся только существующие.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from util import RAW, audit, utcnow  # noqa: E402
import db as dbmod  # noqa: E402

CACHE = RAW.parent / "cache"
ADJ_LO, ADJ_HI = 0.85, 1.15  # как GATE_3


def _cols(c, table: str) -> set[str]:
    return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def _insert(c, table: str, row: dict, verb: str = "INSERT OR IGNORE") -> bool:
    have = _cols(c, table)
    data = {k: v for k, v in row.items() if k in have}
    if not data:
        raise RuntimeError(f"no_matching_columns:{table}")
    names = list(data)
    sql = f"{verb} INTO {table} ({','.join(names)}) VALUES ({','.join('?' * len(names))})"
    return c.execute(sql, [data[k] for k in names]).rowcount > 0


def _js(v):
    return v if isinstance(v, str) or v is None else json.dumps(v, ensure_ascii=False)


def load_qualitative(run_id: str) -> dict:
    qpath, mpath = RAW / run_id / "qualitative.json", RAW / run_id / "model_output.json"
    if not qpath.exists():
        return {"what": "qualitative", "status": "failed", "error": f"artifact_not_found:{qpath}"}
    if not mpath.exists():
        return {"what": "qualitative", "status": "failed", "error": f"artifact_not_found:{mpath}"}
    q = json.loads(qpath.read_text(encoding="utf-8"))
    if q.get("status") == "skipped":
        return {"what": "qualitative", "status": "ok", "rows": 0, "note": "qualitative.status=skipped"}
    teams = {m["match_key"]: (m["home"], m["away"])
             for m in json.loads(mpath.read_text(encoding="utf-8")).get("payload") or []}

    rows, errors = [], []
    for item in q.get("payload") or []:
        mk = item.get("match_key")
        if mk not in teams:
            errors.append({"match_key": mk, "error": "match_not_in_model_output"})
            continue
        for side_name, team in zip(("home", "away"), teams[mk]):
            blk = item.get(side_name) or {}
            for comp in ("attack", "defense"):
                v = blk.get(comp)
                if v is None or abs(float(v) - 1.0) < 1e-9:
                    continue  # нет строки = множитель 1.00
                v = float(v)
                srcs = blk.get("sources") or []
                if not (ADJ_LO <= v <= ADJ_HI):
                    errors.append({"match_key": mk, "team": team, "side": comp, "error": f"out_of_range:{v}"})
                elif not any(isinstance(s, dict) and s.get("url") for s in srcs):
                    errors.append({"match_key": mk, "team": team, "side": comp, "error": "adjustment_without_source"})
                else:
                    rows.append({"run_id": run_id, "match_key": mk, "team": team, "side": comp,
                                 "multiplier": v, "analyst": "aggregated",
                                 "reasoning": blk.get("reasoning") or "",
                                 "sources": _js(srcs), "flags": _js(blk.get("flags") or []),
                                 "created_at": utcnow()})
    if errors:  # частичная загрузка хуже отсутствующей: всё или ничего
        return {"what": "qualitative", "status": "failed", "errors": errors}
    with dbmod.conn() as c:
        c.execute("DELETE FROM adjustments WHERE run_id=? AND analyst='aggregated'", (run_id,))
        for r in rows:
            _insert(c, "adjustments", r, "INSERT")
    return {"what": "qualitative", "status": "ok", "rows": len(rows)}


def load_cache(run_id: str, kind: str) -> dict:
    path = CACHE / f"{kind}_{run_id}.json"
    if not path.exists():
        return {"what": kind, "status": "failed", "error": f"artifact_not_found:{path}"}
    items = json.loads(path.read_text(encoding="utf-8"))
    now, n_new, n_bad = utcnow(), 0, 0
    with dbmod.conn() as c:
        have = _cols(c, "news_raw" if kind == "news" else "lineups")
        for it in items:
            if kind == "news":
                row = dict(it, persons=_js(it.get("persons") or []), fetched_at=it.get("fetched_at") or now)
                need = ("headline", "source_url", "published_at")
                table = "news_raw"
            else:
                row = {"run_id": run_id, "match_key": it.get("match_key"), "team": it.get("team"),
                       "confidence": it.get("confidence") or "unavailable",
                       "fetched_at": it.get("fetched_at") or it.get("published_at") or now,
                       "source_url": it.get("source_url"), "source_name": it.get("source_name"),
                       "published_at": it.get("published_at"),
                       "hours_before_kickoff": it.get("hours_before_kickoff"),
                       "rotation_risk": it.get("rotation_risk"), "rotation_reason": it.get("rotation_reason")}
                for key, aliases in (("missing_players", ("missing", "missing_players")),
                                     ("doubtful_players", ("doubtful", "doubtful_players")),
                                     ("expected_xi", ("expected_xi",))):
                    col = next((a for a in aliases if a in have), None)
                    if col:
                        row[col] = _js(it.get(key) or [])
                need = ("match_key", "team")
                table = "lineups"
            if any(not row.get(k) for k in need):
                n_bad += 1
                continue
            n_new += int(_insert(c, table, row))
    return {"what": kind, "status": "ok" if not n_bad else "partial", "inserted": n_new,
            "seen": len(items), "rejected_missing_fields": n_bad}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-id", required=True)
    p.add_argument("--what", default="qualitative,news,lineups")
    a = p.parse_args()
    out = []
    for w in (x.strip() for x in a.what.split(",") if x.strip()):
        out.append(load_qualitative(a.run_id) if w == "qualitative" else load_cache(a.run_id, w)
                   if w in ("news", "lineups") else {"what": w, "status": "failed", "error": "unknown_target"})
    audit("data-scout", "load_llm_artifacts", "", run_id=a.run_id, results=out)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(0 if all(r["status"] in ("ok", "partial") for r in out) else 1)


if __name__ == "__main__":
    main()
