# SubscriptionSkill 多平台订阅推送技术可行性调研

调研日期：2026-04-10  
调研环境：Genesis，仓库路径 `/home/heyx/Hypo-Agent`，Python 3.12/3.13 混合运行环境，RSSHub 本地实例 `http://127.0.0.1:1200`  
调研脚本：

- `scripts/research/subscription/test_bilibili.py`
- `scripts/research/subscription/test_weibo.py`
- `scripts/research/subscription/test_zhihu.py`

## 执行摘要

结论先行：

1. **B 站可以作为首个正式接入的平台。**
   2026-04-10 在 Genesis 上，使用 `config/secrets.yaml -> services.bilibili.cookie` 中的登录态 Cookie，`x/space/wbi/arc/search` 与 `x/polymer/web-dynamic/v1/feed/space` 均已稳定返回有效数据。Wbi 签名流程已实证可用。无登录态时仍会出现 `-352/-412`，所以登录态 Cookie 仍是必需条件。
2. **微博移动端 API 当前可作为“理论主方案”，但本轮未能在无登录态条件下完成正向验证。**
   2026-04-10 实测 `m.weibo.cn/api/container/getIndex?containerid=107603{uid}` 返回 HTTP `432`，桌面 Ajax 端点 `weibo.com/ajax/statuses/mymblog` 返回登录跳转 JSON。说明微博读取链路高度依赖登录 Cookie，且抗爬更敏感。
3. **知乎不能按“用户动态/专栏统一抓取”来设计。**
   `members/{user_id}/activities` 在 2026-04-10 可返回 JSON，但对公开用户样本返回空数组；`members/{user_id}/pins` 可稳定返回公开“想法”；文章接口 `api/v4/articles/{id}` 返回 `10003`；页面层伪 RSS/Atom 返回 `403`。因此：知乎“想法”可做，知乎“专栏文章”不应承诺为 MVP 可行项。
4. **微信公众号没有公开 API，不适合作为自维护抓取主方案。**
   最现实的两个方向是：
   - 付费托管服务（WeRSS）
   - 现有微信 iLink Bot 被动监听/用户转发
   
   基于当前仓库现状，**推荐默认走 iLink Bot 被动监听**；如果强需求是“主动订阅公众号更新”，则建议把 **WeRSS 作为付费可选集成**，而不是自己维护搜狗/网页抓取。
5. **RSSHub 仍可作为兜底，但只适合挑选“当前部署中稳定”的少数路由。**
   本机 RSSHub 在 2026-04-10 确认可用的路由包括：
   - `/douban/book/latest`
   - `/douban/movie/weekly`
   - `/sspai/index`
   - `/36kr/newsflashes`
   
   GitHub 路由存在上游 rate limit，Telegram/播客/若干其它路由在当前实例不可用或超时，不能把“RSSHub 兜底”理解成“任意路由都稳”。

## 1. B 站 UP 主视频 / 动态更新

### 1.1 Wbi 签名算法结论

已按 `bilibili-API-collect` 文档验证签名流程：

1. 请求 `https://api.bilibili.com/x/web-interface/nav`
2. 从 `data.wbi_img.img_url` 与 `data.wbi_img.sub_url` 取文件名 stem，得到 `img_key` 与 `sub_key`
3. 将二者拼接后按固定索引表重排，取前 32 位得到 `mixin_key`
4. 对请求参数按 key 排序，过滤 `!'()*`
5. 拼接 `wts`
6. `urlencode(sorted_params) + mixin_key` 做 MD5，得到 `w_rid`

2026-04-10 在 Genesis 上实测：

- `img_key = 7cd084941338484aae1ad9425b84077c`
- `sub_key = 4932caff0ff746eab6f01bf08b70ac45`
- `mixin_key = ea1db124af3c7062474693fa704f4ff8`

脚本实现位置：`scripts/research/subscription/test_bilibili.py`

### 1.2 关键 API 端点实测

测试对象：`uid=546195`  
测试日期：2026-04-10

#### A. 投稿列表

端点：`GET https://api.bilibili.com/x/space/wbi/arc/search?mid={uid}&ps=10&pn=1`

实测结果：

- 无签名：返回 `code=-352`
- 有签名但无登录态：出现两种结果
  - HTTP `412` + JSON `{ "code": -412, "message": "request was banned" }`
  - HTTP `200` + JSON `{ "code": -352, "message": "风控校验失败" }`
- 使用 `services.bilibili.cookie` 中的登录态 Cookie 后，连续两轮请求均成功：
  - HTTP `200`
  - JSON `{"code":0,"message":"OK"}`
  - 返回最近 5 条投稿

成功样本字段：

- `aid=116304811986964`
- `bvid=BV1tFXVBLE5a`
- `title=抛硬币！连续十次正面就通关！！`
- `created=1774670898`

结论：

- **Wbi 签名是必要条件**
- **登录态 Cookie 是必要条件**
- **在 Genesis 上，该端点已完成正向验证，可用于后续开发**

#### B. UP 主动态

端点：`GET https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space?host_mid={uid}`

实测结果：

- 无登录态时大多数请求返回 `code=-352`
- 使用 `services.bilibili.cookie` 中的登录态 Cookie，并先访问空间动态页后，连续两轮请求均成功：
  - HTTP `200`
  - JSON `{"code":0,"message":"0"}`
  - `items=12`

成功样本包括：

- `DYNAMIC_TYPE_AV`
  - `id_str=1184691558132219928`
  - `author=老番茄`
  - `pub_ts=1774670898`
  - `title=抛硬币！连续十次正面就通关！！`
  - `bvid=BV1tFXVBLE5a`
- `DYNAMIC_TYPE_FORWARD`
  - `id_str=1176232439618469910`
  - `text=听说雨哥要被AI硬控了，还有空降嘉宾...`

结论：

- 该端点也受风控影响
- 但在当前登录态 Cookie 下已经完成正向验证，可用于后续 diff 设计

### 1.3 Cookie 字段结论

在 Genesis 上基于真实登录态做了删减实验。

#### `arc/search`（投稿列表）矩阵

五次重复测试结果：

- `SESSDATA`：`[(200,0), (200,0), (200,0), (412,-412), (200,0)]`
- `SESSDATA + buvid3`：`[(200,0), (200,0), (200,0), (412,-412), (200,0)]`
- `SESSDATA + bili_jct`：`[(200,0), (412,-412), (200,0), (412,-412), (412,-412)]`
- `SESSDATA + DedeUserID`：`[(200,0), (200,0), (200,0), (200,0), (200,0)]`
- `SESSDATA + bili_jct + DedeUserID`：`[(200,0), (200,0), (200,0), (200,0), (200,0)]`
- 全量 Cookie：`[(200,0), (200,0), (200,0), (200,0), (200,0)]`

#### `web-dynamic`（动态）矩阵

在先访问 `https://space.bilibili.com/{uid}/dynamic` 的前提下：

- `SESSDATA`：5/5 成功，且 `items=12`
- `SESSDATA + DedeUserID`：5/5 成功，且 `items=12`
- 全量 Cookie：5/5 成功，且 `items=12`

结论：

- **动态接口最低可行 Cookie 很可能是 `SESSDATA`**
- **投稿列表接口用 `SESSDATA` 也能多数成功，但存在偶发 `-412`**
- **`SESSDATA + DedeUserID` 在本轮测试里最稳定**
- `bili_jct` 对 GET 接口不是必需项
- `buvid3` 不是稳定性的决定性条件

因此当前推荐最小集不是“理论最小”，而是“工程最稳妥”：

1. `SESSDATA`
2. `DedeUserID`
3. `bili_jct`（保留，便于后续扩展需要 CSRF 的端点）

如果用户直接提供完整浏览器 Cookie，则优先完整保存，后续再逐步裁剪。

### 1.4 频率限制与 5 分钟轮询判断

2026-04-10 本轮未观察到以下响应头：

- `X-RateLimit-Limit`
- `X-RateLimit-Remaining`
- `Retry-After`

但由于当前请求已经在风控层被拦截，**不能据此得出“5 分钟轮询安全”**。

建议：

- 初始轮询周期：**10 分钟**
- 为每个订阅增加 30-90 秒随机抖动
- 连续出现 `-352/-412` 时，指数退避到 30-60 分钟
- 只有在带真实登录态运行 48 小时后，再决定是否缩到 5 分钟

### 1.5 返回数据结构

`arc/search` 已取得成功响应样本，投稿 diff 关键字段建议保留：

- `aid`
- `bvid`
- `title`
- `created`

`dynamic` 已取得成功响应样本，字段路径已验证：

- `item.id_str`
- `item.type`
- `modules.module_author.pub_ts`
- `modules.module_dynamic.desc.text`
- `modules.module_dynamic.major.archive.title`
- `modules.module_dynamic.major.archive.jump_url`
- `modules.module_dynamic.major.archive.bvid`

说明：

- `DYNAMIC_TYPE_AV` 与 `DYNAMIC_TYPE_FORWARD` 都已经有非空样本
- 后续实现 diff 时可优先使用：
  - `id_str` 作为动态唯一键
  - `pub_ts` 作为排序时间
  - `title/text/bvid` 作为通知正文

### 1.6 风险评估

- Cookie 过期：中高
- 风控触发：中高
- 签名算法变更：中
- 封号 / 限流风险：中高

工程判断：

- **可做，且已经完成 PoC 正向验证**
- 但必须标为“高维护、风控敏感”集成

推荐优先级：

- `BilibiliVideoFetcher` 先做
- `BilibiliDynamicFetcher` 次之

## 2. 微博用户动态

### 2.1 端点实测

测试对象：`uid=1195230310`  
测试日期：2026-04-10

#### A. 移动端 API

端点：`GET https://m.weibo.cn/api/container/getIndex?containerid=107603{uid}`

实测结果：

- HTTP `432`
- 返回体不是可解析 JSON

结论：

- 当前无登录态情况下，移动端接口已被风控/访问控制拦截

#### B. 桌面 Ajax 备选

测试端点：

- `https://weibo.com/ajax/statuses/mymblog?uid={uid}&page=1&feature=0`
- `https://weibo.com/ajax/profile/getWaterFallContent?uid={uid}`

实测结果：

- 返回 JSON 登录跳转
- 示例：`{"ok":-100,"url":"https://weibo.com/login.php?..."}`

结论：

- 桌面 Ajax 端点同样依赖登录态
- 如果移动端 API 不稳定，桌面 Ajax 可以作为**备选实现路径**，但仍然不是“免登录方案”

### 2.2 Cookie 与有效期结论

本轮没有可用微博登录 Cookie，无法验证：

- 最小 Cookie 集
- Cookie 失效周期
- 10 分钟轮询是否稳定

能确认的工程事实：

- **没有登录态就不具备可用性**
- 微博应把 Cookie 视为必需依赖，而不是优化项

建议：

- MVP 支持直接导入完整浏览器 Cookie
- 后续再根据实跑日志确认哪些字段是必需的

### 2.3 返回数据结构设计

脚本已按两类结构实现解析器：

移动端 `m.weibo.cn`：

- `mblog.id`
- `mblog.mid`
- `mblog.created_at`
- `mblog.text`
- `retweeted_status`
- `source`
- `reposts_count`
- `comments_count`
- `attitudes_count`

桌面 Ajax：

- `id`
- `mblogid`
- `created_at`
- `text_raw`
- `retweeted_status`
- `reposts_count`
- `comments_count`
- `attitudes_count`

这些字段结构基于当前端点返回约定设计，但**未在登录成功条件下做带数据样本验证**。

### 2.4 替代方案

如果 `m.weibo.cn` 在真实 Cookie 下仍不稳定，备选顺序建议：

1. `weibo.com/ajax/*` 桌面 Ajax + Cookie
2. 有状态浏览器抓取（Playwright）
3. RSSHub / 第三方 RSS
4. 无状态网页抓取

原因：

- 微博页面与移动端都在强风控，Playwright 成本高但成功率往往高于“裸 httpx”
- RSSHub 已知对微博路由失效率高，不宜作为主方案

### 2.5 风险评估

- Cookie 过期：高
- 反爬与 IP 风险：高
- 数据结构变更：中
- 维护成本：高

工程判断：

- **可做，但不建议和 B 站同优先级**
- 应列为 `experimental`，需要单独开关与熔断

### 2.6 带 Cookie 复验（2026-04-11）

- 端点：
  - `https://m.weibo.cn/api/container/getIndex?containerid=1076031195230310`
  - `https://weibo.com/ajax/statuses/mymblog?uid=1195230310&page=1&feature=0`
  - `https://weibo.com/ajax/profile/getWaterFallContent?uid=1195230310`
- Cookie 来源：`config/secrets.yaml -> services.weibo.cookie`
- HTTP 状态码：
  - 移动端 API：连续 3 次均为 `200`
  - 桌面 Ajax `mymblog`：连续 3 次均为 `200`
  - 桌面 Ajax `getWaterFallContent`：连续 3 次均为 `200`
- 返回摘要：
  - 移动端 API 不再返回 `432`，但连续 3 次均返回 JSON `{"ok":-100,"url":"https://passport.weibo.com/sso/signin?..."}`
  - `mymblog` 连续 3 次均返回 `{"ok":1}`，`data.list` 长度稳定为 `13`
  - `mymblog` 成功样本关键字段已确认：
    - `id=5267301378036174`
    - `mid=5267301378036174`
    - `mblogid=QseOBxIzI`
    - `created_at=Tue Feb 17 13:26:13 +0800 2026`
    - `user.screen_name=何炅`
    - `text_raw` 可直接用于正文摘要
    - `retweeted_status` 可判断是否转发
  - `getWaterFallContent` 连续 3 次均返回 `{"ok":1}`，`data.list` 长度稳定为 `3`
- 失败端点和错误：
  - 移动端 `m.weibo.cn`：当前 Cookie 下表现为登录跳转 JSON（`ok=-100`），不能作为主实现端点
  - 初始 PoC 脚本使用较弱请求头时，桌面 Ajax 曾出现 `403`；补齐桌面端请求头与 Referer 后恢复为稳定 `200`
- 结论：✅ 可行。当前工程主方案应调整为“桌面 Ajax 主抓取，移动端仅做可选探测/回退信号”，并保持 `experimental` 标记。

## 3. 知乎用户动态 / 专栏

### 3.1 `activities` 端点

端点：`https://www.zhihu.com/api/v4/members/{user_id}/activities`

测试对象：`user_id=zhang-jia-wei`  
测试日期：2026-04-10

实测结果：

- HTTP `200`
- 返回 JSON
- `data=[]`

对多个用户样本（如 `openai`、`li-kai-fu`、`hupili`）结果一致：空数组。

结论：

- 端点仍然存在
- 但在当前无额外认证/签名参数条件下，**实用性不足**
- 不能把它当成稳定的“用户动态主接口”

### 3.2 `pins` 端点

端点：`https://www.zhihu.com/api/v4/members/{user_id}/pins`

实测结果：稳定可用。

2026-04-10 对 `zhang-jia-wei` 返回了最近公开想法，脚本已提取字段：

- `id`
- `type`
- `created`
- `updated`
- `excerpt_title`
- `author.name`
- `author.url_token`
- `url`

这是本轮知乎部分**唯一稳定验证通过**的用户内容端点。

### 3.3 专栏文章

测试端点：`https://www.zhihu.com/api/v4/articles/{id}`

实测结果：

- HTTP `403`
- JSON 错误：
  - `code=10003`
  - `message=请求参数异常，请升级客户端后重试。`

结论：

- 专栏文章接口不是简单公开 REST
- 需要额外客户端参数 / 签名 / 更完整会话

### 3.4 Atom / RSS

测试 URL：

- `https://www.zhihu.com/people/{user_id}/activities/rss`
- `https://www.zhihu.com/people/{user_id}/posts/rss`

实测结果（2026-04-10）：

- 均为 HTTP `403`
- 返回 HTML 反爬页，不是 Atom/RSS

结论：

- **知乎不存在可直接依赖的公开 Atom/RSS 输出**

### 3.5 轮询与 Cookie 结论

本轮能确认：

- `pins` 在无登录态条件下可拉取
- `activities` 与 `articles` 没有形成完整可用链路

建议：

- 知乎 MVP 只支持 **Pins（想法）**
- `activities` 作为未来研究项
- `articles` 不纳入 MVP 承诺

轮询建议：

- `pins`：10 分钟
- 遇到 `403/10003`：立即标记为 `auth_or_client_signature_required`

### 3.6 风险评估

- API 行为不一致：高
- 反爬策略：中高
- 专栏抓取可维护性：低

工程判断：

- **知乎“想法”可做**
- **知乎“专栏订阅”本轮判定为不可作为 MVP 主功能**

### 3.7 带 Cookie 复验（2026-04-11）

- 端点：
  - `https://www.zhihu.com/api/v4/members/zhang-jia-wei/pins`
  - `https://www.zhihu.com/api/v4/members/zhang-jia-wei/activities`
- Cookie 来源：`config/secrets.yaml -> services.zhihu.cookie`
- HTTP 状态码：
  - `pins`：`200`
  - `activities`：`200`
- 返回摘要：
  - `pins` 返回非空数据，样本条目关键字段已确认：
    - `id=2025938258127758699`
    - `type=pin`
    - `created=1775801322`
    - `updated=1775801322`
    - `excerpt_title=中国文学史上情绪最稳定、最不内耗最不迷…`
    - `author.name=张佳玮`
    - `author.url_token=zhang-jia-wei`
    - `url=/pins/2025938258127758699`
  - `activities` 虽返回 `200`，但 `data=[]`，在当前样本下仍未形成可用动态流
- 失败端点和错误：
  - `https://www.zhihu.com/api/v4/articles/254930530` 返回 `403` + `code=10003`
  - `https://www.zhihu.com/people/zhang-jia-wei/activities/rss` 返回 `403`
  - `https://www.zhihu.com/people/zhang-jia-wei/posts/rss` 返回 `403`
- 结论：✅ `pins` 可行；🔶 `activities` 在当前 Cookie 和样本下仍为空，不建议本轮实现 `ZhihuActivityFetcher`。

## 4. 微信公众号

### 4.1 WeRSS

官方站点（2026-04-10）明确写明：

- 微信官方对接口与内容获取施加限制
- 会员每月有文章抓取额度
- 新增订阅量也受计划档位限制

工程理解：

- WeRSS 是“付费托管抓取服务”，不是公开 API
- 其价值在于把反爬与维护成本外包

判断：

- **如果业务目标是“尽快支持公众号主动订阅”，WeRSS 是最现实的外部方案**
- 风险是：
  - 依赖第三方服务商
  - 成本持续发生
  - 仍可能受微信官方策略影响

### 4.2 iLink Bot 被动监听

当前仓库已有完整微信 iLink 通道实现：

- `scripts/demo_weixin.py`
- `src/hypo_agent/channels/weixin/ilink_client.py`
- `src/hypo_agent/channels/weixin/weixin_channel.py`

本机状态（2026-04-10）：

- `memory/weixin_auth.json` 存在
- 持有有效格式的 `bot_token`、`user_id`、`baseurl`

这说明：

- Genesis 上已经有可复用的微信 Bot 登录态
- 现有系统具备接收用户消息、回发消息、记录上下文 token 的能力

因此“用户手动转发公众号推文给 Bot，由 Bot 识别并记账”是**高可行性方案**。

优点：

- 不依赖公众号公开接口
- 基本不碰外部抓取与反爬
- 与当前仓库能力匹配度最高

缺点：

- 不是“主动订阅”
- 依赖用户转发动作

推荐定位：

- **默认推荐方案**
- 可以作为公众号能力的 MVP

### 4.3 搜狗微信搜索

2026-04-10 实测：

- `https://weixin.sogou.com/` 可访问
- 搜索页也可返回 HTML

但问题在于：

- 页面结构脆弱
- 解析结果并不稳定
- 典型高风控站点，持续抓取非常容易碰到验证码 / 访问频控

判断：

- **不建议作为主方案**
- 最多只适合一次性人工补录，不适合后台订阅轮询

### 4.4 开源项目盘点

2026-04-10 观察到仍有活跃项目：

- `cooderl/wewe-rss`
- `rachelos/we-mp-rss`
- `hellodword/wechat-feeds`

但对 Hypo-Agent 来说，问题不在“有没有项目”，而在：

- 是否要自己承接公众号反爬维护
- 是否愿意为浏览器自动化、登录态、规则更新付长期成本

建议：

- 可把这些项目作为“未来自建替代 WeRSS”的研究储备
- **不建议在 SubscriptionSkill 的第一版里直接内嵌这类抓取器**

另外，`feeddd/feeds` 更像 feeds 仓库与聚合结果，不是适合直接嵌入的公众号抓取后端，不建议作为主集成目标。

### 4.5 公众号最终建议

优先级建议：

1. **默认方案：iLink Bot 被动监听 / 用户转发**
2. **增强方案：对愿意付费的用户接入 WeRSS**
3. **备选研究：自建 `wewe-rss` / `we-mp-rss` 类抓取器**
4. **不推荐：搜狗微信搜索轮询**

## 5. RSSHub 残存可用路由盘点

本机实例：`http://127.0.0.1:1200`  
实例错误页显示版本信息：

- Git Hash: `e86c679a`
- Git Date: `Fri, 20 Mar 2026 15:15:55 GMT`

### 5.1 2026-04-10 实测可用

- `/douban/book/latest`
- `/douban/movie/weekly`
- `/sspai/index`
- `/36kr/newsflashes`

这些路由均返回 `200` + `application/xml`

### 5.2 2026-04-10 实测不可用或不稳定

用户要求测试的路由：

- `/github/repos/{user}`
  - 首次短暂成功，随后因 GitHub API rate limit 返回 `503`
  - 错误：`403 rate limit exceeded`
- `/github/issue/{user}/{repo}`
  - `503`
  - 错误：GitHub API rate limit
- `/podcast/{id}`
  - `503`
  - 错误：`NotFoundError`
- `/telegram/channel/{id}`
  - 读取超时

另外抽测失败：

- `/npm/typescript` -> `NotFoundError`
- `/dockerhub/image/library/python` -> `NotFoundError`
- `/bilibili/ranking/0/0/1` -> `Error: -352`
- `/hackernews/best` -> `fetch failed`
- `/v2ex/topics/python` -> `fetch failed`

### 5.3 对 `RSSFetcher` 的建议

不要做成“任意 RSSHub 路由都能配”的开放能力，建议改成：

- 维护一份**白名单路由注册表**
- 只启用已经在本机实例验证过的路由
- 每个路由记录：
  - `route`
  - `sample_target`
  - `last_verified_at`
  - `failure_mode`

当前推荐白名单：

- `douban_book_latest`
- `douban_movie_weekly`
- `sspai_index`
- `kr36_newsflashes`

GitHub 路由可列为“条件可用”，但需要上游 token 或请求缓存后再考虑纳入。

## 6. 架构设计建议

### 6.1 BaseFetcher 接口

建议按“抓取、归一化、判错、通知格式化”拆开：

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass
class NormalizedItem:
    platform: str
    subscription_id: str
    item_id: str
    item_type: str
    title: str
    summary: str
    url: str
    author_id: str
    author_name: str
    published_at: datetime | None
    raw_payload: dict[str, Any]
    content_hash: str


@dataclass
class FetchResult:
    ok: bool
    items: list[NormalizedItem]
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = True
    auth_stale: bool = False


class BaseFetcher(Protocol):
    platform: str

    async def fetch_latest(self, subscription: dict[str, Any]) -> FetchResult: ...

    def diff(
        self,
        stored_items: list[dict[str, Any]],
        fetched_items: list[NormalizedItem],
    ) -> list[NormalizedItem]: ...

    def format_notification(self, item: NormalizedItem) -> str: ...

    def classify_error(self, payload: dict[str, Any] | Exception) -> tuple[str, bool, bool]: ...
```

关键点：

- `fetch_latest` 只负责 IO
- `diff` 做平台无关的新旧判定
- `format_notification` 保留平台定制空间
- `classify_error` 统一处理 `auth_stale / retryable / anti_bot`

### 6.2 SQLite 表设计

#### `subscriptions`

推荐字段：

- `id TEXT PRIMARY KEY`
- `platform TEXT NOT NULL`
- `target_id TEXT NOT NULL`
- `target_name TEXT`
- `fetcher_key TEXT NOT NULL`
- `poll_interval_sec INTEGER NOT NULL`
- `auth_profile_id TEXT`
- `enabled INTEGER NOT NULL DEFAULT 1`
- `last_success_at TEXT`
- `last_checked_at TEXT`
- `last_error_code TEXT`
- `last_error_message TEXT`
- `consecutive_failures INTEGER NOT NULL DEFAULT 0`
- `next_poll_at TEXT`
- `config_json TEXT NOT NULL DEFAULT '{}'`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

约束建议：

- `UNIQUE(platform, target_id)`

#### `subscription_items`

- `id TEXT PRIMARY KEY`
- `subscription_id TEXT NOT NULL`
- `platform_item_id TEXT NOT NULL`
- `item_type TEXT NOT NULL`
- `title TEXT NOT NULL`
- `summary TEXT NOT NULL DEFAULT ''`
- `url TEXT NOT NULL`
- `author_id TEXT`
- `author_name TEXT`
- `published_at TEXT`
- `content_hash TEXT NOT NULL`
- `raw_json TEXT NOT NULL`
- `first_seen_at TEXT NOT NULL`
- `last_seen_at TEXT NOT NULL`
- `notified_at TEXT`

约束建议：

- `UNIQUE(subscription_id, platform_item_id)`
- `INDEX(subscription_id, published_at DESC)`

### 6.3 Cookie 管理策略

推荐分层：

1. **SQLite 不存原始 Cookie**
2. `subscriptions.auth_profile_id` 只保存引用
3. 原始 Cookie 保存在：
   - MVP：`config/secrets.yaml` 或环境变量
   - 更稳妥：`memory/private/subscription_cookies/<profile>.json`，权限 `600`

Cookie 刷新策略：

- MVP 不做自动刷新
- 只做：
  - 失效检测
  - 主动告警
  - 手动更新入口

原因：

- B 站、微博、知乎都没有值得信赖的“纯 HTTP 自动续期”路径
- 做自动刷新意味着要引入浏览器自动化或扫码重新登录，复杂度明显超出本阶段

过期检测建议：

- B 站：
  - `-101`
  - `-352`
  - `-412`
- 微博：
  - HTTP `432`
  - `ok=-100`
  - 登录跳转 URL
- 知乎：
  - `401`
  - `403`
  - `10003`

### 6.4 错误处理策略

统一分 5 类：

1. `auth_stale`
2. `anti_bot`
3. `rate_limited`
4. `network`
5. `schema_changed`

处理建议：

- `auth_stale`
  - 暂停该订阅
  - 推送“需要更新 Cookie”
- `anti_bot`
  - 指数退避
  - 记录失败计数
  - 触发平台级熔断
- `rate_limited`
  - 平台级统一冷却
- `network`
  - 正常重试
- `schema_changed`
  - 标记需要人工检查
  - 保留原始响应片段

建议阈值：

- 连续 3 次 `auth_stale` -> 直接 disable，并通知
- 连续 5 次 `anti_bot` -> 该平台 6 小时冷却
- 连续 10 次非致命错误 -> 订阅退避到 24 小时

### 6.5 推送消息模板

#### B 站视频

```text
[B站投稿更新] {author_name}
{title}
发布时间：{published_at}
链接：{url}
```

#### B 站动态

```text
[B站动态更新] {author_name}
{summary}
时间：{published_at}
链接：{url}
```

#### 微博

```text
[微博更新] {author_name}
{summary}
时间：{published_at}
链接：{url}
```

#### 知乎想法

```text
[知乎想法更新] {author_name}
{title_or_excerpt}
时间：{published_at}
链接：{url}
```

#### 微信公众号

主动抓取型：

```text
[公众号更新] {account_name}
{title}
发布时间：{published_at}
链接：{url}
```

被动转发型：

```text
[公众号转发记录] {account_name}
标题：{title}
来源：用户手动转发
记录时间：{recorded_at}
```

## 7. 最终推荐方案

### 7.1 MVP 范围建议

应该做：

1. B 站 UP 主投稿
2. B 站动态
3. 微博用户动态（标记 experimental）
4. 知乎想法（Pins）
5. 微信公众号被动转发记录
6. RSSHub 白名单兜底源

不应该承诺：

1. 知乎专栏文章主动订阅
2. 微信公众号自维护主动抓取
3. 任意 RSSHub 路由通配

### 7.2 实现顺序建议

1. `BilibiliVideoFetcher`
2. `BilibiliDynamicFetcher`
3. `ZhihuPinsFetcher`
4. `RSSHubWhitelistFetcher`
5. `WeiboFetcher`
6. `WeixinForwardedArticleRecorder`

### 7.3 风险最高的点

1. B 站风控导致 `-352/-412`
2. 微博 Cookie 与反爬不稳定
3. 微信公众号如果坚持自建抓取，会迅速演变成长期维护项目

## 8. 本轮未完成项

以下项由于当前服务器上**没有可用 B 站 / 微博 / 知乎登录 Cookie**，本轮无法诚实完成：

1. 微博“移动端 Cookie 有效期”的测量
2. 微博“10 分钟轮询稳定性”的测量
3. 知乎“需要何种认证才能稳定读 activities/articles”的最终确认

但这并不影响当前阶段的技术结论：

- B 站可做，且已验证 Wbi + 登录态 Cookie 方案
- 微博可做，但风险明显高于 B 站
- 知乎只建议先做 Pins
- 微信公众号优先被动监听，不要自建主动抓取

## 9. 外部参考

- B 站 Wbi 签名文档：<https://raw.githubusercontent.com/SocialSisterYi/bilibili-API-collect/master/docs/misc/sign/wbi.md>
- B 站 API collect 中关于 `-352` 的讨论：<https://github.com/SocialSisterYi/bilibili-API-collect/issues/686>
- WeRSS 首页：<https://werss.app/>
- WeRSS 帮助页：<https://werss.app/help>
- `cooderl/wewe-rss`：<https://github.com/cooderl/wewe-rss>
- `rachelos/we-mp-rss`：<https://github.com/rachelos/we-mp-rss>
- `hellodword/wechat-feeds`：<https://github.com/hellodword/wechat-feeds>
- `feeddd/feeds`：<https://github.com/feeddd/feeds>
