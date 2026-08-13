# ADR-0021：怪物全量重建（5e_chm 全量提取）

- **状态**：已采纳
- **日期**：2026-08-13
- **版本**：v0.46.0

## 背景

此前怪物主源是 5etools-cn 数据（`translator=None` 的条目即机翻，`/查怪` 显示
`⚠️机翻` 标记）。法术已在 ADR-0018 升级为 5e_chm（= DND5e_chm = 「不全书」）
人工校对中文为主源；怪物仍大量机翻（4551 条中仅 1308 条 `translator=不全书`）。

## 决策

1. **怪物全量重建**：从本地 `5e_chm/md/`（不全书人工翻译）提取全部怪物统计块，
   产出 5etools 兼容 bestiary JSON，覆盖机翻。
2. **LLM 子代理提取**：用 flash 模型子代理「读 md → 产 JSON」（主代理 pro 负责
   契约/校验/对账/合并）。契约 `scripts/llm_monster_schema.md` 覆盖三大格式族：
   2024/2025 表格、2014 文本、MTG 变体。
3. **数值自动对齐 5etools-cn**：对账发现数值不一致时，改回 5etools-cn 数值
   （5e_chm 源偶有 OCR 笔误，如「CHA 5（-1）」应为 1），正文仍用 5e_chm 人工翻译。
4. **按名合并**：官方书按「名字」与 5etools-cn 合并——5e_chm 覆盖同名家，保留
   5etools-cn 独有家（5e_chm 不全的书，如模组仅含新增怪，不丢原有怪）。
   第三方书用中文书名作 source 新增。
5. **三层验收**：规则校验（`validate_monster_json.py`）+ 数值对账
   （`reconcile.py`）+ 抽样对读。

## 结果

- 全量提取 5e_chm 怪物 **2966 条**（覆盖 90 本书：官方核心 + 36 模组 + MTG +
  21 第三方 + DNDBeyond/其他）。
- 合并后 KB 怪物总数 **4570 条**：5e_chm 人工翻译覆盖机翻 + 保留 5etools-cn 独有
  （5e_chm 未覆盖的模组重印怪、Plane Shift、UA 等）。
- 新增第三方书怪物（万兽图志、塔尔多雷、鬼魅幽谷、德拉肯海姆等 21 本）以中文书名
  source 入库。

## 管道（scripts/monster_extract/）

- `inventory.py`：扫描 5e_chm/md 生成书→统计块文件清单。
- `make_plan.py`：生成分批提取计划（CHUNK=38 文件）+ slug 映射。
- `align.py`：清洗（状态括号/特殊抗性/感官归一/空 name 补齐）+ 数值对齐。
- `reconcile.py`：与 5etools-cn 数值对账 + source 映射验证。
- `finalize.py`：合并分块 + 清洗对齐 + 按名合并进 5etools-cn data/bestiary/。

## 后果

- 提取是「md → LLM → JSON → 校验 → 对齐 → 合并」的**可复现管道**；重跑
  `fetch_cn_data.py` 后可用 `finalize.py` 重新合并（5e_chm 产物独立于 5etools-cn）。
- 同名多版本仍按 `UNIQUE(kind,name,source)` 多行；5e_chm 与 5etools-cn 译名不一致
  的怪物会并存两行（source 不同或同名覆盖），属已知可接受现象。
