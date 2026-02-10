from astrbot.api.all import *
from typing import Optional, Dict, Any
import asyncio
import os
import json
import hashlib
import time
import shutil
from astrbot.core.utils.astrbot_path import (
    get_astrbot_data_path,
    get_astrbot_plugin_data_path,
)

from .image_ref import build_image_aliases, normalize_image_ref

class ImageCaptionUtils:
    """
    图片转述工具类
    
    用于调用大语言模型将图片转述为文本描述
    """
    
    # 保存context和config对象的静态变量
    context = None
    config = None
    # 图片描述缓存
    caption_cache = {}
    cache_dir = None
    legacy_cache_dir = None
    _pending: set[str] = set()
    start_time: float = 0.0
    _sema: asyncio.Semaphore | None = None
    _use_plugin_data_root = True
    _keep_legacy_read_fallback = True
    _migrate_legacy_once = True
    _migration_done = False
    
    @staticmethod
    def init(context: Context, config: AstrBotConfig):
        """初始化图片转述工具类，保存context和config引用"""
        ImageCaptionUtils.context = context
        ImageCaptionUtils.config = config
        storage_cfg = config.get("storage", {})
        ImageCaptionUtils._use_plugin_data_root = bool(
            storage_cfg.get("use_plugin_data_root", True)
        )
        ImageCaptionUtils._keep_legacy_read_fallback = bool(
            storage_cfg.get("keep_legacy_read_fallback", True)
        )
        ImageCaptionUtils._migrate_legacy_once = bool(
            storage_cfg.get("migrate_legacy_once", True)
        )
        ImageCaptionUtils.legacy_cache_dir = os.path.join(
            get_astrbot_data_path(), "chat_history", "image_captions"
        )
        if ImageCaptionUtils._use_plugin_data_root:
            base = os.path.join(
                get_astrbot_plugin_data_path(),
                "spectrecorepro",
                "image_captions",
            )
        else:
            base = ImageCaptionUtils.legacy_cache_dir
        ImageCaptionUtils.cache_dir = base
        os.makedirs(base, exist_ok=True)
        if (
            ImageCaptionUtils._use_plugin_data_root
            and ImageCaptionUtils._migrate_legacy_once
            and not ImageCaptionUtils._migration_done
        ):
            ImageCaptionUtils._migrate_legacy_cache()
        ImageCaptionUtils._migration_done = True
        ImageCaptionUtils.start_time = time.time()
        ImageCaptionUtils.caption_cache.clear()
        ImageCaptionUtils._pending.clear()
        conc = int(config.get("image_processing", {}).get("caption_concurrency", 2))
        conc = 1 if conc <= 0 else conc
        ImageCaptionUtils._sema = asyncio.Semaphore(conc)

    @staticmethod
    def _cache_key(image: str) -> str:
        return normalize_image_ref(str(image))

    @staticmethod
    def _hash_image(image: str) -> str:
        h = hashlib.sha1()
        h.update(ImageCaptionUtils._cache_key(image).encode("utf-8"))
        return h.hexdigest()

    @staticmethod
    def _legacy_hash_image(image: str) -> str:
        h = hashlib.sha1()
        h.update(str(image).encode("utf-8"))
        return h.hexdigest()

    @staticmethod
    def _legacy_hash_candidates(image: str) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()
        refs = [str(image)]
        try:
            refs.extend(build_image_aliases(str(image)))
        except Exception:
            pass
        for ref in refs:
            hashed = ImageCaptionUtils._legacy_hash_image(ref)
            if hashed in seen:
                continue
            seen.add(hashed)
            candidates.append(hashed)
        return candidates

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
                    logger.warning(
                        f"迁移图片转述缓存失败: {src_file} -> {dst_file} ({e})"
                    )

    @staticmethod
    def _migrate_legacy_cache() -> None:
        try:
            if (
                not ImageCaptionUtils.legacy_cache_dir
                or not ImageCaptionUtils.cache_dir
                or ImageCaptionUtils.legacy_cache_dir == ImageCaptionUtils.cache_dir
            ):
                return
            ImageCaptionUtils._copy_tree_if_missing(
                ImageCaptionUtils.legacy_cache_dir,
                ImageCaptionUtils.cache_dir,
            )
        except Exception as e:
            logger.warning(f"迁移图片转述缓存目录失败: {e}")

    @staticmethod
    def _cache_path(
        platform: str,
        chat_type: str,
        chat_id: str,
        *,
        base_dir: str | None = None,
        create_dir: bool = True,
    ) -> str:
        safe_platform = platform or "unknown"
        safe_type = chat_type or "group"
        safe_chat = str(chat_id or "unknown")
        root_dir = base_dir if base_dir else ImageCaptionUtils.cache_dir
        path = os.path.join(root_dir, safe_platform, safe_type)
        if create_dir:
            os.makedirs(path, exist_ok=True)
        return os.path.join(path, f"{safe_chat}.json")

    @staticmethod
    def _looks_like_error_text(text: str) -> bool:
        lowered = text.lower()
        if "invalid_argument" in lowered:
            return True
        if "http" in lowered and "error" in lowered:
            return True
        if "请求" in text and "失败" in text:
            return True
        if "错误详情" in text:
            return True
        return False

    @staticmethod
    def _load_cache(path: str) -> Dict[str, Any]:
        if not os.path.exists(path): return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _save_cache(path: str, data: Dict[str, Any]) -> None:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存图片转述缓存失败: {e}")

    @staticmethod
    def _on_caption_task_done(task: asyncio.Task) -> None:
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        except Exception:
            return
        if error:
            logger.warning(f"后台图片转述任务异常: {error}")

    @staticmethod
    async def _wait_and_caption(image: str, platform_name: str, is_private: bool, chat_id: str):
        try:
            from .llm_utils import LLMUtils  # 延迟导入避免循环
        except Exception:
            LLMUtils = None

        # 若 LLM 正在处理该会话，等待空闲或超时
        for _ in range(60):
            try:
                if not LLMUtils or not LLMUtils.is_llm_in_progress(platform_name, is_private, chat_id):
                    break
            except Exception:
                break
            await asyncio.sleep(1)

        try:
            sema = ImageCaptionUtils._sema
            if sema:
                async with sema:
                    await ImageCaptionUtils.generate_image_caption(
                        image,
                        platform_name=platform_name,
                        is_private=is_private,
                        chat_id=chat_id,
                    )
            else:
                await ImageCaptionUtils.generate_image_caption(
                    image,
                    platform_name=platform_name,
                    is_private=is_private,
                    chat_id=chat_id,
                )
        finally:
            ImageCaptionUtils._pending.discard(ImageCaptionUtils._hash_image(image))

    @staticmethod
    def schedule_caption(image: str, platform_name: str, is_private: bool, chat_id: str, msg_ts: float | None = None):
        """后台调度图片转述（幂等）。若命中缓存/正在转述则不重复。"""
        cfg = ImageCaptionUtils.config.get("image_processing", {}) if ImageCaptionUtils.config else {}
        if not cfg.get("use_image_caption", False):
            return
        # 插件重启后，仅处理新的图片
        if msg_ts is not None and msg_ts < ImageCaptionUtils.start_time:
            return
        hashed = ImageCaptionUtils._hash_image(image)
        if hashed in ImageCaptionUtils._pending:
            return
        if ImageCaptionUtils.get_cached_caption(image, platform_name, is_private, chat_id):
            return
        ImageCaptionUtils._pending.add(hashed)
        task = asyncio.create_task(
            ImageCaptionUtils._wait_and_caption(image, platform_name, is_private, chat_id)
        )
        task.add_done_callback(ImageCaptionUtils._on_caption_task_done)

    @staticmethod
    def _prune_cache_data(
        data: Dict[str, Any],
        max_age_days: int,
        max_items: int,
    ) -> Dict[str, Any]:
        if not data:
            return data
        try:
            now = time.time()
            # 删除过期
            expire_ts = now - max_age_days * 86400
            for k in list(data.keys()):
                ts = data[k].get("ts", 0)
                if ts < expire_ts:
                    data.pop(k, None)
            # 超出数量则按时间排序保留最新
            if len(data) > max_items > 0:
                sorted_items = sorted(data.items(), key=lambda kv: kv[1].get("ts", 0), reverse=True)
                data = dict(sorted_items[:max_items])
        except Exception:
            pass
        return data

    @staticmethod
    def get_memory_caption(image: str) -> Optional[str]:
        return ImageCaptionUtils.caption_cache.get(ImageCaptionUtils._cache_key(image))

    @staticmethod
    def get_cached_caption(image: str, platform: str, is_private: bool, chat_id: str) -> Optional[str]:
        chat_type = "private" if is_private else "group"
        hashed = ImageCaptionUtils._hash_image(image)
        legacy_hashes = ImageCaptionUtils._legacy_hash_candidates(image)
        candidates = [
            ImageCaptionUtils._cache_path(platform, chat_type, chat_id),
        ]
        if (
            ImageCaptionUtils._keep_legacy_read_fallback
            and ImageCaptionUtils.legacy_cache_dir
            and ImageCaptionUtils.legacy_cache_dir != ImageCaptionUtils.cache_dir
        ):
            candidates.append(
                ImageCaptionUtils._cache_path(
                    platform,
                    chat_type,
                    chat_id,
                    base_dir=ImageCaptionUtils.legacy_cache_dir,
                    create_dir=False,
                )
            )
        for index, path in enumerate(candidates):
            data = ImageCaptionUtils._load_cache(path)
            if not data:
                continue
            item = data.get(hashed)
            if not item:
                hit_legacy_hash = None
                for legacy_hashed in legacy_hashes:
                    if legacy_hashed == hashed:
                        continue
                    legacy_item = data.get(legacy_hashed)
                    if legacy_item:
                        item = legacy_item
                        hit_legacy_hash = legacy_hashed
                        break
                if item and index == 0:
                    data[hashed] = item
                    if hit_legacy_hash:
                        data.pop(hit_legacy_hash, None)
                    ImageCaptionUtils._save_cache(path, data)
            if not item:
                continue
            caption = item.get("caption")
            if not caption:
                continue
            if index > 0:
                try:
                    ImageCaptionUtils.set_cached_caption(
                        image,
                        caption,
                        platform,
                        is_private,
                        chat_id,
                    )
                except Exception:
                    pass
            return caption
        return None

    @staticmethod
    def set_cached_caption(image: str, caption: str, platform: str, is_private: bool, chat_id: str) -> None:
        chat_type = "private" if is_private else "group"
        path = ImageCaptionUtils._cache_path(platform, chat_type, chat_id)
        data = ImageCaptionUtils._load_cache(path)
        hashed = ImageCaptionUtils._hash_image(image)
        data[hashed] = {"caption": caption, "ts": time.time()}
        for legacy_hashed in ImageCaptionUtils._legacy_hash_candidates(image):
            if legacy_hashed != hashed:
                data.pop(legacy_hashed, None)
        # 清理策略
        cfg = ImageCaptionUtils.config.get("image_processing", {})
        max_age = int(cfg.get("caption_cache_days", 7))
        max_items = int(cfg.get("caption_cache_limit", 200))
        data = ImageCaptionUtils._prune_cache_data(data, max_age, max_items)
        ImageCaptionUtils._save_cache(path, data)

    @staticmethod
    async def generate_image_caption(
        image: str,
        timeout: int = 30,
        platform_name: str = "",
        is_private: bool = False,
        chat_id: str = "",
    ) -> Optional[str]:
        """
        为单张图片生成文字描述
        
        Args:
            image: 图片的base64编码或URL
            timeout: 超时时间（秒）
            
        Returns:
            生成的图片描述文本，如果失败则返回None
        """
        config = ImageCaptionUtils.config
        context = ImageCaptionUtils.context
        if not config:
            return None
        image_processing_config = config.get("image_processing", {})
        if not image_processing_config.get("use_image_caption", False):
            return None

        cache_key = ImageCaptionUtils._cache_key(image)
        memory_caption = ImageCaptionUtils.caption_cache.get(cache_key)
        if memory_caption:
            return memory_caption

        persistent_caption = None
        try:
            if image_processing_config.get("caption_cache_persist", True):
                persistent_caption = ImageCaptionUtils.get_cached_caption(
                    image, platform_name, is_private, chat_id
                )
        except Exception:
            persistent_caption = None

        if persistent_caption:
            ImageCaptionUtils.caption_cache[cache_key] = persistent_caption
            return persistent_caption

        if isinstance(image, str):
            if image.startswith("file:///"):
                file_path = normalize_image_ref(image)
                if not os.path.exists(file_path) or os.path.getsize(file_path) <= 0:
                    logger.warning(f"图片转述跳过：本地文件无效 {file_path}")
                    return None
            elif image.startswith("http"):
                pass
            elif os.path.exists(image):
                if os.path.getsize(image) <= 0:
                    logger.warning(f"图片转述跳过：本地文件为空 {image}")
                    return None

        effective_image = image
        if isinstance(image, str) and image.startswith("http"):
            try:
                from .image_downloader import download_image_by_url_safe

                effective_image = await download_image_by_url_safe(image)
                if not effective_image:
                    logger.warning(f"图片转述跳过：下载为空 {image}")
                    return None
            except Exception as e:
                logger.warning(f"图片转述跳过：下载失败 {image} ({e})")
                return None

        provider_id = image_processing_config.get("image_caption_provider_id", "")
        # 获取提供商
        if provider_id == "":
            provider = context.get_using_provider()
        else:
            provider = context.get_provider_by_id(provider_id)
        
        if not provider:
            logger.warning(f"无法找到提供商: {provider_id if provider_id else '默认'}")
            return None

        try:
            # 带超时控制的调用大模型进行图片转述
            async def call_llm():
                return await provider.text_chat(
                    prompt=image_processing_config.get("image_caption_prompt", "请直接简短描述这张图片"),
                    contexts=[], 
                    image_urls=[effective_image], # 图片链接，支持路径和网络链接
                    func_tool=None, # 当前用户启用的函数调用工具。如果不需要，可以不传
                    system_prompt=""  # 系统提示，可以不传
                )
            
            # 使用asyncio.wait_for添加超时控制
            llm_response = await asyncio.wait_for(call_llm(), timeout=timeout)
            caption = (llm_response.completion_text or "").strip()
            role = getattr(llm_response, "role", "")
            if role and role != "assistant":
                short_caption = caption.replace("\n", " ").strip()
                if len(short_caption) > 80:
                    short_caption = short_caption[:80] + "..."
                logger.warning(f"[SpectreCore] 图片转述失败({role}): {short_caption}")
                return None
            if not caption:
                return None
            if ImageCaptionUtils._looks_like_error_text(caption):
                short_caption = caption.replace("\n", " ").strip()
                if len(short_caption) > 80:
                    short_caption = short_caption[:80] + "..."
                logger.warning(f"[SpectreCore] 图片转述异常文本: {short_caption}")
                return None
            
            # 缓存结果
            if caption:
                short_caption = caption.replace("\n", " ").strip()
                if len(short_caption) > 80:
                    short_caption = short_caption[:80] + "..."
                logger.info(f"[SpectreCore] 图片转述完成: {short_caption}")
                ImageCaptionUtils.caption_cache[cache_key] = caption
                logger.debug(f"缓存图片描述: {image[:50]}... -> {caption}")
                try:
                    if image_processing_config.get("caption_cache_persist", True):
                        ImageCaptionUtils.set_cached_caption(
                            image, caption, platform_name, is_private, chat_id
                        )
                except Exception as e:
                    logger.warning(f"写入持久化转述缓存失败: {e}")
                 
            return caption
        except asyncio.TimeoutError:
            logger.warning(f"图片转述超时，超过了{timeout}秒")
            return None
        except Exception as e:
            logger.error(f"图片转述失败: {e}")
            return None
