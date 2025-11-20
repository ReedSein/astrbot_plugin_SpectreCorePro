from astrbot.api.all import *
from typing import Dict, List, Optional, Any
import time
import threading
from .history_storage import HistoryStorage
from .message_utils import MessageUtils
from astrbot.core.provider.entites import ProviderRequest
from .persona_utils import PersonaUtils

class LLMUtils:
    """
    大模型调用工具类 (SpectreCore Pro Refactored)
    负责构建 System Prompt 和准备 History 数据，将组装权交给 main.py 的 Hook。
    """
    
    _llm_call_status: Dict[str, Dict[str, Any]] = {}
    _lock = threading.Lock()
    
    @staticmethod
    def get_chat_key(platform_name: str, is_private_chat: bool, chat_id: str) -> str:
        chat_type = "private" if is_private_chat else "group"
        return f"{platform_name}_{chat_type}_{chat_id}"
    
    @staticmethod
    def set_llm_in_progress(platform_name: str, is_private_chat: bool, chat_id: str, in_progress: bool = True) -> None:
        chat_key = LLMUtils.get_chat_key(platform_name, is_private_chat, chat_id)
        with LLMUtils._lock:
            if chat_key not in LLMUtils._llm_call_status:
                LLMUtils._llm_call_status[chat_key] = {}
            LLMUtils._llm_call_status[chat_key]["in_progress"] = in_progress
            LLMUtils._llm_call_status[chat_key]["last_call_time"] = time.time()
    
    @staticmethod
    def is_llm_in_progress(platform_name: str, is_private_chat: bool, chat_id: str) -> bool:
        chat_key = LLMUtils.get_chat_key(platform_name, is_private_chat, chat_id)
        with LLMUtils._lock:
            if chat_key not in LLMUtils._llm_call_status:
                return False
            return LLMUtils._llm_call_status[chat_key].get("in_progress", False)

    @staticmethod
    async def call_llm(event: AstrMessageEvent, config: AstrBotConfig, context: Context) -> ProviderRequest:
        """
        构建调用请求。
        注意：此方法不再直接拼接 User Prompt 中的历史记录，而是将其挂载到 event._spectre_history 上。
        """
        platform_name = event.get_platform_name()
        is_private = event.is_private_chat()
        chat_id = event.get_group_id() if not is_private else event.get_sender_id()
        
        # ================= 1. 构建 System Prompt (人设 + 环境) =================
        system_parts = []
        
        # 1.1 基础环境
        if platform_name == "aiocqhttp" and hasattr(event, "bot"):
            try:
                bot_name = (await event.bot.api.get_login_info())["nickname"]
            except:
                bot_name = "AstrBot"
        else:
            bot_name = "AstrBot"
            
        base_env = f"你正在浏览聊天软件。你的ID是{event.get_self_id()}，用户名是{bot_name}。"
        
        if is_private:
            sender_name = event.get_sender_name() or str(event.get_sender_id())
            base_env += f"\n当前场景：与 {sender_name} 的私聊。"
        else:
            base_env += f"\n当前场景：群聊 ({chat_id})。"
        
        system_parts.append(base_env)

        # 1.2 加载 Persona (人设)
        persona_name = config.get("persona", "")
        contexts = [] 
        
        if persona_name:
            try:
                persona = PersonaUtils.get_persona_by_name(context, persona_name)
                if persona:
                    p_prompt = persona.get('prompt', '')
                    # 模仿风格
                    if persona.get('_mood_imitation_dialogs_processed'):
                        p_prompt += "\n请模仿以下对话风格(a=用户, b=你):\n" + persona.get('_mood_imitation_dialogs_processed', '')
                    
                    # 开场白
                    if persona.get('_begin_dialogs_processed'):
                        contexts.extend(persona.get('_begin_dialogs_processed', []))
                    
                    system_parts.append(p_prompt)
            except Exception as e:
                logger.error(f"获取人格失败: {e}")

        # 1.3 功能性指令 (放在 System Prompt 末尾)
        instruction_part = "\n\n【回复规则】\n1. 在聊天记录中，你的名字被显示为 'AstrBot'。\n2. 不要重复自己的名字作为回复开头。"
        
        if config.get("read_air", False):
            instruction_part += "\n3. 决策逻辑：如果你觉得不需要回复（例如大家在闲聊与你无关的话题），请严格只输出 <NO_RESPONSE>。如果决定回复，直接输出内容。"
        else:
            instruction_part += "\n3. 请直接生成回复内容。"
            
        system_parts.append(instruction_part)
        
        final_system_prompt = "\n\n".join(system_parts)

        # ================= 2. 准备历史记录 (挂载到 Event) =================
        history_str = ""
        try:
            history_limit = config.get("group_msg_history", 10)
            history_messages = HistoryStorage.get_history(platform_name, is_private, chat_id)
            
            if history_messages:
                formatted_history = await MessageUtils.format_history_for_llm(history_messages, max_messages=history_limit)
                if formatted_history:
                    history_str = "以下是最近的聊天记录：\n" + formatted_history
            else:
                history_str = "（暂无最近聊天记录）"
        except Exception as e:
            logger.error(f"格式化历史记录失败: {e}")
            history_str = "（获取聊天记录出错）"

        # 【关键】将历史记录挂载到 event 对象上，供 main.py 的 Hook 使用
        setattr(event, "_spectre_history", history_str)

        # ================= 3. 准备当前 User Prompt =================
        # 这里只放当前消息，模板套用交给 main.py
        current_msg_outline = event.get_message_outline() or "[非文本消息]"

        # ================= 4. 图片处理 =================
        image_urls = []
        if image_count := config.get("image_processing", {}).get("image_count", 0):
            if history_messages:
                # 从历史记录中提取图片
                msgs = history_messages[-history_limit:] if len(history_messages) > history_limit else history_messages
                for msg in reversed(msgs):
                    if hasattr(msg, "message") and msg.message:
                        for comp in msg.message:
                            if isinstance(comp, Image) and comp.file:
                                image_urls.append(comp.file)
                                if len(image_urls) >= image_count: break
                    if len(image_urls) >= image_count: break
                
                if image_urls:
                    # 既然图片是历史记录里的，我们在 System Prompt 里提一句
                    final_system_prompt += f"\n\n[System]: 上下文中包含了最近的 {len(image_urls)} 张图片供参考。"

        # ================= 5. 发起请求 =================
        func_tools_mgr = context.get_llm_tool_manager() if config.get("use_func_tool", False) else None

        return event.request_llm(
            prompt=current_msg_outline, 
            func_tool_manager=func_tools_mgr,
            contexts=contexts,
            system_prompt=final_system_prompt, 
            image_urls=image_urls, 
        )