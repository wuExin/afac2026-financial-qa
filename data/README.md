# data/ — 数据集目录

`data/` 存放赛题原始数据和解析产物。整体不纳入版本控制（已 gitignore），读者需自行下载数据集并运行解析脚本生成。

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
└── processed_pymupdf4llm/             # pymupdf4llm 解析结果（运行脚本生成）
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
2. **运行解析脚本**：
   ```bash
   python -m src.preprocess.prepare_data
   ```
   该脚本会自动解压 zip 包，并调用 `pymupdf4llm` 把所有 PDF 转成 Markdown（每页一个 `page_XXXX.md`）。

解析后的 Markdown 文件按 `{domain}/{doc_id}/page_XXXX.md` 组织，`doc_id` 与题目 JSON 中的 `doc_ids` 一一对应。

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
- **doc_ids**: 引用的文档编号，对应 `processed_pymupdf4llm/` 下的子目录名
- **注意**: 题目文件中**不含标准答案**

## 维护约束

1. `data/` 整体 gitignore，不进入版本控制
2. 题目文件由赛方提供，不要手动修改
3. 解析产物通过 `src/preprocess/prepare_data.py` 生成，不要手动编辑
