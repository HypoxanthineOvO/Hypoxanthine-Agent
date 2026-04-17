# WeWe RSS Watchdog Design

**Date:** 2026-04-11

**Goal**

为托管在 `http://10.15.88.94:4000` 的 WeWe RSS 增加账号失效巡检与二维码登录闭环：
- 定时检测微信读书账号是否失效
- 失效时默认全通道主动通知
- 用户在任一渠道发出“我扫码登录一下”等自然语言时，只在当前渠道收到二维码
- 发送二维码后继续轮询登录结果，并在当前渠道回告成功/失败

**Current Context**

- Hypo-Agent 已具备 `APScheduler + EventQueue + ChatPipeline + ChannelRelay` 主动推送链路。
- 外部通道已支持图片附件下发，微信/QQ/WebUI 都能消费 `Message.attachments`。
- WeWe RSS 前端 bundle 已暴露管理账号所用的 tRPC procedure：
  - `account.list`
  - `account.add`
  - `account.edit`
  - `account.delete`
  - `platform.createLoginUrl`
  - `platform.getLoginResult`
- 实测这些接口受 `authCode` 保护，未提供正确 `Authorization` 时会返回 `401 authCode不正确！`。

## Approach

采用原生 API 集成，而不是浏览器自动化。

1. 新增 `WeWeRSSClient`
   - 封装 WeWe RSS 的 tRPC GET/mutation 调用
   - 统一处理 `Authorization`、错误码与返回结构
   - 提供：
     - `list_accounts()`
     - `create_login_url()`
     - `get_login_result(uuid)`
     - `add_account(id, name, token)`

2. 新增 `WeWeRSSMonitorService`
   - 负责账号状态检测、失效去重、二维码生成、登录轮询和主动推送事件组装
   - 失效检测输出进入 `EventQueue`
   - 手动扫码场景直接返回带附件的 `Message`，并显式指定 `target_channels=[当前渠道]`

3. 调度接入现有 `tasks.yaml`
   - 增加 `tasks.wewe_rss`
   - 应用启动时按 interval/cron 注册巡检任务
   - 未配置 WeWe 或未启用时静默跳过，不影响现有 heartbeat/subscription

4. Pipeline 增加一个 pre-LLM shortcut
   - 识别“我扫码登录一下”“发我二维码”“WeWe 登录二维码”等短语
   - 直接调用 `WeWeRSSMonitorService.start_login_flow(...)`
   - 避免依赖 LLM 自由发挥，保证“当前渠道回复二维码”稳定成立

## Data Model

新增配置：

- `services.wewe_rss.enabled`
- `services.wewe_rss.base_url`
- `services.wewe_rss.auth_code`
- `services.wewe_rss.login_timeout_seconds`
- `services.wewe_rss.poll_interval_seconds`

新增调度配置：

- `tasks.wewe_rss.enabled`
- `tasks.wewe_rss.mode`
- `tasks.wewe_rss.interval_minutes`
- `tasks.wewe_rss.cron`

新增持久化偏好键（用 `StructuredStore preferences` 即可，无需新表）：

- `wewe_rss.last_alert_signature`
- `wewe_rss.last_alert_at`
- `wewe_rss.last_login_started_at`
- `wewe_rss.last_login_uuid`

这样可以避免重复报警，并在重启后保留最近一次状态。

## Status Rules

从前端可知账号状态枚举：

- `0`: 失效
- `1`: 启用
- `2`: 禁用

巡检规则：

- 任一账号 `status == 0` 视为失效
- 若所有账号都不存在，也视为异常并提醒“当前没有已接入账号”
- `status == 2` 只算已禁用，不算登录异常
- 同一组异常摘要在短时间内只提醒一次；恢复后清除去重签名

## Login Flow

1. 调用 `platform.createLoginUrl`
2. 取回 `uuid` 和 `scanUrl`
3. 本地生成二维码 PNG，落到 `memory/rendered_images/` 或单独目录
4. 构造仅发当前渠道的 `Message`
5. 后台轮询 `platform.getLoginResult(uuid)`
6. 若拿到 `vid + username + token`
   - 调用 `account.add`
   - 回告“账号已恢复”
7. 若返回 `message`
   - 视为失败，回告失败原因
8. 超时则回告超时

## Delivery Rules

手动扫码：
- 当前用户在哪个渠道触发，就只回那个渠道

定时失效提醒：
- 默认广播全部外部通道
- 不限制为当前会话来源

## Error Handling

- WeWe 配置缺失：不注册调度任务；手动触发返回明确错误
- `401 UNAUTHORIZED`：提示 authCode 无效，需要更新配置
- WeWe 网络错误：归类为短暂失败，保留重试
- 二维码生成失败：回文字错误，不中断后续轮询逻辑
- `account.add` 失败：回“扫码成功但写入账号失败”

## Testing Strategy

围绕以下层次补测试：

- `WeWeRSSClient`
  - 授权 header
  - tRPC query/mutation 解析
  - 401/500 错误映射

- `WeWeRSSMonitorService`
  - 失效账号检测
  - 异常摘要去重
  - 二维码 PNG 生成
  - 登录成功/失败/超时

- `ChatPipeline`
  - 自然语言 shortcut 命中后返回当前渠道限定消息
  - 返回消息包含图片附件

- `gateway.app`
  - 启动时注册 WeWe 巡检任务
  - 未配置时不注册

## Non-Goals

- 不在本次实现中接管 WeWe 自己的网页 UI
- 不做多用户/多 authCode 管理
- 不把 WeWe 登录流程复用到 Hypo-Agent 自身微信通道
