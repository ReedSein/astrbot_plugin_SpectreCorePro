from astrbot.api.all import *
from astrbot.api.message_components import At
from typing import Dict, Any, Optional
import random
import time
from .llm_utils import LLMUtils

class ReplyDecision:
    """
    消息回复决策工具类 (SpectreCore Pro Fix)
    修复了被 @ 时不回复的问题，给予 @ 最高优先级。
    """
    
    @staticmethod
    def should_reply(event: AstrMessageEvent, config: AstrBotConfig) -> bool:
        """
        判断是否应该回复消息
        """
        try:
            # 获取必要信息
            platform_name = event.get_platform_name()
            is_private_chat = event.is_private_chat()
            chat_id = event.get_sender_id() if is_private_chat else event.get_group_id()
            
            # 1. 检查是否已有大模型在处理 (防止并发轰炸)
            if LLMUtils.is_llm_in_progress(platform_name, is_private_chat, chat_id):
                logger.debug(f"当前聊天已有大模型处理中，不进行回复")
                return False
            
            # 2. 检查是否处于临时静默状态 (mute 指令)
            mute_info = config.get("_temp_mute", {})
            if mute_info and mute_info.get("until", 0) > time.time():
                logger.debug(f"当前处于临时静默状态，不进行回复")
                return False
            
            # 3. 检查消息是否包含黑名单关键词 (优先级：黑名单 > @)
            # 如果你希望 @ 能突破黑名单，可以把这段移到下面
            blacklist_keywords = config.get("model_frequency", {}).get("blacklist_keywords", [])
            if blacklist_keywords and ReplyDecision._check_blacklist_keywords(event, blacklist_keywords):
                logger.debug("消息中包含黑名单关键词，不进行回复")
                return False

            # =========================================================
            # 【核心修复】 4. 检查是否被 @ (优先级：最高)
            # 只要被 @，无视后续的概率设置，强制回复
            # =========================================================
            if ReplyDecision._is_at_me(event):
                logger.info(f"[SpectreCore] 检测到被 @，强制触发回复。")
                return True
            
            # 5. 检查常规配置规则 (概率、关键词等)
            return ReplyDecision._check_reply_rules(event, config)

        except Exception as e:
            logger.error(f"判断是否回复时发生错误: {e}")
            return False
    
    @staticmethod
    def _is_at_me(event: AstrMessageEvent) -> bool:
        """
        检查消息是否 @ 了机器人
        """
        try:
            # 私聊默认视为被 @
            if event.is_private_chat():
                return True
                
            bot_self_id = event.get_self_id()
            if not bot_self_id:
                return False
            
            # 遍历消息组件检查 At
            if hasattr(event.message_obj, 'message'):
                for comp in event.message_obj.message:
                    if isinstance(comp, At):
                        # 兼容字符串和数字类型的 ID 比较
                        if str(comp.qq) == str(bot_self_id) or comp.qq == "all":
                            return True
            return False
        except Exception:
            return False

    @staticmethod
    def _check_reply_rules(event: AstrMessageEvent, config: AstrBotConfig) -> bool:
        """
        检查常规回复规则 (概率、关键词)
        """
        # 检查是否是开启回复的群聊/私聊
        if event.is_private_chat():
            if not config.get("enabled_private", False):
                logger.debug("未开启私聊回复功能")
                return False
        else:
            if event.get_group_id() not in config.get("enabled_groups", []):
                logger.debug(f"群聊{event.get_group_id()}未开启回复功能")
                return False
            
        # 获取消息频率配置
        frequency_config = config.get("model_frequency", {})
        
        # 检查关键词触发
        keywords = frequency_config.get("keywords", [])
        if keywords and ReplyDecision._check_keywords(event, keywords):
            logger.debug("消息中包含关键词，触发回复")
            return True
        
        # 获取回复方法
        method = frequency_config.get("method", "概率回复")
        
        # 根据不同方法判断
        if method == "概率回复":
            prob_config = frequency_config.get("probability", {})
            
            # 私聊总是回复 (但在 _is_at_me 中已经处理过，这里作为兜底)
            if event.is_private_chat():
                return True
            else:
                probability = prob_config.get("probability", 0.1)
            
            # 使用概率计算是否回复
            should_reply = random.random() < probability
            if should_reply:
                logger.debug(f"概率触发回复，当前概率: {probability}")
            # else:
            #     logger.debug(f"概率回复未触发，当前概率: {probability}")
            return should_reply
        
        return False
    
    @staticmethod
    def _check_keywords(event: AstrMessageEvent, keywords: list) -> bool:
        message_text = event.get_message_outline()
        for keyword in keywords:
            if keyword in message_text:
                return True
        return False
        
    @staticmethod
    def _check_blacklist_keywords(event: AstrMessageEvent, blacklist_keywords: list) -> bool:
        message_text = event.get_message_outline()
        for keyword in blacklist_keywords:
            if keyword in message_text:
                return True
        return False

    @staticmethod
    async def process_and_reply(event: AstrMessageEvent, config: AstrBotConfig, context: Context):
        platform_name = event.get_platform_name()
        is_private = event.is_private_chat()
        chat_id = event.get_sender_id() if is_private else event.get_group_id()

        LLMUtils.set_llm_in_progress(platform_name, is_private, chat_id)

        try:
            yield await LLMUtils.call_llm(event, config, context)
        finally:
            LLMUtils.set_llm_in_progress(platform_name, is_private, chat_id, False)
