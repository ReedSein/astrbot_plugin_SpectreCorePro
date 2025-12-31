import os
import jsonpickle
from typing import List
from astrbot.api.all import *
import time
import traceback

class HistoryStorage:
    """
    历史消息存储工具类
    """
    
    config = None
    base_storage_path = None
    
    @staticmethod
    def init(config: AstrBotConfig):
        HistoryStorage.config = config
        HistoryStorage.base_storage_path = os.path.join(os.getcwd(), "data", "chat_history")
        HistoryStorage._ensure_dir(HistoryStorage.base_storage_path)
        logger.info(f"消息存储路径初始化: {HistoryStorage.base_storage_path}")
        jsonpickle.set_encoder_options('json', ensure_ascii=False, indent=2)
        jsonpickle.set_preferred_backend('json')
    
    @staticmethod
    def _ensure_dir(directory: str) -> None:
        if not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
    
    @staticmethod
    def _get_storage_path(platform_name: str, is_private_chat: bool, chat_id: str) -> str:
        if not HistoryStorage.base_storage_path:
            HistoryStorage.base_storage_path = os.path.join(os.getcwd(), "data", "chat_history")
            HistoryStorage._ensure_dir(HistoryStorage.base_storage_path)
            
        chat_type = "private" if is_private_chat else "group"
        directory = os.path.join(HistoryStorage.base_storage_path, platform_name, chat_type)
        HistoryStorage._ensure_dir(directory)
        return os.path.join(directory, f"{chat_id}.json")
    
    @staticmethod
    def _sanitize_message(message: AstrBotMessage) -> AstrBotMessage:
        import copy
        sanitized_message = copy.copy(message)
        for attr in ['_client', '_callback', '_handler', '_context', 'raw_message']:
            if hasattr(sanitized_message, attr):
                setattr(sanitized_message, attr, None)
        return sanitized_message
    
    @staticmethod
    async def save_message(message: AstrBotMessage) -> bool:
        try:
            is_private_chat = not bool(message.group_id)
            platform_name = message.platform_name if hasattr(message, "platform_name") else "unknown"
            
            if is_private_chat:
                if hasattr(message, "private_id") and message.private_id:
                    chat_id = message.private_id
                else:
                    chat_id = message.sender.user_id
            else:
                chat_id = message.group_id
                
            file_path = HistoryStorage._get_storage_path(platform_name, is_private_chat, chat_id)
            history = HistoryStorage.get_history(platform_name, is_private_chat, chat_id) or []
            
            await HistoryStorage._process_image_persistence(message)

            sanitized_message = HistoryStorage._sanitize_message(message)
            history.append(sanitized_message)

            if len(history) > 200:
                history = history[-200:]

            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(jsonpickle.encode(history, unpicklable=True))

            import random
            if random.random() < 0.05:
                try: HistoryStorage._cleanup_old_images()
                except: pass

            return True
        except Exception as e:
            logger.error(f"保存消息历史记录失败: {e}")
            return False
    
    @staticmethod
    def is_chat_enabled(event: AstrMessageEvent) -> bool:
        if not HistoryStorage.config: return False
        is_private = event.is_private_chat()
        if is_private:
            return HistoryStorage.config.get("enabled_private", False)
        else:
            group_id = event.get_group_id()
            # 确保类型一致
            enabled_groups = [str(g) for g in HistoryStorage.config.get("enabled_groups", [])]
            return str(group_id) in enabled_groups
    
    @staticmethod
    async def process_and_save_user_message(event: AstrMessageEvent) -> None:
        if not HistoryStorage.is_chat_enabled(event): return
        message_obj = event.message_obj
        message_obj.platform_name = event.get_platform_name()
        await HistoryStorage.save_message(message_obj)
    
    @staticmethod
    def create_bot_message(chain: List[BaseMessageComponent], event: AstrMessageEvent) -> AstrBotMessage:
        msg = AstrBotMessage()
        msg.message = chain
        msg.platform_name = event.get_platform_name()
        msg.timestamp = int(time.time())
        
        is_private = event.is_private_chat()
        msg.type = MessageType.FRIEND_MESSAGE if is_private else MessageType.GROUP_MESSAGE
        if not is_private:
            msg.group_id = event.get_group_id()
        
        # 【修改点】将 nickname 硬编码为 Rosa
        msg.sender = MessageMember(user_id=event.get_self_id(), nickname="Rosa")
        msg.private_id = event.get_sender_id()
        
        msg.message_str = "".join([comp.text for comp in chain if isinstance(comp, Plain)])
        msg.self_id = event.message_obj.self_id if hasattr(event.message_obj, "self_id") else "bot"
        msg.session_id = event.session_id
        msg.message_id = f"bot_{int(time.time())}"
        
        return msg
    
    @staticmethod
    async def save_bot_message_from_chain(chain: List[BaseMessageComponent], event: AstrMessageEvent) -> bool:
        try:
            if not HistoryStorage.is_chat_enabled(event): return False
            bot_msg = HistoryStorage.create_bot_message(chain, event)
            return await HistoryStorage.save_message(bot_msg)
        except Exception as e:
            logger.error(f"保存机器人消息失败: {e}")
            return False
    
    @staticmethod
    def get_history(platform_name: str, is_private_chat: bool, chat_id: str) -> List[AstrBotMessage]:
        try:
            file_path = HistoryStorage._get_storage_path(platform_name, is_private_chat, chat_id)
            if not os.path.exists(file_path): return []
            with open(file_path, "r", encoding="utf-8") as f:
                return jsonpickle.decode(f.read())
        except Exception as e:
            logger.error(f"读取消息历史记录失败: {e}")
            return []
    
    @staticmethod
    def clear_history(platform_name: str, is_private_chat: bool, chat_id: str) -> bool:
        try:
            file_path = HistoryStorage._get_storage_path(platform_name, is_private_chat, chat_id)
            if os.path.exists(file_path): os.remove(file_path)
            return True
        except Exception as e:
            logger.error(f"清空消息历史记录失败: {e}")
            return False

    @staticmethod
    async def _process_image_persistence(message: AstrBotMessage) -> None:
        # ... (保持原有图片处理逻辑不变) ...
        try:
            if not HistoryStorage.config: return
            cfg = HistoryStorage.config.get("image_processing", {})
            if not cfg.get("enable_image_persistence", True): return
            if not hasattr(message, 'message') or not message.message: return

            from astrbot.core.utils.astrbot_path import get_astrbot_data_path
            images_dir = os.path.join(get_astrbot_data_path(), "chat_history", "images")
            HistoryStorage._ensure_dir(images_dir)

            for component in message.message:
                if isinstance(component, Image):
                    if component.file and component.file.startswith("file:///") and "/images/" in component.file: continue
                    try:
                        temp_file_path = await component.convert_to_file_path()
                        if temp_file_path and os.path.exists(temp_file_path):
                            import uuid, shutil
                            ext = ".jpg"
                            if "." in temp_file_path:
                                original_ext = os.path.splitext(temp_file_path)[1].lower()
                                if original_ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']: ext = original_ext
                            
                            fname = f"{uuid.uuid4().hex}{ext}"
                            dest = os.path.join(images_dir, fname)
                            shutil.copy2(temp_file_path, dest)
                            abs_dest = os.path.abspath(dest)
                            abs_dest = abs_dest.replace("\\", "/")
                            component.file = f"file:///{abs_dest}"
                    except: pass
        except: pass

    @staticmethod
    def _cleanup_old_images() -> None:
        # ... (保持原有清理逻辑不变) ...
        try:
            if not HistoryStorage.config: return
            cfg = HistoryStorage.config.get("image_processing", {})
            if not cfg.get("enable_image_persistence", True): return
            
            days = cfg.get("image_retention_days", 7)
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path
            images_dir = os.path.join(get_astrbot_data_path(), "chat_history", "images")
            if not os.path.exists(images_dir): return
            
            thresh = days * 24 * 3600
            now = time.time()
            for fname in os.listdir(images_dir):
                fpath = os.path.join(images_dir, fname)
                if os.path.isfile(fpath) and now - os.path.getctime(fpath) > thresh:
                    try: os.remove(fpath)
                    except: pass
        except: pass
