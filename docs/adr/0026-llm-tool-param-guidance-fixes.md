# ADR-0026：LLM 工具参数引导修复（子职 kind/选项 filter 白名单/子职空 body）

- **状态**：已采纳
- **日期**：2026-08-15
- **版本**：v0.51.0

## 背景

用户反馈：让 LLM 查第三方法师子职「司书」（THC《火炬光下的克苏鲁》）时，
`query_dnd_knowledge` 返回「未找到与「司书」相关的条目」。日志显示 LLM 传参为
`action=search, kind=职业, name=司书`，参数本身已正确送达工具（docstring 与
函数签名一致，`tests/test_llm_tool_schema.py` 全过），但查询失败。

排查确认根因是**参数引导缺陷**，而非参数传递缺陷：

1. **docstring `kind` 枚举漏「子职」**（写作「法术/怪物/物品/专长/背景/职业/
   状态/种族/选项」）。`resolve_kind("子职")` 实际支持（`KIND_CN` 含
   `"子职": "subclass"`），但 LLM 从 docstring 根本看不到这个合法取值，
   查子职只能猜「职业」（class）→ 数据库里「司书」是 `entries.kind='subclass'`，
   class 查询必然 0 命中。
2. **docstring 引导「查子职能力走 class_features」不足**：只写了
   「action=class_features，kind=职业」，未强调 `name=所属职业名` +
   `subclass=子职名` 的组合；LLM 倾向于用 `search` 按名字直查。
3. **子职条目本身无正文**（`entries.body` 恒为空，正文在 `class_features`
   表）——即便 `kind=子职` 查中，`detail` 也只返回空卡片，误导 LLM
   以为知识库无内容。
4. **LLM 工具 filter 白名单漏 `optionalfeature`**：docstring 明确推荐
   「action=filter，kind=选项，opt_type=…」，但工具入口白名单只含
   spell/monster/item/race/feat/class/subclass/background，`filter+选项`
   直接被拒——文档与实现矛盾（命令侧 `/筛选项` 正常）。

## 决策

1. **docstring 补齐**：
   - `kind` 枚举加「子职」，并注明「查询子职能力用 class_features+subclass，
     不要用 kind=职业 查子职名」；
   - 用法提示区新增「查子职」条目：`action=class_features + name=所属职业名
     + subclass=子职名`（例：name=法师 + subclass=司书），并说明
     kind=子职 的 detail/search 只能确认存在、完整能力走 class_features。
2. **LLM 工具 filter 白名单加 `optionalfeature`**（与命令侧 `/筛选项` 对齐）。
3. **search/detail 全库兜底**：kind 限定查询 0 命中时，自动全库（不带 kind）
   再搜一次；若命中其他类别同名条目，返回候选并提示「kind=「X」下未找到，
   但知识库中存在其他类别的同名条目（可能是子职等）」，引导 LLM 纠正
   kind 或改用 class_features。**仅对显式传了 kind 的查询生效**，不改变
   未传 kind 的行为，避免歧义。
4. **子职空 body 引导**：`format_entry` 对 `kind=='subclass'` 且 body 为空
   的条目，body 位置替换为「子职条目本身无正文，请用 action=class_features，
   name=所属职业名，subclass=该子职名 查询」。
5. **回归测试**：`tests/test_kb_commands.py` 新增 3 例（filter+选项 可用、
   kind=职业 查子职名的全库兜底提示、子职 detail 空 body 引导）。

## 影响

- 仅 LLM 工具查询路径与展示文案变更；知识库数据、命令侧行为不变。
- 全量 1273 测试通过（+3 新用例）。

## 备选方案

- 让 `search` 无条件跨 kind 合并结果：否决——会污染「未找到」语义，
  且同名多 kind（如「法师」既是职业又可能是种族）会产生歧义。
- 构建期给 entries.subclass 写入 class_features 合并正文：否决——数据
  重复、构建复杂化；运行时引导成本更低且不破坏「子职正文以 class_features
  为准」的单一事实来源。
