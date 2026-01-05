from astrbot.api.all import *
from typing import List, Dict, Any
import os
import time
from datetime import datetime
from .image_caption import ImageCaptionUtils
import asyncio
import json
import traceback

class MessageUtils:
    """
    消息处理工具类
    """
        
    @staticmethod
    async def format_history_for_llm(
        history_messages: List[AstrBotMessage],
        max_messages: int = 20,
        image_caption: bool = True,
        platform_name: str = "",
        is_private: bool = False,
        chat_id: str = "",
    ) -> str:
        if not history_messages:
            return ""
        
        if len(history_messages) > max_messages:
            history_messages = history_messages[-max_messages:]
        
        formatted_text = ""
        divider = "\n" + "-" + "\n"
        
        for idx, msg in enumerate(history_messages):
            sender_name = "未知用户"
            sender_id = "unknown"
            if hasattr(msg, "sender") and msg.sender:
                sender_name = msg.sender.nickname or "未知用户"
                sender_id = msg.sender.user_id or "unknown"
            
            # 【修改点】如果旧历史记录中名字是 AstrBot，强制显示为 Rosa
            if sender_name == "AstrBot":
                sender_name = "Rosa"
            
            send_time = "未知时间"
            if hasattr(msg, "timestamp") and msg.timestamp:
                try:
                    time_obj = datetime.fromtimestamp(msg.timestamp)
                    send_time = time_obj.strftime("%Y-%m-%d %H:%M:%S")
                except: pass
            
            message_content = await MessageUtils.outline_message_list(
                msg.message,
                counter={"i": 0},
                image_caption=image_caption,
                platform_name=platform_name,
                is_private=is_private,
                chat_id=chat_id,
            ) if hasattr(msg, "message") and msg.message else ""
            
            message_text = f"发送者: {sender_name} (ID: {sender_id})\n"
            message_text += f"时间: {send_time}\n"
            message_text += f"内容: {message_content}"
            
            formatted_text += message_text
            
            if idx < len(history_messages) - 1:
                formatted_text += divider
        
        return formatted_text
           
    @staticmethod
    async def outline_message_list(
        message_list: List[BaseMessageComponent],
        counter: Dict[str, int] | None = None,
        image_caption: bool = True,
        platform_name: str = "",
        is_private: bool = False,
        chat_id: str = "",
    ) -> str:
        outline = ""
        idx_ref = counter or {"i": 0}
        for i in message_list:
            try:
                component_type = getattr(i, 'type', None)
                if not component_type:
                    component_type = i.__class__.__name__.lower()
                
                if component_type == "reply" or isinstance(i, Reply):
                    outline += await MessageUtils._format_reply_component(i)
                    continue
                elif component_type == "plain" or isinstance(i, Plain):
                    outline += i.text
                elif component_type == "image" or isinstance(i, Image):
                    try:
                        image = i.file if i.file else i.url
                        idx_ref["i"] += 1
                        tag = f"[图片{idx_ref['i']}"
                        if image:
                            if image_caption:
                                if isinstance(image, str) and image.startswith("file:///"):
                                    image_path = image[8:]
                                    if not os.path.exists(image_path):
                                        outline += f"{tag}: 文件过期]"
                                        continue
                                    image = image_path
                                # 优先命中缓存，未命中则调度后台转述
                                caption = ImageCaptionUtils.get_cached_caption(
                                    image, platform_name, is_private, chat_id
                                ) or ImageCaptionUtils.caption_cache.get(image)
                                if caption:
                                    outline += f"{tag}: {caption}]"
                                else:
                                    outline += f"{tag}]"
                                    ImageCaptionUtils.schedule_caption(
                                        image, platform_name, is_private, chat_id
                                    )
                            else:
                                outline += f"{tag} 已上传]"
                        else:
                            outline += f"{tag}]"
                    except Exception:
                        outline += "[图片]"
                elif component_type == "face" or isinstance(i, Face):
                    outline += f"[表情:{getattr(i, 'id', '')}]"
                elif component_type == "at" or isinstance(i, At):
                    qq = getattr(i, 'qq', '')
                    name = getattr(i, 'name', '')
                    if str(qq).lower() == "all": outline += "@全体成员"
                    elif name: outline += f"@{name}({qq})"
                    else: outline += f"@{qq}"
                else:
                    outline += f"[{component_type}]"
                    
            except Exception:
                outline += f"[未知消息]"
                continue
        return outline

    @staticmethod
    async def _format_reply_component(reply_component: Reply) -> str:
        try:
            sender_id = getattr(reply_component, 'sender_id', '')
            sender_nickname = getattr(reply_component, 'sender_nickname', '')
            
            sender_info = f"{sender_nickname}({sender_id})" if sender_nickname else f"{sender_id}" or "未知用户"
            
            reply_content = ""
            if hasattr(reply_component, 'chain') and reply_component.chain:
                reply_content = await MessageUtils.outline_message_list(
                    reply_component.chain,
                    counter={"i": 0},
                    image_caption=True,
                    platform_name="",
                    is_private=False,
                    chat_id="",
                )
            elif hasattr(reply_component, 'message_str') and reply_component.message_str:
                reply_content = reply_component.message_str
            elif hasattr(reply_component, 'text') and reply_component.text:
                reply_content = reply_component.text
            else:
                reply_content = "[内容不可用]"
            
            if len(reply_content) > 150: reply_content = reply_content[:150] + "..."
            return f"「↪ 引用消息 {sender_info}：{reply_content}」"
        except: return "[回复消息]"
