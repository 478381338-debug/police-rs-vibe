# 现场核查样本接入说明

## 为什么仍显示“未核实”

现有 83 条样本中，53 条来自“青山卫士任务二”的历史遥感检测结果，30 条是
AOI 背景格网。它们没有对应的现场核查笔录、处罚决定或应急记录，因此不能
诚实地标记为 `field_verified`。本系统不会用公开 GitHub 仓库里的其他地区数据
冒充本地真值。

## 如何接入真实核查记录

1. 复制模板：

   ```text
   data/samples/verification_records_template.csv
   → data/samples/verification_records.csv
   ```

2. 每行填写一个 `sample_id`（可在 `/api/verification-samples` 或页面样本
   弹窗中查看）。`status` 使用以下之一：

   - `field_verified` / `verified` / `confirmed`：有现场核查证据，计入已核实；
   - `rejected`：现场否定该样本；
   - `reviewed`：已复核但证据不足，不计入已核实。

3. 必须填写 `verified_by`、`verified_at`、`source`（例如“现场核查表编号”）。
   建议在 `note` 中写清证据类型，不要把“遥感影像看起来像”写成现场核实。

4. 重新生成样本摘要：

   ```bash
   .venv/Scripts/python.exe -m app.samples
   ```

5. 重启服务后，`/api/verification-samples` 的 `verified_count` 会更新；页面
   “数据质量”面板同步显示。

## 使用边界

- `verified_count=0` 时，Top4 回溯覆盖率只是历史筛查覆盖，不是预报准确率。
- 样本量、时空覆盖和类别不平衡仍会限制校准；即使全部核实，也应做时间外
  （out-of-time）验证，而不是随机切分。
- 公开站点没有登录，因此**不提供匿名写入接口**。核查记录只通过本地 CSV
  导入，避免访客篡改样本。
