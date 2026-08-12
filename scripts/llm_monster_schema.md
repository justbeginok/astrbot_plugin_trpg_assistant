# 怪物 LLM 提取 Schema（md → 5etools 兼容 JSON）

试点源：`5e_chm/md/第三方/火炬光下的克苏鲁/第七章`（28 个 md 文件）。
产物：`bestiary-thc.json`（monster 数组），字段对齐 5etools 中文怪物
schema，使 `build_kb.py` 的 `_monster_body` / `_monster_tags` 原样工作。

## 提取规则（必须遵守）

1. **只提取含统计块（CR 行）的页面**。纯 lore 页（如「深潜者总」——
   只有背景故事和外貌描写）、章节页（「第七章：神话生物」）跳过，
   不产生条目。
2. **标题格式**：md 中「##### 深潜者 Deep One」→ `name=深潜者`、
   `ENG_name=Deep One`。
3. **清理 md 瑕疵**：
   - 特性/动作标题「水陆两栖Amphibious.。」→ `name` 只保留中文
     「水陆两栖」（去掉英文与重复句号「.。」）；
   - `*斜体*` 强调标记 → 去掉 `*`；
   - 正文中夹带的「（详见XXX）」等源内引用说明 → 保留原样即可。
4. **免疫行拆分**：一行混合文本按「；」拆——分号前为伤害免疫
   （`immune`），分号后为状态免疫（`conditionImmune`）。
   例：「免疫 强酸，暗蚀，毒素；目盲，魅惑，耳聋，恐慌，中毒，倒地」
   → `immune=["强酸","暗蚀","毒素"]`、
   `conditionImmune=["目盲","魅惑","耳聋","恐慌","中毒","倒地"]`。
   若整行全是伤害或全是状态，对应另一组留空/缺省。
5. **属性表**：HTML table 六列（力量/敏捷/体质/智力/感知/魅力），
   取「数值」列（第 4 列，如 16/10/12/15/11/13）。豁免/调整列不产出。
6. **豁免/技能**：md 统计块中「豁免 力量+5，感知+4」→
   `save={"str":"+5","wis":"+4"}`（中文属性名转英文键）。
   「技能 运动+5，察觉+2」→ `skill={"athletics":"+5","perception":"+2"}`
   （中文技能名转英文键：运动=athletics、察觉=perception、
   奥秘=arcana、历史=history、洞悉=insight、威吓=intimidation、
   调查=investigation、医药=medicine、自然=nature、表演=performance、
   游说=persuasion、宗教=religion、巧手=sleightOfHand、
   隐匿=stealth、生存=survival、特技=acrobatics、驯兽=animalHandling、
   欺瞒=deception、观察=insight）。
7. **速度**：「速度 30尺，游泳30尺」→ `speed={"walk":30,"swim":30}`；
   「速度 20尺」→ `speed={"walk":20}`。
8. **感官**：「感官 黑暗视觉60尺；被动察觉12」→
   `senses=["黑暗视觉60尺"]`、`passive=12`。被动察觉只进 `passive`，
   不进 senses。感官文本保持原样（含数值，如「真实视觉 120 尺」）。
9. **阵营**：md 类型行「中型异怪（神话生物），守序邪恶」→ 阵营转
   **5e 轴码数组**（构建期 format_alignment 按轴码拼接中文）：
   守序善良=`["L","G"]`、中立善良=`["N","G"]`、混乱善良=`["C","G"]`、
   守序中立=`["L","N"]`、绝对中立=`["N"]`、混乱中立=`["C","N"]`、
   守序邪恶=`["L","E"]`、中立邪恶=`["N","E"]`、混乱邪恶=`["C","E"]`、
   无阵营=`["U"]`、任意阵营=`["A"]`、任意邪恶=`["L","N","C","E"]`、
   任意善良=`["L","N","C","G"]`。
10. **体型**：中型=M、小型=S、大型=L、巨型=H、超大型=G、微型=T。
11. **类型**：中文生物类型转英文码：异怪=aberration、野兽=beast、
    天界生物=celestial、构造体=construct、龙类=dragon、元素=elemental、
    精类=fey、邪魔=fiend、巨人=giant、类人生物=humanoid、怪物=
    monstrosity、泥怪=ooze、植物=plant、不死生物=undead、虫群=swarm。
    括号内标签（如「（神话生物）」）→ `type={"type":"aberration",
    "tags":["神话生物"]}`；无标签 → `type="aberration"`。
12. **特性/动作/反应等**：`trait`/`action`/`bonus`/`reaction`/
    `legendary`/`mythic` 数组，每项 `{"name": 中文标题,
    "entries": [正文段落]}`。标题「多重攻击Multiattack」→ name 只留
    中文。正文中的攻击描述（*近战攻击检定：*+5...）去 `*` 后原样保留。
13. **施法**：`spellcasting` 数组，每项 `{"name":"施法",
    "ability":"int", "will":[...], "daily":{"1":[...],"3":[...]}}`。
    md 中「*随意：魅惑类人，易容术，传讯术1/日：雷鸣波*」→
    `will=["魅惑类人","易容术","传讯术"]`、`daily={"1":["雷鸣波"]}`。
    ability（施法属性）从「使用智力作为施法属性」提取：
    智力=int、感知=wis、魅力=cha。
14. **缺失字段**：不适用/未出现的字段省略或空数组，不要编造。
    没有「抗性/易伤」就不写该字段。
15. **CR**：「CR 2（XP450；PB+2）」→ `cr="2"`；「CR 1/4（XP50；PB+2）」
    → `cr="1/4"`（字符串，保留分数）；「CR 0」→ `cr=0`。只取 CR 值，
    XP/PB 不产出。

## JSON Schema（monster 数组元素）

```json
{
  "name": "深潜者",
  "ENG_name": "Deep One",
  "source": "火炬光下的克苏鲁",
  "edition": "2024",
  "size": "M",
  "type": {"type": "aberration", "tags": ["神话生物"]},
  "alignment": ["L", "E"],
  "ac": 14,
  "hp": {"average": 44, "formula": "8d8+8"},
  "cr": "2",
  "speed": {"walk": 30, "swim": 30},
  "str": 16, "dex": 10, "con": 12, "int": 15, "wis": 11, "cha": 13,
  "save": {"str": "+5", "int": "+4"},
  "skill": {"athletics": "+5", "perception": "+2"},
  "immune": ["毒素"],
  "resist": [],
  "vulnerable": [],
  "conditionImmune": ["中毒"],
  "languages": ["通用语"],
  "senses": ["黑暗视觉60尺"],
  "passive": 12,
  "environment": [],
  "trait": [{"name": "水陆两栖", "entries": ["深潜者可以在空气和水中呼吸。"]}],
  "action": [{"name": "多重攻击", "entries": ["深潜者发动两次爪击和一次啃咬攻击。"]}],
  "bonus": [],
  "reaction": [],
  "legendary": [],
  "mythic": [],
  "spellcasting": [{"name": "施法", "ability": "int", "will": ["魅惑类人"], "daily": {"1": ["雷鸣波"]}}]
}
```

## 输出要求

- 输出一个 JSON 文件，顶层 `{"monster": [ ... ]}`。
- 文件命名为 `bestiary-thc.json`。
- 严格 JSON，无注释、无 markdown 代码围栏之外的文字。
- `source` 一律 `火炬光下的克苏鲁`；`edition` 一律 `2024`。
