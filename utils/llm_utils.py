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
    大模型调用工具类 (SpectreCore Pro - Context Enhanced Edition)
    包含上下文增强逻辑，确保 Bot 自身的历史回复不丢失。
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
        bot_self_id = str(event.get_self_id())
        
        # 1. 构建 System Prompt
        system_parts = []
        
        # 【基础环境】
        bot_name = "Rosa"
        if platform_name == "aiocqhttp" and hasattr(event, "bot"):
            try:
                # 尝试获取 API 返回的昵称，失败则默认 Rosa
                # bot_name = (await event.bot.api.get_login_info())["nickname"]
                pass 
            except: pass
            
        env_info = f"你的ID: {bot_self_id}, 名字: {bot_name}。"
        if is_private:
            env_info += f"\n场景: 私聊 (对方ID: {event.get_sender_id()})。"
        else:
            env_info += f"\n场景: 群聊 ({chat_id})。"
        system_parts.append(env_info)

        # 【人设加载】
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

        # 【功能指令】
        instruction = "\n\n【规则】\n1. 你的名字在聊天记录中显示为 'Rosa'。\n2. 请勿重复自己的名字作为回复开头。"
        if config.get("read_air", False):
            instruction += "\n3. 若无需回复（如话题与你无关），请严格输出 <NO_RESPONSE>。"
        else:
            instruction += "\n3. 请直接生成回复。"
        system_parts.append(instruction)

        final_system_prompt = "\n\n".join(system_parts)

        # =========================================================================
        # 2. 准备历史记录 (智能上下文融合逻辑)
        # =========================================================================
        history_str = ""
        try:
            # 基础配置：要获取的历史总条数
            msg_limit = config.get("group_msg_history", 10)
            # 增强配置：强制保留的 Bot 最近发言条数 (默认为 3)
            bot_history_keep = config.get("bot_reply_history_count", 3)
            
            # 获取存储的所有历史记录 (通常是最近的200条)
            all_msgs = HistoryStorage.get_history(platform_name, is_private, chat_id)
            
            if all_msgs:
                # --- 策略 A: 标准时间线窗口 ---
                # 获取最后 N 条消息
                tail_msgs = all_msgs[-msg_limit:] if len(all_msgs) > msg_limit else all_msgs
                
                # --- 策略 B: Bot 历史回溯 ---
                # 即使被刷屏，也要强制捞回 Bot 最近说过的 M 条话
                recent_bot_msgs = []
                if bot_history_keep > 0:
                    bot_msgs = []
                    for m in all_msgs:
                        sender_id = None
                        if hasattr(m, "sender") and m.sender:
                            sender_id = str(m.sender.user_id)
                        
                        # 判断消息发送者是否为 Bot
                        if sender_id == bot_self_id:
                            bot_msgs.append(m)
                    
                    # 取最后 M 条
                    if bot_msgs:
                        recent_bot_msgs = bot_msgs[-bot_history_keep:]

                # --- 策略 C: 融合与去重 ---
                # 将两者合并，并通过 timestamp 去重
                seen_timestamps = set()
                merged_list = []
                
                # 先加入标准尾部消息
                for m in tail_msgs:
                    merged_list.append(m)
                    if hasattr(m, 'timestamp'): 
                        seen_timestamps.add(m.timestamp)
                
                # 再补入 Bot 历史 (如果不在尾部消息中)
                for bm in recent_bot_msgs:
                    ts = getattr(bm, 'timestamp', 0)
                    if ts not in seen_timestamps:
                        merged_list.append(bm)
                        seen_timestamps.add(ts)
                
                # --- 策略 D: 重新排序 ---
                # 必须按时间重新排序，否则对话顺序会乱
                merged_list.sort(key=lambda x: getattr(x, 'timestamp', 0))
                
                # --- 策略 E: 格式化 ---
                # 传入 max_messages=999，因为我们已经在上面手动控制了数量
                fmt = await MessageUtils.format_history_for_llm(merged_list, max_messages=999)
                if fmt:
                    history_str = "以下是最近的聊天记录：\n" + fmt
            else:
                history_str = "（暂无历史记录）"
        except Exception as e:
            logger.error(f"获取历史失败: {e}")

        # 挂载历史记录到 Event，供 Prompt 组装使用
        setattr(event, "_spectre_history", history_str)

        # 3. 准备 User Prompt
        current_msg = event.get_message_outline() or "[非文本消息]"

        # 4. 图片处理
        # 逻辑：检查最近的一批消息里是否有图片
        image_urls = []
        img_check_count = config.get("image_processing", {}).get("image_count", 0)
        
        if img_check_count > 0 and all_msgs:
            # 只回溯最近的 15 条消息查找图片，避免读取太久远的图
            check_range = 15 
            msgs_to_check = all_msgs[-check_range:] if len(all_msgs) > check_range else all_msgs
            
            for msg in reversed(msgs_to_check):
                if hasattr(msg, "message") and msg.message:
                    for comp in msg.message:
                        if isinstance(comp, Image) and comp.file:
                            image_urls.append(comp.file)
                            if len(image_urls) >= img_check_count: break
                if len(image_urls) >= img_check_count: break
            
            if image_urls:
                # 这里的 [System] 提示会追加到 System Prompt 末尾
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

