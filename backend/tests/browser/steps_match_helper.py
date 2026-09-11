"""Номера шагов так, как их посчитает выгрузка в Google-таблицу."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.funnel_input import funnel_steps  # noqa: E402
from app.graph import compile_graph  # noqa: E402

graph = compile_graph(json.loads(sys.stdin.read()))
print(json.dumps({nid: i + 1 for i, (nid, _n) in enumerate(funnel_steps(graph))}))
