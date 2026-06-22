# data/ — 数据集目录

`data/` 存放赛题原始数据和解析产物。整体不纳入版本控制（已 gitignore），读者需自行下载数据集；Markdown 解析产物由赛题方通过 MinerU 预先提供，无需本地解析。

## 当前状态

本地只有 A 组数据（split='A'），共 100 题。B 榜数据在复赛阶段才下发，当前不存在。

## 目录结构

```
data/
├── README.md                          # 本文件
├── public_dataset_a.zip               # 赛方原始数据压缩包（需自行下载，不纳入版本控制）
├── raw_dataset/                       # 解压后的原始数据
│   ├── questions/
│   │   └── group_a/                   # A 组题目 JSON（5 个领域，共 100 题）
│   └── raw/                           # 原始文档（86 个 PDF）
│       ├── financial_contracts/       # 14 个 PDF
│       ├── financial_reports/         # 10 个 PDF
│       ├── insurance/                 # 16 个 PDF
│       ├── regulatory/                # 26 个 PDF
│       └── research/                  # 20 个 PDF
└── merged_md/                         # MinerU 解析的 Markdown（赛题方提供）
    ├── financial_contracts/
    ├── financial_reports/
    ├── insurance/
    ├── regulatory/
    └── research/
```

## 数据规模

| 领域 | 题目数 | 原始文档数 | 文档特点 |
|------|--------|-----------|---------|
| insurance | 20 | 16 | 保险条款，公式密集，计算题集中 |
| regulatory | 20 | 26 | 监管法规，法条引用多，跨文档对比题多 |
| financial_contracts | 20 | 14 | 金融合同，募集说明书，数据表格多 |
| financial_reports | 20 | 10 | 年报，数据量大，财务指标对比题多 |
| research | 20 | 20 | 行业研报，分析为主，事实查找题多 |
| **合计** | **100** | **86** | |

## 数据准备流程

1. **下载数据集**：从赛方获取 `public_dataset_a.zip`（约 274MB），放到 `data/` 目录下
   - 该文件不纳入 Git 版本控制，需自行下载
2. **运行解压脚本**：
   ```bash
   python -m src.preprocess.prepare_data
   ```
   该脚本会解压 zip 包到 `data/raw_dataset/`（含 `questions/` 和 `raw/`）。
3. **Markdown 解析产物**：`data/merged_md/` 由赛题方通过 MinerU 预先提供，无需本地生成。

`merged_md/` 下每个文档是一个完整的 `{doc_id}.md` 文件，`doc_id` 与题目 JSON 中的 `doc_ids` 一一对应。

## 题目格式

每题 JSON 结构：

```json
{
  "qid": "ins_a_001",
  "domain": "insurance",
  "split": "A",
  "question": "...",
  "options": { "A": "...", "B": "...", "C": "...", "D": "..." },
  "answer_format": "mcq | multi | tf",
  "type": "推理判断 | 计算题 | ...",
  "doc_ids": ["1", "2", "15"]
}
```

- **split**: 当前均为 A 组
- **answer_format**: `mcq`（单选）、`multi`（多选）、`tf`（判断）
- **doc_ids**: 引用的文档编号，对应 `merged_md/{domain}/` 下的文件名（去后缀）
- **注意**: 题目文件中**不含标准答案**

## 维护约束

1. `data/` 整体 gitignore，不进入版本控制
2. 题目文件由赛方提供，不要手动修改
3. `data/merged_md/` 由赛题方通过 MinerU 提供，不要手动编辑
