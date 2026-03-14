# B1+ Chat UI Sync Design

## Goal

Improve the chat experience around the single `main` session by fixing QQ/WebUI real-time sync, simplifying the chat layout, improving mobile navigation, and adding an empty-state welcome screen.

## Scope

- Keep backend multi-session capability intact.
- Simplify the frontend to present a single-session UI by default.
- Preserve existing streaming UX for the active WebUI sender.
- Add best-effort full-message synchronization for other WebUI clients and QQ mirror notifications for WebUI-origin conversations.

## Backend Design

### WebUI Connection Model

- Extend the WebSocket connection manager to track connections by `client_id`.
- Keep a best-effort broadcast path that can exclude the current sender connection while still broadcasting to other WebUI tabs.
- Distinguish between:
  - streaming events for the active sender (`assistant_chunk`, `assistant_done`, tool events)
  - full message sync events for other listeners

### Message Sync Rules

- QQ inbound user messages:
  - persist as normal `Message(channel="qq")`
  - broadcast the full inbound user message to WebUI listeners on session `main`
- QQ-origin assistant replies:
  - continue through the existing pipeline
  - broadcast the final full assistant message to WebUI listeners
- WebUI inbound user messages:
  - immediately broadcast the full inbound user message to other WebUI listeners, excluding the sender connection
  - persist and process as normal
- WebUI-origin assistant replies:
  - broadcast the final full assistant message to other WebUI listeners, excluding the sender connection
  - do not use the default QQ channel broadcast path for the final assistant text

### WebUI -> QQ Mirror Notifications

- For WebUI-origin conversations, mirror two notification messages to QQ via the existing NapCat HTTP sender:
  - `[WebUI] User: {text}`
  - `[WebUI] Assistant: {text}`
- The mirror target comes from `services.qq.allowed_users` in `secrets.yaml`.
- If QQ is disabled or disconnected, skip silently.
- Truncate mirrored assistant text at 500 characters and append `... [完整内容请查看 WebUI]`.
- Mirror notifications are delivery-only and must not be written into session history.

### Message Metadata

- Use the existing `channel` field as the display source (`webui`, `qq`, `system`).
- Add transient metadata for transport-only routing, such as originating `webui_client_id`, but do not rely on it for persisted history rendering.

## Frontend Design

### Single Session Chat Shell

- Remove the session list sidebar and new-session action from the default Chat UI.
- Keep `sessionId` prop / query-driven override support for development and future multi-session work.
- Replace the current header with a cleaner single-session console header focused on Hypo-Agent, connection state, and lightweight actions.

### Empty State Welcome

- Show a welcome hero when `main` has no messages.
- Include four quick prompts:
  - `📧 帮我看看邮件`
  - `📁 今天有什么任务？`
  - `🔧 检查系统状态`
  - `💬 随便聊聊`
- Clicking a quick prompt fills the composer instead of auto-sending.

### Source-Aware Messages

- Render source badges on synced messages:
  - `via QQ` for `channel="qq"`
  - `via WebUI` only when useful for mirrored multi-tab awareness
- Keep reminder / heartbeat badges working.

### Mobile Navigation

- On widths `<= 768px`, replace the hidden side rail with a drawer-triggered navigation menu.
- Keep Dashboard / Chat / Config / Memory reachable.
- Preserve the existing desktop side rail for wider layouts.

## Testing Strategy

- Backend:
  - websocket sync between two WebUI clients
  - QQ inbound message mirrored to WebUI
  - WebUI-origin QQ mirror notification behavior
- Frontend:
  - no session sidebar rendered in ChatView
  - empty-state welcome screen appears only with empty history
  - quick prompt click fills the composer
  - source badges render
  - mobile navigation trigger is visible and operable
