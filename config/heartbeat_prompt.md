# 心跳检查清单

你每次被唤醒时，请按以下清单自主检查并汇报。

## 服务器状态
- 只用 exec_command 检查服务器负载（load average）是否过高，并给出概览：uptime、load average、CPU/内存概览。
- 如果负载不高，只需要给出一行"负载正常"的结论，不要展开长列表。
- 注意：用 exec_command 检查系统资源的时候：
  - 不要强制只用白名单命令；以"非阻塞"为原则即可。
  - 严禁使用已知阻塞/交互式/可能卡住的命令（黑名单）：`top`、`htop`、`iotop`、`iftop`、`dstat`（持续模式）、`watch`、不带结束条件的 `tail -f`/`journalctl -f` 等。
  - 系统检查必须拆成多次 `exec_command` 调用，不要把 `free/uptime/ps/df/nvidia-smi` 串成一个超长 shell。
  - 推荐按下面的粒度分别执行；每一条是一条独立的 `exec_command`：
    - 负载/概览：`uptime`
    - CPU/内存概览：`free -h`
    - GPU 概览（必须执行）：`nvidia-smi --query-gpu=index,name,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits`
    - GPU 进程（必须执行）：`nvidia-smi --query-compute-apps=gpu_uuid,gpu_bus_id,pid,process_name,used_memory --format=csv,noheader,nounits`
    - GPU UUID 映射（如需要，必须执行）：`nvidia-smi --query-gpu=gpu_uuid,index --format=csv,noheader`

## 项目概览（按人汇报）
- **人名映射规则**：在汇报时，必须读取 `memory/people/index.md` 文件，将账号（如 heyx/dingqh）映射为对应的中文姓名（如 贺云翔/丁麒涵）。如果索引中找不到该账号，则保留原账号名。
- 不需要筛「高消耗进程」。只需要回答「每个人在跑什么项目」。
- 用 exec_command 从 `ps` 输出里归并：按用户（如 heyx/dingqh/lichf/jiangye/chenqy/liyx）分组，每个用户列出主要在跑的 1-3 个命令/项目关键词即可。
- 建议命令：`ps -eo user,etime,pcpu,pmem,comm,args --sort=-pcpu | head -80`
- 输出格式要求：
  - 每个用户一行：`user(姓名): 项目/命令的整理介绍）+ 负载情况`
  - 负载情况包括：CPU / 内存 / GPU / 磁盘。记得使用非阻塞命令进行查询。
  - 如果某用户没进程就不写。

## GPU 归属规则（必须做）
- 每次心跳必须查询 GPU（不得写“GPU 未查”）。
- 需要同时汇报：
  - GPU 总览（每张卡 util/显存/温度）
  - GPU 进程列表（PID -> 显存占用）
- 必须把 GPU 进程 PID 映射回 `ps` 里的 user，并并入「项目概览（按人汇报）」的每人一行里（至少给出：该用户占用哪几张卡/显存大概多少）。
- 如果 `nvidia-smi --query-compute-apps` 返回空：明确写“当前无 GPU compute 进程”。
- 如果机器无 NVIDIA/无权限/命令不可用：明确写失败原因（stderr 关键行），不要静默跳过。

## Notion ToDo（HYX 的计划通）
- 检查 Notion 数据库 HYX 的计划通的内容：
  - 筛选出日期包含今天的未完成任务
    - 详细描述高优先级任务
    - 列出其他中低优先级的未完成任务
  - 如果有三天内到期的高优先级未完成任务，着重提醒
  - 简单描述一下今天到期的已完成任务

## 邮件
- 调用 scan_emails 扫描未读邮件。
- 🔴 重要邮件：展开讲解邮件内容，附件信息放在概要下方
- ⚪ 普通邮件：一句话概括
- 被扫描的文件全部标记为已读。

## 提醒
- 调用 list_reminders 检查：
    - 有没有漏网的过期提醒
    - 有没有临期提醒（半天以内）
    - 没有的话就不用汇报

## 汇报规则
- 有事：统一汇报一条消息，分门别类的清晰表述，不要省略细节。
- 无事：严格静默，输出且只输出下面这一行（必须完全一致，不要加任何其他文字、空格、标点、emoji，不要加加引号/代码块）：
**SILENT**
