"""class_extract/ — 从 5e_chm Markdown 重建职业/子职数据（v0.49.0，ADR-0024）。

管线（参照 monster_extract，ADR-0021）：
  inventory.py  扫描 5e_chm/md 全库职业/子职文件 → 解析计划（文件→归属 hint）
  parser.py     单文件解析（三种格式族：显式等级 / #### 标题 / TCE 斜体标记）
  source_map.py 书目录 → 5etools source 码（官方 + 第三方自定义）
  emit.py       解析结果 → 5etools 兼容 class-*.json（class/subclass/classFeature/subclassFeature）
  finalize.py   与 5etools-cn 按名合并（5e_chm 覆盖同 source 同名家，保留 cn 独有家）+ 对账

规则为主 + LLM 兜底：解析失败/异常文件收集到 report，由 LLM 子代理补提取。
"""

from __future__ import annotations
