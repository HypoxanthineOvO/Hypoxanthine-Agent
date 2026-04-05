# 心跳检查清单

你每次被唤醒时，请按以下清单自主检查并汇报。

## 调用顺序
- 优先只调用一次 `get_heartbeat_snapshot`。
- 只有当聚合结果缺某个 section、或你需要补充细节时，才额外调用单独的 snapshot 工具：
  - `get_system_snapshot`
  - `get_mail_snapshot`
  - `get_notion_todo_snapshot`
  - `get_reminder_snapshot`
- 不要为了心跳再去调用 `exec_command`、`scan_emails`、`list_reminders`、`read_file` 等底层工具；这些固定查询已经由 snapshot skill 内部完成。

## 汇报要求
- 把工具返回的 JSON 整理成自然语言中文汇报，不要原样复述 JSON。
- 服务器状态：说明 load、内存、磁盘、GPU 概览，以及按人聚合的项目/进程信息。
- Notion ToDo：重点讲今天到期未完成任务、三天内高优未完成任务、今天已完成任务。
- 邮件：重要邮件展开说明，普通邮件一句话概括；如果没有新邮件，可以简写。
- 提醒：只在有过期提醒或半天内提醒时汇报。

## 详细度要求
- 不要用“已获取”“正常”“有数据”这类空泛句子代替细节。
- 如果某个 section 带有 `human_summary`，优先沿用其中的细节，只做轻微改写，不要把它重新压缩成一句空话。
- 如果 `system.projects_by_user` 不为空，必须单独写一个“按人运行情况”小节。
- 如果 `system.project_activity_summary` 不为空，至少引用其中前 3 行的核心信息。
- “按人运行情况”至少展开前 3 个最活跃账号；如果只有 1-2 个，就全部展开。
- 每个账号至少包含：
  - 姓名/账号
  - 总 CPU / 内存占用
  - 是否占用 GPU（显存和卡号）
  - 1-3 个代表性进程或项目
- 如果 `system.top_system_processes` 有明显高 CPU 进程，也要点名 PID、用户和命令，不要只写“负载正常/偏高”。
- Notion、邮件、提醒只要有命中项，就尽量列出标题，而不是只报数量。

## 汇报规则
- 有事：统一汇报一条消息，分门别类的清晰表述，不要省略细节。
- 无事：严格静默，输出且只输出下面这一行（必须完全一致，不要加任何其他文字、空格、标点、emoji，不要加加引号/代码块）：
**SILENT**
