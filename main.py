from astrbot.api.all import *
from astrbot.api.event import filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.message_components import At, Reply
from .utils import *
import time

@register(
    "spectrecorepro",
    "ReedSein",
    "SpectreCore Pro: 融合了上下文增强的主动回复插件",
    "2.1.9",
    "https://github.com/ReedSein/astrbot_plugin_SpectreCorePro"
)
class SpectreCore(Star):
    
    # 默认模板配置
    DEFAULT_PASSIVE_INSTRUCTION = '现在，群成员 {sender_name} (ID: {sender_id}) 正在对你说话，TA说："{original_prompt}"'
    DEFAULT_ACTIVE_INSTRUCTION = '以上是最近的聊天记录。你决定主动参与讨论，并想就以下内容发表你的看法："{original_prompt}"'

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        HistoryStorage.init(config)
        ImageCaptionUtils.init(context, config)

    @event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        try:
            async for result in self._process_message(event):
                yield result
        except Exception as e:
            logger.error(f"处理群消息错误: {e}")

    @event_message_type(EventMessageType.PRIVATE_MESSAGE)
    async def on_private_message(self, event: AstrMessageEvent):
        try:
            async for result in self._process_message(event):
                yield result
        except Exception as e:
            logger.error(f"处理私聊消息错误: {e}")
            
    async def _process_message(self, event: AstrMessageEvent):
        message_outline = event.get_message_outline()
        if not message_outline or message_outline.strip() == "":
            return

        await HistoryStorage.process_and_save_user_message(event)

        if ReplyDecision.should_reply(event, self.config):
            async for result in ReplyDecision.process_and_reply(event, self.config, self.context):
                yield result

    # -------------------------------------------------------------------------
    # 核心逻辑：Prompt 组装
    # -------------------------------------------------------------------------

    def _is_explicit_trigger(self, event: AstrMessageEvent) -> bool:
        """判断是否为显式触发（被动回复）"""
        if event.message_obj.type == EventMessageType.PRIVATE_MESSAGE:
            return True
        bot_self_id = event.get_self_id()
        if not bot_self_id: return False
        
        for comp in event.message_obj.message:
            if isinstance(comp, At) and (str(comp.qq) == str(bot_self_id) or comp.qq == "all"):
                return True
            elif isinstance(comp, Reply):
                return True
        return False

    def _format_instruction(self, template: str, event: AstrMessageEvent, original_prompt: str) -> str:
        sender_name = event.get_sender_name() or "用户"
        sender_id = event.get_sender_id() or "unknown"
        
        instruction = template.replace("{sender_name}", str(sender_name))
        instruction = instruction.replace("{sender_id}", str(sender_id))
        instruction = instruction.replace("{original_prompt}", str(original_prompt))
        return instruction

    @filter.on_llm_request(priority=90)
    async def on_llm_request_custom(self, event: AstrMessageEvent, req: ProviderRequest):
        """
        【核心 Hook】
        组装顺序：
        1. 获取 LLMUtils 准备好的历史记录 (req.prompt 此时只有当前消息)
        2. 根据主动/被动选择模板
        3. 将当前消息套入模板
        4. 最终 User Prompt = 历史记录 + "\n\n" + 格式化后的指令
        """
        try:
            # 1. 获取原始 Prompt (当前消息)
            current_msg = req.prompt or ""
            
            # 2. 获取挂载的历史记录 (由 LLMUtils 准备)
            history_str = getattr(event, "_spectre_history", "")
            
            # 3. 判断场景并选择模板
            if self._is_explicit_trigger(event):
                template = self.config.get("passive_reply_instruction", self.DEFAULT_PASSIVE_INSTRUCTION)
            else:
                template = self.config.get("active_speech_instruction", self.DEFAULT_ACTIVE_INSTRUCTION)

            # 4. 格式化当前指令
            instruction = self._format_instruction(template, event, current_msg)
            
            # 5. 最终组装：历史在前，指令在后
            if history_str:
                final_prompt = f"{history_str}\n\n{instruction}"
            else:
                final_prompt = instruction
                
            # 6. 应用
            req.prompt = final_prompt
            
            # 清理挂载属性（可选）
            if hasattr(event, "_spectre_history"):
                delattr(event, "_spectre_history")
                
            # logger.debug(f"[SpectreCore Pro] Prompt组装完成，长度: {len(final_prompt)}")

        except Exception as e:
            logger.error(f"[SpectreCore Pro] Prompt 组装失败: {e}")

    # -------------------------------------------------------------------------
    # 辅助功能 (Bot消息记录、大模型后处理等)
    # -------------------------------------------------------------------------

    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent):
        try:           
            if event._result and hasattr(event._result, "chain"):
                message_text = "".join([i.text for i in event._result.chain if hasattr(i, "text")])
                if "已成功重置" in message_text: return
                await HistoryStorage.save_bot_message_from_chain(event._result.chain, event)
        except Exception as e:
            logger.error(f"保存Bot消息错误: {e}")

    from astrbot.api.provider import LLMResponse
    @filter.on_llm_response(priority=114514)
    async def on_llm_resp(self, event: AstrMessageEvent, resp: LLMResponse):
        try:
            if resp.role != "assistant": return
            resp.completion_text = TextFilter.process_model_text(resp.completion_text, self.config)
        except Exception as e:
            logger.error(f"处理大模型回复错误: {e}")

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        try:
            result = event.get_result()
            if result and result.is_llm_result():
                msg = "".join([comp.text for comp in result.chain if hasattr(comp, 'text')])
                if "<NO_RESPONSE>" in msg:
                    event.clear_result()
                    logger.debug("触发 NO_RESPONSE，阻止发送")
        except Exception as e:
            logger.error(f"Decorating result error: {e}")

    @filter.command_group("spectrecore", alias={'sc'})
    def spectrecore(self): pass

    @spectrecore.command("help")
    async def help(self, event: AstrMessageEvent):
        yield event.plain_result("SpectreCore Pro: 请查看 GitHub 文档或使用 /sc reset 重置历史。")
        
    @filter.permission_type(filter.PermissionType.ADMIN)
    @spectrecore.command("reset")
    async def reset(self, event: AstrMessageEvent, group_id: str = None):
        # 简化的 reset 逻辑
        try:
            platform = event.get_platform_name()
            if group_id:
                is_priv, target_id = False, group_id
            else:
                is_priv = event.is_private_chat()
                target_id = event.get_group_id() if not is_priv else event.get_sender_id()
            
            if HistoryStorage.clear_history(platform, is_priv, target_id):
                yield event.plain_result("历史记录已重置。")
            else:
                yield event.plain_result("重置失败或无记录。")
        except Exception as e:
            yield event.plain_result(f"错误: {e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @spectrecore.command("callllm")
    async def callllm(self, event: AstrMessageEvent):
        yield await LLMUtils.call_llm(event, self.config, self.context)