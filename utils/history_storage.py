import asyncio
import os
import random
import shutil
import time
from typing import List

import jsonpickle

from astrbot.api.all import *
from astrbot.core.utils.astrbot_path import (
    get_astrbot_data_path,
    get_astrbot_plugin_data_path,
)

from .image_caption import ImageCaptionUtils
from .image_ref import extract_image_src, normalize_image_ref

class HistoryStorage:
    """
    历史消息存储工具类
    """
    
    config = None
    base_storage_path = None
    legacy_base_storage_path = None
    images_path = None
    legacy_images_path = None
    _file_locks: dict[tuple[int, str], asyncio.Lock] = {}
    _use_plugin_data_root = True
    _keep_legacy_read_fallback = True
    _migrate_legacy_once = True
    _migration_done = False
    
    @staticmethod
    def init(config: AstrBotConfig):
        HistoryStorage.config = config
        storage_cfg = config.get("storage", {})
        HistoryStorage._use_plugin_data_root = bool(
            storage_cfg.get("use_plugin_data_root", True)
        )
        HistoryStorage._keep_legacy_read_fallback = bool(
            storage_cfg.get("keep_legacy_read_fallback", True)
        )
        HistoryStorage._migrate_legacy_once = bool(
            storage_cfg.get("migrate_legacy_once", True)
        )
        HistoryStorage.legacy_base_storage_path = os.path.join(
            get_astrbot_data_path(), "chat_history"
        )
        if HistoryStorage._use_plugin_data_root:
            plugin_root = os.path.join(
                get_astrbot_plugin_data_path(),
                "spectrecorepro",
            )
            HistoryStorage.base_storage_path = os.path.join(plugin_root, "chat_history")
            HistoryStorage.images_path = os.path.join(plugin_root, "images")
        else:
            HistoryStorage.base_storage_path = HistoryStorage.legacy_base_storage_path
            HistoryStorage.images_path = os.path.join(
                HistoryStorage.legacy_base_storage_path,
                "images",
            )
        HistoryStorage.legacy_images_path = os.path.join(
            HistoryStorage.legacy_base_storage_path,
            "images",
        )
        HistoryStorage._file_locks.clear()
        HistoryStorage._ensure_dir(HistoryStorage.base_storage_path)
        HistoryStorage._ensure_dir(HistoryStorage.images_path)
        if (
            HistoryStorage._use_plugin_data_root
            and HistoryStorage._migrate_legacy_once
            and not HistoryStorage._migration_done
        ):
            HistoryStorage._migrate_legacy_data()
        HistoryStorage._migration_done = True
        logger.info(f"消息存储路径初始化: {HistoryStorage.base_storage_path}")
        jsonpickle.set_encoder_options("json", ensure_ascii=False, indent=2)
        jsonpickle.set_preferred_backend("json")
    
    @staticmethod
    def _ensure_dir(directory: str) -> None:
        if not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)

    @staticmethod
    def _copy_tree_if_missing(src_dir: str, dst_dir: str) -> None:
        if not os.path.exists(src_dir):
            return
        for root, _dirs, files in os.walk(src_dir):
            rel = os.path.relpath(root, src_dir)
            target_root = dst_dir if rel == "." else os.path.join(dst_dir, rel)
            os.makedirs(target_root, exist_ok=True)
            for file_name in files:
                src_file = os.path.join(root, file_name)
                dst_file = os.path.join(target_root, file_name)
                if os.path.exists(dst_file):
                    continue
                try:
                    shutil.copy2(src_file, dst_file)
                except Exception as e:
                    logger.warning(f"迁移文件失败: {src_file} -> {dst_file} ({e})")

    @staticmethod
    def _migrate_legacy_data() -> None:
        try:
            legacy_root = HistoryStorage.legacy_base_storage_path
            if not legacy_root or not os.path.exists(legacy_root):
                return
            if HistoryStorage.base_storage_path:
                for item in os.listdir(legacy_root):
                    if item in {"images", "image_captions"}:
                        continue
                    src = os.path.join(legacy_root, item)
                    if not os.path.isdir(src):
                        continue
                    dst = os.path.join(HistoryStorage.base_storage_path, item)
                    HistoryStorage._copy_tree_if_missing(src, dst)
            if HistoryStorage.legacy_images_path and HistoryStorage.images_path:
                HistoryStorage._copy_tree_if_missing(
                    HistoryStorage.legacy_images_path,
                    HistoryStorage.images_path,
                )
        except Exception as e:
            logger.warning(f"迁移历史图片数据失败: {e}")

    @staticmethod
    def _get_file_lock(file_path: str) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        key = (id(loop), file_path)
        lock = HistoryStorage._file_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            HistoryStorage._file_locks[key] = lock
        return lock

    @staticmethod
    def _read_history_file(file_path: str) -> List[AstrBotMessage]:
        if not os.path.exists(file_path):
            return []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                decoded = jsonpickle.decode(f.read())
            return decoded if isinstance(decoded, list) else []
        except Exception as e:
            logger.error(f"读取消息历史记录失败: {e}")
            return []

    @staticmethod
    def _write_history_file(file_path: str, history: List[AstrBotMessage]) -> None:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(jsonpickle.encode(history, unpicklable=True))
    
    @staticmethod
    def _get_storage_path(platform_name: str, is_private_chat: bool, chat_id: str) -> str:
        if not HistoryStorage.base_storage_path:
            HistoryStorage.base_storage_path = os.path.join(
                get_astrbot_plugin_data_path(),
                "spectrecorepro",
                "chat_history",
            )
            HistoryStorage._ensure_dir(HistoryStorage.base_storage_path)

        chat_type = "private" if is_private_chat else "group"
        directory = os.path.join(
            HistoryStorage.base_storage_path, platform_name, chat_type
        )
        HistoryStorage._ensure_dir(directory)
        return os.path.join(directory, f"{chat_id}.json")

    @staticmethod
    def _get_legacy_storage_path(
        platform_name: str,
        is_private_chat: bool,
        chat_id: str,
    ) -> str:
        if not HistoryStorage.legacy_base_storage_path:
            HistoryStorage.legacy_base_storage_path = os.path.join(
                get_astrbot_data_path(),
                "chat_history",
            )
        chat_type = "private" if is_private_chat else "group"
        return os.path.join(
            HistoryStorage.legacy_base_storage_path,
            platform_name,
            chat_type,
            f"{chat_id}.json",
        )
    
    @staticmethod
    def _sanitize_message(message: AstrBotMessage) -> AstrBotMessage:
        import copy

        sanitized_message = copy.copy(message)
        for attr in ["_client", "_callback", "_handler", "_context", "raw_message"]:
            if hasattr(sanitized_message, attr):
                setattr(sanitized_message, attr, None)
        return sanitized_message

    @staticmethod
    def _get_image_src(component: Image) -> str | None:
        return extract_image_src(component)

    @staticmethod
    def _is_managed_image_path(path_value: str) -> bool:
        if not path_value or not HistoryStorage.images_path:
            return False
        try:
            managed_root = os.path.abspath(HistoryStorage.images_path)
            candidate = os.path.abspath(path_value)
            return os.path.commonpath([managed_root, candidate]) == managed_root
        except Exception:
            return False
    
    @staticmethod
    async def save_message(message: AstrBotMessage) -> bool:
        try:
            is_private_chat = not bool(message.group_id)
            platform_name = (
                message.platform_name
                if hasattr(message, "platform_name")
                else "unknown"
            )

            if is_private_chat:
                if hasattr(message, "private_id") and message.private_id:
                    chat_id = message.private_id
                else:
                    chat_id = message.sender.user_id
            else:
                chat_id = message.group_id

            file_path = HistoryStorage._get_storage_path(
                platform_name, is_private_chat, chat_id
            )
            file_lock = HistoryStorage._get_file_lock(file_path)

            await HistoryStorage._process_image_persistence(message)
            # 后台调度图片转述（不阻塞）
            try:
                if hasattr(message, "message") and message.message:
                    for comp in message.message:
                        if isinstance(comp, Image):
                            img_src = HistoryStorage._get_image_src(comp)
                            if not img_src:
                                continue
                            msg_ts = getattr(message, "timestamp", None)
                            ImageCaptionUtils.schedule_caption(
                                img_src,
                                platform_name,
                                is_private_chat,
                                chat_id,
                                msg_ts,
                            )
            except Exception:
                pass

            sanitized_message = HistoryStorage._sanitize_message(message)

            async with file_lock:
                history = await asyncio.to_thread(
                    HistoryStorage._read_history_file, file_path
                )
                history.append(sanitized_message)

                if len(history) > 200:
                    history = history[-200:]

                await asyncio.to_thread(
                    HistoryStorage._write_history_file, file_path, history
                )

            if random.random() < 0.05:
                try:
                    await asyncio.to_thread(HistoryStorage._cleanup_old_images)
                except Exception:
                    pass

            return True
        except Exception as e:
            logger.error(f"保存消息历史记录失败: {e}")
            return False

    @staticmethod
    async def retry_uncaptioned_images(platform_name: str, is_private_chat: bool, chat_id: str, max_scan: int = 30) -> None:
        """
        在 LLM 调用结束后，为近期未转述成功的图片再发起一次转述。
        - 已有转述缓存的图片不会重复
        - 插件重启前的图片不会被转述（依赖 schedule_caption 的 start_time/时间戳判断）
        """
        try:
            if not HistoryStorage.config:
                return
            if is_private_chat:
                if not HistoryStorage.config.get("enabled_private", False):
                    return
            else:
                if not chat_id:
                    return
                group_id = str(chat_id)
                blocked_groups = {str(g) for g in HistoryStorage.config.get("blocked_groups", [])}
                enabled_groups = {str(g) for g in HistoryStorage.config.get("enabled_groups", [])}
                if group_id in blocked_groups:
                    return
                if not HistoryStorage.config.get("enable_all_groups", False):
                    if group_id not in enabled_groups:
                        return
            history = (
                await HistoryStorage.get_history_async(
                    platform_name,
                    is_private_chat,
                    chat_id,
                )
            ) or []
            if not history:
                return
            recent = history[-max_scan:] if len(history) > max_scan else history
            for msg in recent:
                try:
                    msg_ts = getattr(msg, "timestamp", None)
                    if msg_ts is None or msg_ts < ImageCaptionUtils.start_time:
                        continue
                    if not hasattr(msg, "message") or not msg.message:
                        continue
                    for comp in msg.message:
                        if isinstance(comp, Image):
                            img_src = HistoryStorage._get_image_src(comp)
                            if not img_src:
                                continue
                            ImageCaptionUtils.schedule_caption(
                                img_src, platform_name, is_private_chat, chat_id, msg_ts
                            )
                        elif isinstance(comp, Reply) and getattr(comp, "chain", None):
                            for r in comp.chain:
                                if isinstance(r, Image):
                                    img_src = HistoryStorage._get_image_src(r)
                                    if not img_src:
                                        continue
                                    ImageCaptionUtils.schedule_caption(
                                        img_src,
                                        platform_name,
                                        is_private_chat,
                                        chat_id,
                                        msg_ts,
                                    )
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"重试图片转述失败: {e}")
    
    @staticmethod
    def is_chat_enabled(event: AstrMessageEvent) -> bool:
        if not HistoryStorage.config:
            return False
        is_private = event.is_private_chat()
        if is_private:
            return HistoryStorage.config.get("enabled_private", False)
        else:
            group_id_raw = event.get_group_id()
            if not group_id_raw:
                return False
            group_id = str(group_id_raw)
            blocked_groups = {str(g) for g in HistoryStorage.config.get("blocked_groups", [])}
            enabled_groups = {str(g) for g in HistoryStorage.config.get("enabled_groups", [])}
            if group_id in blocked_groups:
                return False
            if HistoryStorage.config.get("enable_all_groups", False):
                return True
            return group_id in enabled_groups
    
    @staticmethod
    async def process_and_save_user_message(event: AstrMessageEvent) -> None:
        if event.get_extra("incantation_command", False):
            return
        if not HistoryStorage.is_chat_enabled(event):
            return
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
            if not HistoryStorage.is_chat_enabled(event):
                return False
            bot_msg = HistoryStorage.create_bot_message(chain, event)
            return await HistoryStorage.save_message(bot_msg)
        except Exception as e:
            logger.error(f"保存机器人消息失败: {e}")
            return False

    @staticmethod
    async def get_history_async(
        platform_name: str,
        is_private_chat: bool,
        chat_id: str,
    ) -> List[AstrBotMessage]:
        file_path = HistoryStorage._get_storage_path(
            platform_name, is_private_chat, chat_id
        )
        file_lock = HistoryStorage._get_file_lock(file_path)
        async with file_lock:
            history = await asyncio.to_thread(
                HistoryStorage._read_history_file,
                file_path,
            )
        if history or not HistoryStorage._keep_legacy_read_fallback:
            return history

        legacy_path = HistoryStorage._get_legacy_storage_path(
            platform_name, is_private_chat, chat_id
        )
        legacy_lock = HistoryStorage._get_file_lock(legacy_path)
        async with legacy_lock:
            legacy_history = await asyncio.to_thread(
                HistoryStorage._read_history_file,
                legacy_path,
            )
        if legacy_history and not os.path.exists(file_path):
            async with file_lock:
                await asyncio.to_thread(
                    HistoryStorage._write_history_file,
                    file_path,
                    legacy_history,
                )
        return legacy_history
    
    @staticmethod
    def get_history(platform_name: str, is_private_chat: bool, chat_id: str) -> List[AstrBotMessage]:
        file_path = HistoryStorage._get_storage_path(
            platform_name, is_private_chat, chat_id
        )
        history = HistoryStorage._read_history_file(file_path)
        if history or not HistoryStorage._keep_legacy_read_fallback:
            return history
        legacy_path = HistoryStorage._get_legacy_storage_path(
            platform_name, is_private_chat, chat_id
        )
        legacy_history = HistoryStorage._read_history_file(legacy_path)
        if legacy_history and not os.path.exists(file_path):
            HistoryStorage._write_history_file(file_path, legacy_history)
        return legacy_history
    
    @staticmethod
    def clear_history(platform_name: str, is_private_chat: bool, chat_id: str) -> bool:
        try:
            file_path = HistoryStorage._get_storage_path(
                platform_name, is_private_chat, chat_id
            )
            if os.path.exists(file_path):
                os.remove(file_path)
            legacy_path = HistoryStorage._get_legacy_storage_path(
                platform_name, is_private_chat, chat_id
            )
            if os.path.exists(legacy_path):
                os.remove(legacy_path)
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

            images_dir = HistoryStorage.images_path or os.path.join(
                get_astrbot_data_path(), "chat_history", "images"
            )
            HistoryStorage._ensure_dir(images_dir)

            for component in message.message:
                if isinstance(component, Image):
                    file_ref = getattr(component, "file", "")
                    if isinstance(file_ref, str) and file_ref.startswith("file:///"):
                        normalized_ref = normalize_image_ref(file_ref)
                        if HistoryStorage._is_managed_image_path(normalized_ref):
                            continue
                    try:
                        temp_file_path = None
                        if isinstance(file_ref, str):
                            if file_ref.startswith("file:///"):
                                candidate = normalize_image_ref(file_ref)
                                if os.path.exists(candidate) and os.path.getsize(candidate) > 0:
                                    temp_file_path = candidate
                            elif os.path.exists(file_ref):
                                if os.path.getsize(file_ref) > 0:
                                    temp_file_path = file_ref
                        if not temp_file_path:
                            temp_file_path = await component.convert_to_file_path()
                        if (
                            temp_file_path
                            and os.path.exists(temp_file_path)
                            and os.path.getsize(temp_file_path) > 0
                        ):
                            import uuid
                            ext = ".jpg"
                            if "." in temp_file_path:
                                original_ext = os.path.splitext(temp_file_path)[1].lower()
                                if original_ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']:
                                    ext = original_ext
                            
                            fname = f"{uuid.uuid4().hex}{ext}"
                            dest = os.path.join(images_dir, fname)
                            shutil.copy2(temp_file_path, dest)
                            abs_dest = normalize_image_ref(dest)
                            component.file = f"file:///{abs_dest}"
                    except Exception:
                        pass
        except Exception:
            pass

    @staticmethod
    def _cleanup_old_images() -> None:
        # ... (保持原有清理逻辑不变) ...
        try:
            if not HistoryStorage.config: return
            cfg = HistoryStorage.config.get("image_processing", {})
            if not cfg.get("enable_image_persistence", True): return
            
            days = cfg.get("image_retention_days", 7)
            images_dir = HistoryStorage.images_path or os.path.join(
                get_astrbot_data_path(), "chat_history", "images"
            )
            if not os.path.exists(images_dir): return
            
            thresh = days * 24 * 3600
            now = time.time()
            for fname in os.listdir(images_dir):
                fpath = os.path.join(images_dir, fname)
                if os.path.isfile(fpath) and now - os.path.getctime(fpath) > thresh:
                    try:
                        os.remove(fpath)
                    except Exception:
                        pass
        except Exception:
            pass
