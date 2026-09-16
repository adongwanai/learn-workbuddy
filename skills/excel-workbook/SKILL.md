---
name: excel-workbook
description: "使用此 Skill 创建、检查、编辑和验证 XLSX 工作簿。用户提到 Excel、xlsx、工作簿、工作表、表格，或要求修改、汇总、检查 Excel 文件时使用。只支持基础 XLSX 操作，不处理宏、VBA、图表、数据透视表或外部工作簿引用。"
allowed-tools:
  - Bash
---

# Excel 工作簿基础操作

当用户要求创建、检查、编辑或验证 `.xlsx` 文件时使用本 Skill。

## 支持范围

支持以下基础操作：

- 检查工作簿、工作表、行列范围、公式、日期和合并单元格；
- 创建包含表头、数据和简单公式的新工作簿；
- 编辑单元格、追加行和基础数字格式；
- 保留已有工作表、公式、日期、数据类型和未修改区域的基础样式；
- 重新打开输出文件，验证关键内容；
- 设置 Excel/WPS 打开文件时自动重算的标记。

当前版本只支持 `.xlsx`。不处理 `.xlsm` 宏、VBA、图表、数据透视表、
Power Query、外部工作簿引用或复杂财务模型。

## 工作原则

1. 先检查，再修改。
2. 默认不覆盖原文件；编辑时输入文件和输出文件必须是不同路径。
3. 修改现有模板时，只改用户指定的单元格或区域，不重建整个工作簿。
4. 日期、数字和公式要保持正确的数据类型。公式字符串被写入不等于公式已经计算。
5. 修改后必须重新打开输出文件并验证。
6. 没有 Excel、WPS 或 LibreOffice 重算结果时，不要声称公式结果已经验证。

## 工具脚本

脚本位于当前 Skill 的 `scripts/workbook_tool.py`：

```text
python scripts/workbook_tool.py inspect input.xlsx
python scripts/workbook_tool.py create output.xlsx --spec spec.json
python scripts/workbook_tool.py edit input.xlsx output.xlsx --operations operations.json
python scripts/workbook_tool.py validate output.xlsx --expect expectations.json
python scripts/workbook_tool.py recalc output.xlsx
```

所有命令默认输出 JSON，便于继续检查和记录。

### 创建数据格式

`create` 的 spec 使用以下结构：

```json
{
  "sheet": "销售数据",
  "headers": ["日期", "商品", "金额", "含税金额"],
  "rows": [
    [
      {"date": "2026-09-16"},
      "A",
      100,
      {"formula": "=C2*1.1"}
    ]
  ],
  "column_widths": {"A": 14, "C": 12, "D": 14}
}
```

日期使用 `{"date": "YYYY-MM-DD"}`，公式使用 `{"formula": "=..."}`。

### 编辑数据格式

`edit` 的 operations 使用以下结构：

```json
{
  "set_cells": [
    {"sheet": "销售数据", "cell": "C2", "value": 120},
    {"sheet": "销售数据", "cell": "D2", "formula": "=C2*1.1"},
    {"sheet": "销售数据", "cell": "A2", "date": "2026-09-17"}
  ],
  "append_rows": [
    {
      "sheet": "销售数据",
      "values": [{"date": "2026-09-18"}, "B", 80, {"formula": "=C3*1.1"}]
    }
  ],
  "number_formats": [
    {"sheet": "销售数据", "cell": "C2", "format": "#,##0.00"}
  ]
}
```

### 验证数据格式

`validate` 的 expectations 使用以下结构：

```json
{
  "sheets": ["销售数据"],
  "cells": {
    "销售数据!C2": 120,
    "销售数据!D2": "=C2*1.1",
    "销售数据!A2": {"date": "2026-09-17"}
  },
  "required_formulas": ["销售数据!D2"]
}
```

## 重算边界

`recalc` 会先设置：

```text
calcMode=auto
fullCalcOnLoad=true
forceFullCalc=true
```

如果本机存在 LibreOffice，脚本会尝试执行无界面重算；否则返回
`unavailable`，并明确说明只设置了 Excel/WPS 打开时重算标记。

## 交付格式

完成后说明：

```text
输入文件：
输出文件：
执行操作：
保留内容：
验证结果：
未完成的检查：
```
