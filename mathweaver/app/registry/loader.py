"""Registry:课程级基础定义/引理库 + 问题模式库。

AI 生成的节点只允许通过 source_refs 引用这里存在的 id;
validator 会拒绝悬空引用——这是杜绝"LLM 发明公理"的机制。
"""
from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


class Registry:
    def __init__(self):
        self.foundations: dict[str, dict] = {}
        self.patterns: dict[str, dict] = {}
        self._load()

    def _load(self):
        for f in sorted(DATA_DIR.glob("foundations_*.json")):
            doc = json.loads(f.read_text(encoding="utf-8"))
            for item in doc.get("items", []):
                item["domain"] = doc.get("domain", "")
                self.foundations[item["id"]] = item
        for f in sorted((DATA_DIR / "patterns").glob("*.json")):
            pat = json.loads(f.read_text(encoding="utf-8"))
            self.patterns[pat["id"]] = pat

    # ------------------------------------------------------------- 查询

    def foundation(self, ref_id: str) -> dict | None:
        return self.foundations.get(ref_id)

    def has_ref(self, ref_id: str) -> bool:
        return ref_id in self.foundations

    def pattern(self, pattern_id: str) -> dict | None:
        return self.patterns.get(pattern_id)

    def match_pattern(self, text: str) -> tuple[str | None, float]:
        """关键词打分匹配问题模式(LLM 不可用时的回退,也用于交叉验证 LLM 分类)。"""
        best, best_score = None, 0.0
        low = text.lower()
        for pid, pat in self.patterns.items():
            kws = pat.get("keywords", [])
            hits = sum(1 for k in kws if k.lower() in low)
            score = hits / max(len(kws), 1)
            if hits >= 2 and score > best_score:
                best, best_score = pid, score
        return best, best_score

    def foundations_for_pattern(self, pattern_id: str) -> list[dict]:
        pat = self.pattern(pattern_id)
        if not pat:
            return list(self.foundations.values())
        domain = pat.get("domain", "")
        return [it for it in self.foundations.values() if it.get("domain") == domain]


_registry: Registry | None = None


def get_registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry()
    return _registry
