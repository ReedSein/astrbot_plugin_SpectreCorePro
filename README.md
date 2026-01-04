# SpectreCore Pro

**SpectreCore Pro** 专注于深度人设扮演与上下文增强。通过强制思维链 (CoT)、动态记忆注入及用户档案系统，提供沉浸式交互体验。

## 核心功能

*   **思维链强制 (CoT)**: 强制 LLM 在 `<ROSAOS>` 标签内先思考再回复，支持内容预填充与格式校验。
*   **记忆与档案**: 自动维护用户画像 (Dossier)，并联动 `Mnemosyne` 插件读取长期记忆。
*   **合并转发分析**: 支持解析合并转发消息内容，并使用独立 Prompt 进行总结或评价 (仅 OneBot v11)。
*   **智能交互**: 支持概率主动插话、关键词触发、空@唤醒及静默模式 (输出 `<NO_RESPONSE>` 时不回复)。

## 指令列表

指令前缀: `/sc` 或 `/spectrecore`

| 指令 | 参数 | 权限 | 说明 |
| :--- | :--- | :--- | :--- |
| `/sc help` | 无 | 所有人 | 显示帮助信息 |
| `/sc reset` | `[群号]` | 管理员 | 重置当前或指定群组/私聊的会话历史 |
| `/sc groupreset` | `<群号>` | 管理员 | 强制重置指定群组的历史记录 |
| `/sc mute` | `[分钟]` | 管理员 | 临时禁言 Bot (默认 5 分钟) |
| `/sc unmute` | 无 | 管理员 | 解除禁言状态 |
| `/sc callllm` | 无 | 管理员 | 手动触发一次 LLM 调用 (调试用) |

## 关键配置

建议在 WebUI 配置，或修改 `config.json`:

*   **基础**:
    *   `persona`: 绑定的人设 ID。
    *   `enabled_groups`: 启用的群组白名单。
    *   `read_air`: 静默模式开关 (允许 Bot 决定是否不回复)。
*   **交互**:
    *   `model_frequency`: 配置主动插话的概率及触发/屏蔽关键词。
    *   `cot_prefill`: 启用 CoT 预填充 (如 `<ROSAOS>`)，提升逻辑稳定性。
*   **提示词模板**:
    *   `passive_reply_instruction`: 被动回复 (@Bot) 模板。
    *   `active_speech_instruction`: 主动插话模板。
    *   `forward_analysis_prompt`: 合并转发消息分析模板。
