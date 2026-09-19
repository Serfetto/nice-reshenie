"""Временная шкала механизма: класс, причина, тип данных, уверенность и доказательства по точкам сетки."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from common.timeutil import from_np, iso

NO_DATA, ACCEPTABLE, UNDESIRABLE, CRITICAL = -1, 0, 1, 2
CLASS_NAMES = {NO_DATA: "no_data", ACCEPTABLE: "acceptable", UNDESIRABLE: "undesirable", CRITICAL: "critical"}
CONF_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}

REASONS = {
    "nominal": "фоновые условия",
    "saa": "проход Южно-Атлантической аномалии (захваченные протоны)",
    "sep_s3": "солнечное протонное событие уровня S3+ (NOAA: избегать ВКД)",
    "sep_open_zone": "протоны события проникают к МКС (поток выше порога обрезания велик)",
    "sep_partial_access": "протоны события частично проникают к МКС",
    "sep_shielded": "идёт протонное событие, МКС под защитой магнитного поля",
    "sep_probable": "протонное событие вероятно (прогноз SWPC на сутки)",
    "sep_unlikely": "протонное событие маловероятно (прогноз SWPC на сутки)",
    "sep_ongoing_beyond_horizon": "событие идёт, горизонт собственного прогноза исчерпан",
    "no_observation": "нет измерений потока протонов",
    "obs_stale": "измерения потока устарели — прогноз не строится",
    "source_disabled": "источник отключён для этого расчёта",
    "no_forecast": "нет прогноза на этот интервал",
    "conjunction": "сближение с объектом: проход через зону контроля МКС",
    "meteor_shower": "метеорный поток: поток частиц заметно выше фона, радиант не закрыт Землёй",
    "no_catalog": "нет актуального каталога объектов",
    "catalog_stale": "каталог объектов устарел",
    "no_orbit": "нет орбитальных данных МКС",
}


@dataclass
class MechanismTimeline:
    name: str
    times: np.ndarray                 # datetime64[s]
    cls: np.ndarray                   # int8
    reason: np.ndarray                # object (коды REASONS)
    kind: np.ndarray                  # object: observation | forecast_external | forecast_team | probability | geometry | none
    confidence: np.ndarray            # object: high | medium | low | none
    evidence: list[tuple]             # по точкам — ключи evidence_items
    evidence_items: dict[str, dict] = field(default_factory=dict)
    series: dict[str, np.ndarray] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    conf_reason: np.ndarray | None = None  # object: почему такая уверенность (по точкам)

    def intervals(self) -> list[dict]:
        """Схлопывает точки с одинаковым состоянием в интервалы."""
        out = []
        n = len(self.times)
        if n == 0:
            return out
        step = (self.times[1] - self.times[0]) if n > 1 else np.timedelta64(30, "s")
        cr = self.conf_reason if self.conf_reason is not None else np.full(n, "", dtype=object)
        start = 0
        for i in range(1, n + 1):
            if i < n and (self.cls[i] == self.cls[start] and self.reason[i] == self.reason[start]
                          and self.kind[i] == self.kind[start] and self.confidence[i] == self.confidence[start]
                          and self.evidence[i] == self.evidence[start] and cr[i] == cr[start]):
                continue
            out.append({
                "from": iso(from_np(self.times[start])), "to": iso(from_np(self.times[i - 1] + step)),
                "class": CLASS_NAMES[int(self.cls[start])], "reason": self.reason[start],
                "reason_text": REASONS.get(self.reason[start], self.reason[start]),
                "kind": self.kind[start], "confidence": self.confidence[start],
                "confidence_reason": cr[start] or None,
                "evidence": list(self.evidence[start]),
            })
            start = i
        return out


def min_confidence(values) -> str:
    vals = [v for v in values if v in CONF_ORDER]
    if not vals:
        return "none"
    return min(vals, key=lambda v: CONF_ORDER[v])
