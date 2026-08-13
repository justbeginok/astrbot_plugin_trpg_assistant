# ADR-0022：骰子多重投掷与复杂公式（四则运算 + 括号）

- **状态**：已采纳
- **日期**：2026-08-13
- **版本**：v0.47.0

## 背景

骰子引擎此前是「扁平 +/- 和」结构：`dice_parser.parse()` 逐 token 消费，只支持
`+`/`-` 连接骰组与平坦整数修正（`groups` + `flat_modifier`），不支持重复投掷、
乘除、括号。README「与 Roll20 规范的差距」表明确列出「算式骰数/骰面」「完整数学
运算符」「括号运算优先级」三项未实现，路线图第 1 条为「重复掷骰」。

`#` 字符被 `_strip_label` 抢占为「标签强制分隔符」（`d20+5#攻击`），与 Roll20 的
重复投掷语法 `N#expr` 冲突，需要消歧。

## 决策

1. **单一 AST + 扁平兼容回退**：`parse()` 一律先用递归下降文法构建表达式树
   （`ConstNode`/`DiceNode`/`BinOpNode`/`NegNode`/`GroupNode`）；若树是纯 `+/-` 和
   且骰数/骰面为字面整数，则回退为现行 `groups + flat_modifier + modifier=-1`
   哨兵（`ast=None`），保证既有表达式与输出零回归。
2. **`repeat` 放 `ParsedExpression` 顶层**（独立于 AST）；`RollResult` 增
   `sub_results: list[RollResult]` 表达 N 次独立结果，`group_results` 语义保持
   「左到右扁平骰组列表」。
3. **`#` 位置敏感消歧**：仅当 `#` 左侧整体为纯数字且位于表达式开头时视为重复
   前缀（`3#d20+d6`），其余 `#` 仍是标签分隔符（`d20+5#攻击`）。双重 `#`
   （`3#d20#攻击`）天然分层，LLM 工具 `_compose_tool_expr` 无需逻辑改动。
4. **计数组限根**：带 `>N`/`<N`/`fN` 计数修饰的骰组只能作为整条表达式单独使用，
   不得参与任何四则运算或出现在括号中（含骰数/骰面位置的括号算式），否则报错。
   这是行为变更：现行 `3d6>3+1d4` 会静默忽略 `+1d4`，本版改为明确报错。
5. **`flat_modifier` 保留兼容字段不迁移**：生产消费点仅 `dice_roller.total` 与
   `formatter` 两处，且只在 `ast is None` 的扁平路径读取。
6. **除法语义**：整数除法向下取整（Python `//`，含负数向 -inf 取整），左结合每步
   floor；除零报错。骰数/骰面位置的括号算式结果必须为正整数，受 `max_dice_count`
   /`max_dice_sides` 限制。
7. **防滥用上限 `max_repeat_count`**：重复投掷次数上限，默认 20（新配置项，滑块
   1–100）；全局骰数预算复用 `max_dice`（`repeat × 字面骰数 ≤ max_dice`），
   骰数含骰子的表达式在求值期增量计数兜底。
8. **多重投掷输出多行**：首行为标题行（`3#d20+d6: 重复 3 次`，兼作历史摘要首行），
   逐行 `#N 明细 = 值`，末行 `合计/平均`；禁用 DC 与大成功/大失败判定。

## 结果

- `/r 3#d20+d6` 重复投掷整个表达式 3 次，逐行输出。
- `/r 3d6*(2+4)d12`、`/r (2+3)d6`、`/r 3d(2*4)`、`/r (2d6+1)d8` 完整四则与括号。
- 骰子 token 解析器（15 种修饰符）原样复用，仅在骰数/骰面读取点扩展括号算式。

## 后果

- `3d6>3+1d4` 由「静默忽略 `+1d4`」收紧为报错（语义收紧，CHANGELOG 明示）。
- `ParsedExpression` 增 `repeat`/`ast` 字段；`RollResult` 增 `sub_results`/
  `ast_value`；`DiceGroup` 增 `count_src`/`sides_src`/`count_expr`/`sides_expr`。
- 全角 `＃` 归一为 `#`（`_FULLWIDTH_TABLE`），`3＃d20` 亦可识别。
- 计数组永远走扁平路径（限根校验通过后扁平化），AST 路径恒不含计数组，
  `is_success_mode` 判定无需在 AST 分支特殊处理。
