# C5/M6 端到端回放验收

## 验收结论

C5 已达到 pending_acceptance：可恢复的工具/模型中间失败不会再直接外显；长任务使用稳定的调用中/任务状态；最终失败保留一条摘要；非阻塞消息 runtime 默认开启。

## 真实样本回放

| 样本 | 旧表现 | 新表现 |
| --- | --- | --- |
| Notion 计划通：`May 24-27, 2026，ISCAS会议 / 重要` | 先显示 `Notion page not found: HYX的计划通`，后又宣称成功。 | 非终态 page miss 折叠为内部恢复；最终成功只展示写入结果，最终失败只展示一条摘要。 |
| TicNote 搜索：`ticnote的悦享版甄选版至尊版青春版有啥区别` | 外显 `Request timed out after 60 seconds`，用户追问后又成功。 | 搜索 timeout 会内部 retry；成功 fallback 不外显裸 timeout。 |
| 图片解释：`解释一下这个图片` | 先显示两次 `读取文件 失败：File not found...`，最终又解释成功。 | 原路径 miss 可从上传目录恢复；成功时不显示中间读取失败。 |

## 模拟测试矩阵

| 场景 | 覆盖测试 | 结果 |
| --- | --- | --- |
| recoverable tool error + final success 不外显失败 | `tests/core/test_channel_progress.py` | passed |
| recoverable model timeout + final success 不外显 timeout | `tests/unit/test_pipeline_error_handling.py`、`tests/core/test_pipeline.py` | passed |
| terminal tool failure 只显示摘要 | `tests/core/test_channel_progress.py` | passed |
| pure chat 无调用中状态 | 渠道 progress 单元测试和 pipeline 流式路径 | passed |
| long-running search 最多一个调用中 | `tests/core/test_channel_progress.py` | passed |
| concurrent messages 慢任务不阻塞第二条入队 | `tests/core/test_pipeline_event_consumer.py` | passed |
| search/web_read timeout 内部 retry | `tests/skills/test_agent_search_skill.py` | passed |
| 微信图片路径 miss 后 upload fallback | `tests/skills/test_fs_skill.py` | passed |

## 本轮验证

- `uv run pytest -q`
- 结果：全量后端测试 passed。
- `git diff --check`
- 结果：passed。

## 残余风险

- 真实 Notion/渠道写入未在验收中执行，以避免额外外部副作用；当前通过 mock/test-mode 覆盖。
- 具体模型 provider 的线上超时概率仍取决于服务质量；本轮保证用户侧不再看到可恢复链路中的裸错误。
