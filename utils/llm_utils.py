from astrbot.api.all import *
from typing import Dict, List, Optional, Any
import time
import datetime
import threading
from .history_storage import HistoryStorage
from .message_utils import MessageUtils
from astrbot.core.provider.entites import ProviderRequest
from .persona_utils import PersonaUtils

class LLMUtils:
    """
    大模型调用工具类 (SpectreCore Pro - Dual-Layer Time Awareness)
    包含双层时间感知（群组活跃度 + 个人活跃度），支持精准的语境判断。
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
    def _calculate_time_diff_desc(seconds: float) -> str:
        """辅助函数：将秒数转换为人类可读描述"""
        if seconds < 60:
            return f"{int(seconds)}秒"
        elif seconds < 3600:
            return f"{int(seconds/60)}分钟"
        elif seconds < 86400:
            return f"{int(seconds/3600)}小时"
        else:
            return f"{int(seconds/86400)}天"

    @staticmethod
    def _get_time_prompt(history_msgs: List[AstrBotMessage], current_user_id: str, config: AstrBotConfig) -> str:
        """
        [双重回溯版] 生成时间感知提示词
        同时计算：
        1. 全局最后一条消息的时间（判断群活跃度）
        2. 当前用户最后一条消息的时间（判断用户活跃度）
        """
        try:
            if not config.get('enable_time_tracking', True):
                return ""
            
            if not history_msgs:
                return ""

            current_time = datetime.datetime.now()
            current_time_ts = current_time.timestamp()
            current_time_str = current_time.strftime("%Y-%m-%d %H:%M:%S")
            
            # 初始化状态
            last_global_msg = None  # 全局上一条（任何人）
            last_user_msg = None    # 用户上一条（指定人）
            
            # 倒序遍历，寻找两个锚点
            for i in range(len(history_msgs) - 1, -1, -1):
                msg = history_msgs[i]
                if not hasattr(msg, "timestamp") or not msg.timestamp:
                    continue
                
                # 计算时间差
                diff = current_time_ts - msg.timestamp
                
                # 过滤掉“当前正在发送”的消息（比如2秒内的）
                # 避免把自己刚刚发出的这条当成“上一条”
                if diff < 2.0:
                    # 但我们要确认这条消息是不是当前用户发的
                    # 如果是当前用户发的，且时间极短，这就是当前指令本身，跳过
                    sender_id = str(msg.sender.user_id) if (hasattr(msg, "sender") and msg.sender) else ""
                    if sender_id == str(current_user_id):
                        continue
                
                # 1. 捕捉全局锚点（只捕捉第一次遇到的，即最新的）
                if last_global_msg is None:
                    last_global_msg = msg
                
                # 2. 捕捉用户锚点
                sender_id = str(msg.sender.user_id) if (hasattr(msg, "sender") and msg.sender) else ""
                if last_user_msg is None and sender_id == str(current_user_id):
                    last_user_msg = msg
                
                # 如果两个都找到了，就可以提前结束循环
                if last_global_msg and last_user_msg:
                    break
            
            # --- 构建 Prompt ---
            prompts = [f"当前时间: {current_time_str}。"]
            
            # 分析用户活跃度 (Personal Interval)
            user_interval_desc = "这是首次发言"
            if last_user_msg:
                user_diff = current_time_ts - last_user_msg.timestamp
                user_interval_desc = f"距离该用户上次发言已过去 {LLMUtils._calculate_time_diff_desc(user_diff)}"
            prompts.append(f"[用户状态]: {user_interval_desc}。")

            # 分析群活跃度 (Global Interval)
            # 只有当全局最新消息 不是 用户自己的消息时，这个对比才有意义
            # 如果全局最新就是用户上次发的（比如群里只有他在说话），那群活跃度=用户活跃度
            if last_global_msg and last_global_msg != last_user_msg:
                global_diff = current_time_ts - last_global_msg.timestamp
                # 如果群活跃度很高（比如 < 5分钟），但用户很久没说话
                # 提示：群里很热闹，但他刚来
                global_desc = LLMUtils._calculate_time_diff_desc(global_diff)
                sender_name = last_global_msg.sender.nickname if hasattr(last_global_msg.sender, "nickname") else "其他人"
                prompts.append(f"[环境状态]: 群聊处于活跃状态，{global_desc}前 '{sender_name}' 刚发过言。")
            elif last_global_msg:
                # 此时说明上一条有效消息就是这个用户自己发的（或者长时间死群）
                prompts.append(f"[环境状态]: 此前群聊处于静默状态。")

            # 综合指导
            prompts.append("请据此调整语气：若用户久别重逢（间隔长）应寒暄；若群内热聊中他突然插入（环境活跃用户间隔长）应自然接话；若连续对话（间隔短）则保持连贯。")

            return "\n".join(prompts)
            
        except Exception as e:
            logger.error(f"时间提示词生成错误: {e}")
            return ""

    @staticmethod
    async def call_llm(event: AstrMessageEvent, config: AstrBotConfig, context: Context) -> ProviderRequest:
        platform_name = event.get_platform_name()
        is_private = event.is_private_chat()
        chat_id = event.get_group_id() if not is_private else event.get_sender_id()
        user_id = event.get_sender_id()
        bot_self_id = str(event.get_self_id())
        
        # =========================================================================
        # 1. 获取历史记录 (提前获取)
        # =========================================================================
        all_msgs = []
        try:
            all_msgs = HistoryStorage.get_history(platform_name, is_private, chat_id)
        except Exception as e:
            logger.error(f"获取历史失败: {e}")

        # =========================================================================
        # 2. 计算双层时间感知
        # =========================================================================
        # 传入 current_user_id 以区分个人活跃度
        time_prompt = LLMUtils._get_time_prompt(all_msgs, user_id, config)

        # =========================================================================
        # 3. 构建 System Prompt
        # =========================================================================
        system_parts = []
        
        # 【基础环境】
        bot_name = "Rosa"
        try:
            if platform_name == "aiocqhttp" and hasattr(event, "bot"):
                pass 
        except: pass
            
        env_info = f"你的ID: {bot_self_id}, 名字: {bot_name}。"
        if is_private:
            env_info += f"\n场景: 私聊 (对方ID: {user_id})。"
        else:
            env_info += f"\n场景: 群聊 ({chat_id})。"
        
        if time_prompt:
            env_info += f"\n{time_prompt}"

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
        # 4. 准备历史记录字符串
        # =========================================================================
        history_str = ""
        msg_limit = config.get("group_msg_history", 10)
        bot_history_keep = config.get("bot_reply_history_count", 3)
        
        if all_msgs:
            # --- 策略 A: 标准时间线 ---
            tail_msgs = all_msgs[-msg_limit:] if len(all_msgs) > msg_limit else all_msgs
            
            # --- 策略 B: Bot 回溯 ---
            recent_bot_msgs = []
            if bot_history_keep > 0:
                bot_msgs = []
                for m in all_msgs:
                    sender_id = None
                    if hasattr(m, "sender") and m.sender:
                        sender_id = str(m.sender.user_id)
                    if sender_id == bot_self_id:
                        bot_msgs.append(m)
                if bot_msgs:
                    recent_bot_msgs = bot_msgs[-bot_history_keep:]

            # --- 策略 C: 融合 ---
            seen_timestamps = set()
            merged_list = []
            
            for m in tail_msgs:
                merged_list.append(m)
                if hasattr(m, 'timestamp'): seen_timestamps.add(m.timestamp)
            
            for bm in recent_bot_msgs:
                ts = getattr(bm, 'timestamp', 0)
                if ts not in seen_timestamps:
                    merged_list.append(bm)
                    seen_timestamps.add(ts)
            
            # --- 策略 D: 排序 ---
            merged_list.sort(key=lambda x: getattr(x, 'timestamp', 0))
            
            # --- 策略 E: 格式化 ---
            fmt = await MessageUtils.format_history_for_llm(merged_list, max_messages=999)
            if fmt:
                history_str = "以下是最近的聊天记录：\n" + fmt
        else:
            history_str = "（暂无历史记录）"

        setattr(event, "_spectre_history", history_str)

        # 5. User Prompt
        current_msg = event.get_message_outline() or "[非文本消息]"

        # 6. 图片处理
        image_urls = []
        img_check_count = config.get("image_processing", {}).get("image_count", 0)
        
        if img_check_count > 0 and all_msgs:
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
                final_system_prompt += f"\n\n[System]: 上下文中包含了最近的 {len(image_urls)} 张图片供参考。"

        # 7. Request
        func_tools_mgr = context.get_llm_tool_manager() if config.get("use_func_tool", False) else None

        return event.request_llm(
            prompt=current_msg, 
            func_tool_manager=func_tools_mgr,
            contexts=contexts,
            system_prompt=final_system_prompt, 
            image_urls=image_urls, 
        )
