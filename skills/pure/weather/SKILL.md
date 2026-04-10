---
name: "weather"
description: "天气查询 workflow：通过 weather CLI 获取当前天气、指定日期天气或小时级预报。用户提到 weather、forecast、温度、降雨、体感或天气趋势时使用。"
compatibility: "linux"
allowed-tools: "exec_command"
metadata:
  hypo.category: "pure"
  hypo.backend: "exec"
  hypo.exec_profile: "cli-json"
  hypo.triggers: "天气,weather,forecast,温度,降雨,下雨,体感,湿度,风速,天气预报"
  hypo.risk: "low"
  hypo.dependencies: "cr-mb-weather-cli"
  hypo.cli_package: "cr-mb-weather-cli"
  hypo.cli_commands: "weather"
  hypo.io_format: "json-stdio"
---
# Weather 使用指南

## 定位 (Positioning)

`weather` 是一个基于外部 CLI 的天气查询 workflow，通过 `weather` 命令获取当前天气、指定日期天气或 hourly forecast，并以 `JSON stdio` 形式返回结构化结果。

## 适用场景 (Use When)

- 用户要查询某个城市、坐标或地区的当前天气。
- 用户要看明天或指定日期的天气。
- 用户要查看小时级天气趋势，例如温度、风速、降雨与体感温度。

## 工具与接口 (Tools)

- 通过 `exec_command` 调用 `weather` CLI。
- 默认命令形态是 `weather ... --format=raw`，这样 stdout 会返回 JSON。

## 标准流程 (Workflow)

1. 先确认地点输入。
2. 如果用户已经给了经纬度，优先使用 `--lat` 和 `--lon`，因为这条路径最稳定，也避免额外 geocoding。
3. 如果用户给的是城市名或地区名，可以直接传 positional `location`，但要知道这依赖 geocoding，可能比坐标更慢。
4. 默认加上 `--format=raw --timeout 10`，必要时再补 `--date`、`--hourly`、`--units` 或 `--source=open-meteo`。
5. 解析 JSON 后，用人类可读方式总结关键天气信息，而不是原样转储整段 JSON。

## 参数约定 (Parameters)

- 当前天气：
  `weather "<location>" --format=raw --timeout 10`
- 坐标查询：
  `weather --lat <lat> --lon <lon> --format=raw --timeout 10`
- 指定日期：
  `weather "<location>" --date tomorrow --format=raw --timeout 10`
- 小时级预报：
  `weather "<location>" --hourly --format=raw --timeout 10`
- 单位：
  默认 `metric`；只有用户明确要求英制单位时再加 `--units imperial`

## 边界与风险 (Guardrails)

- 不要默认使用空 location 触发 auto-detect，这会把“当前机器位置”误当成“用户位置”。
- 优先走 `open-meteo`，因为它不需要 API key，且适合零配置部署。
- 如果地点字符串查询超时或不稳定，优先建议或改用经纬度。
- 输出时应提炼温度、体感、天气状况、风速、湿度与降雨线索，不要把整个 JSON 直接塞给用户。
