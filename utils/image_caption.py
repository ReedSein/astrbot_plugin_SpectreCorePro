from astrbot.api.all import *
from typing import Optional, Dict, Any
import asyncio
import os
import json
import hashlib
import time

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
    _pending: set[str] = set()
    start_time: float = 0.0
    _sema: asyncio.Semaphore | None = None
    
    @staticmethod
    def init(context: Context, config: AstrBotConfig):
        """初始化图片转述工具类，保存context和config引用"""
        ImageCaptionUtils.context = context
        ImageCaptionUtils.config = config
        base = os.path.join(os.getcwd(), "data", "chat_history", "image_captions")
        ImageCaptionUtils.cache_dir = base
        os.makedirs(base, exist_ok=True)
        ImageCaptionUtils.start_time = time.time()
        conc = int(config.get("image_processing", {}).get("caption_concurrency", 2))
        conc = 1 if conc <= 0 else conc
        ImageCaptionUtils._sema = asyncio.Semaphore(conc)
    
    @staticmethod
    def _hash_image(image: str) -> str:
        h = hashlib.sha1()
        h.update(str(image).encode("utf-8"))
        return h.hexdigest()

    @staticmethod
    def _cache_path(platform: str, chat_type: str, chat_id: str) -> str:
        safe_platform = platform or "unknown"
        safe_type = chat_type or "group"
        safe_chat = str(chat_id or "unknown")
        path = os.path.join(ImageCaptionUtils.cache_dir, safe_platform, safe_type)
        os.makedirs(path, exist_ok=True)
        return os.path.join(path, f"{safe_chat}.json")

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
    async def _wait_and_caption(image: str, platform_name: str, is_private: bool, chat_id: str):
        try:
            from .llm_utils import LLMUtils  # 延迟导入，避免循环依赖
        except Exception:
            LLMUtils = None

        # 如果 LLM 正在处理，等待空闲（最长 60s）
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
    def schedule_caption(image: str, platform_name: str, is_private: bool, chat_id: str):
        """后台调度图片转述；命中缓存或正在排队则直接返回。"""
        cfg = ImageCaptionUtils.config.get("image_processing", {}) if ImageCaptionUtils.config else {}
        if not cfg.get("use_image_caption", False):
            return
        hashed = ImageCaptionUtils._hash_image(image)
        if hashed in ImageCaptionUtils._pending:
            return
        if ImageCaptionUtils.get_cached_caption(image, platform_name, is_private, chat_id):
            return
        ImageCaptionUtils._pending.add(hashed)
        asyncio.create_task(
            ImageCaptionUtils._wait_and_caption(image, platform_name, is_private, chat_id)
        )

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
        asyncio.create_task(
            ImageCaptionUtils._wait_and_caption(image, platform_name, is_private, chat_id)
        )

    @staticmethod
    def _prune_cache(path: str, max_age_days: int, max_items: int) -> None:
        try:
            data = ImageCaptionUtils._load_cache(path)
            if not data: return
            now = time.time()
            changed = False
            # 删除过期
            expire_ts = now - max_age_days * 86400
            for k in list(data.keys()):
                ts = data[k].get("ts", 0)
                if ts < expire_ts:
                    data.pop(k, None)
                    changed = True
            # 超出数量则按时间排序保留最新
            if len(data) > max_items > 0:
                sorted_items = sorted(data.items(), key=lambda kv: kv[1].get("ts", 0), reverse=True)
                data = dict(sorted_items[:max_items])
                changed = True
            if changed:
                ImageCaptionUtils._save_cache(path, data)
        except Exception: pass

    @staticmethod
    def get_cached_caption(image: str, platform: str, is_private: bool, chat_id: str) -> Optional[str]:
        path = ImageCaptionUtils._cache_path(platform, "private" if is_private else "group", chat_id)
        data = ImageCaptionUtils._load_cache(path)
        hashed = ImageCaptionUtils._hash_image(image)
        item = data.get(hashed)
        if item:
            return item.get("caption")
        return None

    @staticmethod
    def set_cached_caption(image: str, caption: str, platform: str, is_private: bool, chat_id: str) -> None:
        path = ImageCaptionUtils._cache_path(platform, "private" if is_private else "group", chat_id)
        data = ImageCaptionUtils._load_cache(path)
        hashed = ImageCaptionUtils._hash_image(image)
        data[hashed] = {"caption": caption, "ts": time.time()}
        # 清理策略
        cfg = ImageCaptionUtils.config.get("image_processing", {})
        max_age = int(cfg.get("caption_cache_days", 7))
        max_items = int(cfg.get("caption_cache_limit", 200))
        ImageCaptionUtils._prune_cache(path, max_age, max_items)
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
        # 检查缓存
        if image in ImageCaptionUtils.caption_cache:
            logger.debug(f"命中图片描述缓存: {image[:50]}...")
            return ImageCaptionUtils.caption_cache[image]
        
        # 获取配置
        config = ImageCaptionUtils.config
        context = ImageCaptionUtils.context
        # 检查是否已启用图片转述
        image_processing_config = config.get("image_processing", {})
        if not image_processing_config.get("use_image_caption", False):
            return None

        persistent_caption = None
        try:
            if image_processing_config.get("caption_cache_persist", True):
                persistent_caption = ImageCaptionUtils.get_cached_caption(
                    image, platform_name, is_private, chat_id
                )
        except Exception:
            persistent_caption = None

        if persistent_caption:
            ImageCaptionUtils.caption_cache[image] = persistent_caption
            return persistent_caption

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
                    image_urls=[image], # 图片链接，支持路径和网络链接
                    func_tool=None, # 当前用户启用的函数调用工具。如果不需要，可以不传
                    system_prompt=""  # 系统提示，可以不传
                )
            
            # 使用asyncio.wait_for添加超时控制
            llm_response = await asyncio.wait_for(call_llm(), timeout=timeout)
            caption = llm_response.completion_text
            
            # 缓存结果
            if caption:
                 ImageCaptionUtils.caption_cache[image] = caption
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
