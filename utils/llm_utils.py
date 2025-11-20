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
    大模型调用工具类 (Rosa Edition)
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
        platform_name = event.get_platform_name()
        is_private = event.is_private_chat()
        chat_id = event.get_group_id() if not is_private else event.get_sender_id()
        
        # 1. 构建 System Prompt
        system_parts = []
        
        # 【修改点】基础环境名称设为 Rosa
        bot_name = "Rosa"
        if platform_name == "aiocqhttp" and hasattr(event, "bot"):
            try:
                # 即使平台返回其他名字，也优先使用 Rosa，或者保留平台昵称？
                # 既然用户强制要求叫 Rosa，这里就强制使用 Rosa
                # bot_name = (await event.bot.api.get_login_info())["nickname"]
                pass 
            except: pass
            
        env_info = f"你的ID: {event.get_self_id()}, 名字: {bot_name}。"
        if is_private:
            env_info += f"\n场景: 私聊 (对方ID: {event.get_sender_id()})。"
        else:
            env_info += f"\n场景: 群聊 ({chat_id})。"
        system_parts.append(env_info)

        # 人设
        persona_name = config.get("persona", "")
        contexts = []
        if persona_name:
            try:
                p = PersonaUtils.get_persona_by_name(context, persona_name)
                if p:
                    system_parts.append(p.get('prompt', ''))
                    if p.get('_begin_dialogs_processed'):
                        contexts.extend(p.get('_begin_dialogs_processed', []))
            except Exception as e:
                logger.error(f"加载人设失败: {e}")

        # 【修改点】功能指令中的名字设为 Rosa
        instruction = "\n\n【规则】\n1. 你的名字在聊天记录中显示为 'Rosa'。\n2. 请勿重复自己的名字作为回复开头。"
        if config.get("read_air", False):
            instruction += "\n3. 若无需回复（如话题与你无关），请严格输出 <NO_RESPONSE>。"
        else:
            instruction += "\n3. 请直接生成回复。"
        system_parts.append(instruction)

        final_system_prompt = "\n\n".join(system_parts)

        # 2. 准备历史记录
        history_str = ""
        try:
            limit = config.get("group_msg_history", 10)
            msgs = HistoryStorage.get_history(platform_name, is_private, chat_id)
            if msgs:
                fmt = await MessageUtils.format_history_for_llm(msgs, max_messages=limit)
                if fmt:
                    history_str = "以下是最近的聊天记录：\n" + fmt
            else:
                history_str = "（暂无历史记录）"
        except Exception as e:
            logger.error(f"获取历史失败: {e}")

        # 挂载到 Event
        setattr(event, "_spectre_history", history_str)

        # 3. 准备 User Prompt
        current_msg = event.get_message_outline() or "[非文本消息]"

        # 4. 图片处理
        image_urls = []
        if config.get("image_processing", {}).get("image_count", 0) > 0 and msgs:
            msgs_to_check = msgs[-limit:] if len(msgs) > limit else msgs
            for msg in reversed(msgs_to_check):
                if hasattr(msg, "message") and msg.message:
                    for comp in msg.message:
                        if isinstance(comp, Image) and comp.file:
                            image_urls.append(comp.file)
                            if len(image_urls) >= config.get("image_processing", {}).get("image_count"): break
                if len(image_urls) >= config.get("image_processing", {}).get("image_count"): break
            
            if image_urls:
                final_system_prompt += f"\n\n[System]: 上下文中包含了最近的 {len(image_urls)} 张图片供参考。"

        # 5. 发起请求
        func_tools_mgr = context.get_llm_tool_manager() if config.get("use_func_tool", False) else None

        return event.request_llm(
            prompt=current_msg, 
            func_tool_manager=func_tools_mgr,
            contexts=contexts,
            system_prompt=final_system_prompt, 
            image_urls=image_urls, 
        )
