from astrbot.api.all import *
from astrbot.api.message_components import At
from typing import Dict, Any, Optional
import random
import time
from .llm_utils import LLMUtils

class ReplyDecision:
    """
    消息回复决策工具类 (SpectreCore Pro Fixed)
    修复群号类型匹配问题，增强 @ 检测，增加详细 Debug 日志。
    """
    
    @staticmethod
    def should_reply(event: AstrMessageEvent, config: AstrBotConfig) -> bool:
        try:
            # 1. 获取基本信息
            platform_name = event.get_platform_name()
            is_private = event.is_private_chat()
            chat_id = event.get_sender_id() if is_private else event.get_group_id()
            
            # 2. 检查锁 (防止并发)
            if LLMUtils.is_llm_in_progress(platform_name, is_private, chat_id):
                return False
            
            # 3. 检查静默 (Mute)
            mute_info = config.get("_temp_mute", {})
            if mute_info and mute_info.get("until", 0) > time.time():
                return False

            # 4. 检查启用状态 (类型安全转换)
            if not ReplyDecision._is_chat_enabled(event, config):
                return False

            # 5. 检查黑名单
            blacklist = config.get("model_frequency", {}).get("blacklist_keywords", [])
            if blacklist and ReplyDecision._check_keywords(event, blacklist):
                logger.debug("[SpectreCore] 过滤：包含黑名单关键词")
                return False

            # ===========================================
            # 6. @检测 (最高优先级 - 强制回复)
            # ===========================================
            if ReplyDecision._is_at_me(event):
                logger.info(f"[SpectreCore] 触发：检测到被 @ (ChatID: {chat_id})")
                return True

            # 7. 关键词检测
            freq_config = config.get("model_frequency", {})
            keywords = freq_config.get("keywords", [])
            if keywords and ReplyDecision._check_keywords(event, keywords):
                logger.info(f"[SpectreCore] 触发：包含触发关键词")
                return True
            
            # 8. 概率回复 (读空气)
            if is_private:
                return True
                
            method = freq_config.get("method", "概率回复")
            if method == "概率回复":
                probability = freq_config.get("probability", {}).get("probability", 0.0)
                if random.random() < probability:
                    logger.info(f"[SpectreCore] 触发：概率命中 ({probability})")
                    return True
            
            return False

        except Exception as e:
            logger.error(f"[SpectreCore] 决策逻辑发生错误: {e}")
            return False

    @staticmethod
    def _is_chat_enabled(event: AstrMessageEvent, config: AstrBotConfig) -> bool:
        """检查当前会话是否开启 (类型安全版)"""
        if event.is_private_chat():
            return config.get("enabled_private", False)
        
        # 群聊检查：将所有 ID 转为字符串比对
        group_id = str(event.get_group_id())
        enabled_groups = [str(g) for g in config.get("enabled_groups", [])]
        
        return group_id in enabled_groups

    @staticmethod
    def _is_at_me(event: AstrMessageEvent) -> bool:
        """检查是否被 @"""
        try:
            if event.is_private_chat():
                return True

            bot_self_id = str(event.get_self_id() or "")
            
            # 1. 检查消息组件
            if hasattr(event.message_obj, 'message'):
                for comp in event.message_obj.message:
                    if isinstance(comp, At):
                        if str(comp.qq) == bot_self_id or comp.qq == "all":
                            return True
            
            # 2. 文本模糊匹配 (兜底)
            msg_text = event.get_message_outline() or ""
            if f"@{bot_self_id}" in msg_text:
                return True
                
            return False
        except Exception:
            return False

    @staticmethod
    def _check_keywords(event: AstrMessageEvent, keywords: list) -> bool:
        msg_text = event.get_message_outline() or ""
        for kw in keywords:
            if kw in msg_text:
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
