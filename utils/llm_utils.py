from astrbot.api.all import *
from typing import Dict, List, Optional, Any
import time
import datetime
import threading
import aiohttp
import json
import os
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from .history_storage import HistoryStorage
from .message_utils import MessageUtils
from astrbot.core.provider.entites import ProviderRequest
from .persona_utils import PersonaUtils

class LLMUtils:
    """
    大模型调用工具类 (SpectreCore Pro - Dual Timezone + Multi-Source Sync)
    支持双时区并行感知，并内置多源网络时间校准机制（抗干扰版）。
    """
    
    _llm_call_status: Dict[str, Dict[str, Any]] = {}
    _lock = threading.Lock()
    
    # 网络时间校准相关
    _time_offset: float = 0.0  # 网络时间 - 系统时间 的偏移量 (秒)
    _is_time_synced: bool = False
    _sync_lock = asyncio.Lock()
    
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
    async def _sync_network_time(timezone_str: str):
        """
        [异步] 多源自主联网校准时间
        依次尝试多个高可用时间接口，计算系统时间偏差。
        """
        if LLMUtils._is_time_synced: return

        async with LLMUtils._sync_lock:
            if LLMUtils._is_time_synced: return
            
            logger.info(f"[SpectreCore] 正在执行多源时间校准...")
            
            # 定义时间源列表 (URL, 类型)
            # 类型用于区分不同的解析逻辑
            sources = [
                # 1. 淘宝 API (国内极速，返回毫秒级时间戳)
                ("http://api.m.taobao.com/rest/api3.do?api=mtop.common.getTimestamp", "taobao"),
                # 2. WorldTimeAPI (国际标准)
                ("http://worldtimeapi.org/api/timezone/Etc/UTC", "worldtimeapi"),
                # 3. 苏宁 API (备用)
                ("http://quan.suning.com/getSysTime.do", "suning")
            ]
            
            async with aiohttp.ClientSession() as session:
                for url, src_type in sources:
                    try:
                        logger.debug(f"[SpectreCore] 尝试连接时间源 ({src_type})...")
                        # 设置较短的超时，快速故障转移
                        async with session.get(url, timeout=3) as resp:
                            if resp.status == 200:
                                remote_ts = 0.0
                                
                                if src_type == "worldtimeapi":
                                    data = await resp.json()
                                    datetime_str = data.get('datetime')
                                    remote_time = datetime.datetime.fromisoformat(datetime_str)
                                    remote_ts = remote_time.timestamp()
                                    
                                elif src_type == "taobao":
                                    data = await resp.json()
                                    # 格式: {"data": {"t": "169..."}}
                                    ts_ms = data.get('data', {}).get('t')
                                    if ts_ms:
                                        remote_ts = int(ts_ms) / 1000.0
                                        
                                elif src_type == "suning":
                                    data = await resp.json()
                                    # 格式: {"sysTime2":"2023-10-27 10:00:00"}
                                    time_str = data.get('sysTime2')
                                    if time_str:
                                        # 苏宁返回的是北京时间字符串
                                        dt = datetime.datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
                                        # 减去8小时转为 UTC 时间戳，或者直接设为北京时区再转 timestamp
                                        tz_cn = datetime.timezone(datetime.timedelta(hours=8))
                                        dt = dt.replace(tzinfo=tz_cn)
                                        remote_ts = dt.timestamp()

                                if remote_ts > 0:
                                    # 核心逻辑：计算偏移量
                                    # 偏移量 = 真实网络时间戳 - 本地系统时间戳
                                    # 后续获取时间时：PreceiseTime = LocalTime + Offset
                                    system_now_ts = time.time()
                                    LLMUtils._time_offset = remote_ts - system_now_ts
                                    LLMUtils._is_time_synced = True
                                    
                                    logger.info(f"[SpectreCore] 时间校准成功 (来源: {src_type})！系统时间偏差: {LLMUtils._time_offset:.2f}秒")
                                    return # 成功后直接退出
                    except Exception as e:
                        logger.warning(f"[SpectreCore] 时间源 {src_type} 连接失败: {e}")
                        continue
            
            logger.error("[SpectreCore] ❌ 所有网络时间源均不可用，将强制回退至系统本地时间。请检查服务器网络连接。")

    @staticmethod
    async def _get_precise_now(config: AstrBotConfig, tz_name: str) -> datetime.datetime:
        """
        获取指定时区的当前精准时间
        """
        # 1. 解析时区
        try:
            target_tz = ZoneInfo(tz_name)
        except:
            target_tz = ZoneInfo("Asia/Shanghai")
            
        # 2. 联网校准触发 (只需一次)
        if config.get("enable_internet_time", False) and not LLMUtils._is_time_synced:
            # 异步触发校准，不阻塞当前请求太久，但为了第一次准确，这里 await
            await LLMUtils._sync_network_time("Etc/UTC")
            
        # 3. 计算时间
        now_ts = time.time()
        if LLMUtils._is_time_synced:
            now_ts += LLMUtils._time_offset
            
        return datetime.datetime.fromtimestamp(now_ts, target_tz)

    @staticmethod
    def _calculate_time_diff_desc(seconds: float) -> str:
        if seconds < 60: return f"{int(seconds)}秒"
        elif seconds < 3600: return f"{int(seconds/60)}分钟"
        elif seconds < 86400: return f"{int(seconds/3600)}小时"
        else: return f"{int(seconds/86400)}天"

    @staticmethod
    def _get_tz_display_name(tz_str: str) -> str:
        mapping = {
            "Asia/Shanghai": "北京时间",
            "Asia/Hong_Kong": "香港时间",
            "Asia/Taipei": "台北时间",
            "Asia/Tokyo": "东京时间",
            "America/Los_Angeles": "美国太平洋时区",
            "America/New_York": "美国东部时间",
            "Europe/London": "伦敦时间",
            "Europe/Paris": "巴黎时间"
        }
        return mapping.get(tz_str, tz_str)

    @staticmethod
    async def _get_time_prompt(history_msgs: List[AstrBotMessage], current_user_id: str, config: AstrBotConfig) -> str:
        """
        [双时区 + 多源校准版] 生成时间感知提示词
        """
        try:
            if not config.get('enable_time_tracking', True):
                return ""
            
            # 1. 获取主时区时间
            tz_primary = config.get("system_timezone", "Asia/Shanghai")
            dt_primary = await LLMUtils._get_precise_now(config, tz_primary)
            
            # 2. 获取副时区时间 (如果有)
            tz_secondary = config.get("secondary_timezone", "America/Los_Angeles")
            time_display_str = ""
            
            if tz_secondary and tz_secondary.strip():
                dt_secondary = await LLMUtils._get_precise_now(config, tz_secondary)
                time_display_str = (
                    f"当前时间:\n"
                    f"{dt_secondary.strftime('%Y-%m-%d %H:%M:%S')} ({LLMUtils._get_tz_display_name(tz_secondary)})\n"
                    f"{dt_primary.strftime('%Y-%m-%d %H:%M:%S')} ({LLMUtils._get_tz_display_name(tz_primary)})"
                )
            else:
                time_display_str = f"当前时间 ({LLMUtils._get_tz_display_name(tz_primary)}): {dt_primary.strftime('%Y-%m-%d %H:%M:%S')}"

            # 3. 计算活跃度 (使用 UTC 时间戳计算差值)
            if not history_msgs:
                return f"{time_display_str}。"

            current_ts = dt_primary.timestamp() 
            
            last_global_msg = None
            last_user_msg = None
            
            for i in range(len(history_msgs) - 1, -1, -1):
                msg = history_msgs[i]
                if not hasattr(msg, "timestamp") or not msg.timestamp: continue
                
                diff = current_ts - msg.timestamp
                if diff < 2.0:
                    sender_id = str(msg.sender.user_id) if (hasattr(msg, "sender") and msg.sender) else ""
                    if sender_id == str(current_user_id): continue
                
                if last_global_msg is None: last_global_msg = msg
                sender_id = str(msg.sender.user_id) if (hasattr(msg, "sender") and msg.sender) else ""
                if last_user_msg is None and sender_id == str(current_user_id): last_user_msg = msg
                if last_global_msg and last_user_msg: break
            
            # --- 构建 Prompt ---
            prompts = [f"{time_display_str}。"]
            
            user_interval_desc = "这是首次发言"
            if last_user_msg:
                user_diff = current_ts - last_user_msg.timestamp
                user_interval_desc = f"距离该用户上次发言已过去 {LLMUtils._calculate_time_diff_desc(user_diff)}"
            prompts.append(f"[用户状态]: {user_interval_desc}。")

            if last_global_msg and last_global_msg != last_user_msg:
                global_diff = current_ts - last_global_msg.timestamp
                global_desc = LLMUtils._calculate_time_diff_desc(global_diff)
                sender_name = last_global_msg.sender.nickname if hasattr(last_global_msg.sender, "nickname") else "其他人"
                prompts.append(f"[环境状态]: 群聊处于活跃状态，{global_desc}前 '{sender_name}' 刚发过言。")
            elif last_global_msg:
                prompts.append(f"[环境状态]: 此前群聊处于静默状态。")

            prompts.append("请据此调整语气：注意双时区差异（例如对方可能是深夜，而你是白天）；若用户久别重逢应寒暄；若连续对话则保持连贯。")

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

        # 特例：引用图片且 @Bot 时，若图片未上传且无转述，则先同步转述
        try:
            if not is_private and hasattr(event.message_obj, "message"):
                bot_id = bot_self_id
                at_me = any(
                    isinstance(c, At) and (str(c.qq) == bot_id or c.qq == "all")
                    for c in event.message_obj.message
                )
                if at_me:
                    for comp in event.message_obj.message:
                        if isinstance(comp, Reply) and getattr(comp, "chain", None):
                                    for r_comp in comp.chain:
                                        if isinstance(r_comp, Image) and (r_comp.file or getattr(r_comp, "url", None)):
                                            img_src = r_comp.file or r_comp.url
                                            if not ImageCaptionUtils.get_cached_caption(img_src, platform_name, is_private, chat_id):
                                                await ImageCaptionUtils.generate_image_caption(
                                                    img_src,
                                                    platform_name=platform_name,
                                                    is_private=is_private,
                                                    chat_id=chat_id,
                                        )
        except Exception as e:
            logger.warning(f"引用图片转述预处理失败: {e}")
        
        all_msgs = []
        try:
            all_msgs = HistoryStorage.get_history(platform_name, is_private, chat_id)
        except Exception as e:
            logger.error(f"获取历史失败: {e}")

        # 调用新的双时区时间生成逻辑
        time_prompt = await LLMUtils._get_time_prompt(all_msgs, user_id, config)

        system_parts = []
        
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

        instruction = "\n\n【规则】\n1. 你的名字在聊天记录中显示为 'Rosa'。\n2. 请勿重复自己的名字作为回复开头。"
        if config.get("read_air", False):
            instruction += "\n3. 若无需回复（如话题与你无关），请严格输出 <NO_RESPONSE>。"
        else:
            instruction += "\n3. 请直接生成回复。"
        system_parts.append(instruction)

        # 预取图片用于上传与提示
        image_processing_cfg = config.get("image_processing", {})
        use_image_caption = bool(image_processing_cfg.get("use_image_caption", False))
        image_urls = []
        image_notes = []
        img_check_count = image_processing_cfg.get("image_count", 0)
        
        if img_check_count > 0 and all_msgs:
            check_range = 15 
            msgs_to_check = all_msgs[-check_range:] if len(all_msgs) > check_range else all_msgs
            for msg in reversed(msgs_to_check):
                if hasattr(msg, "message") and msg.message:
                    for comp in msg.message:
                        if isinstance(comp, Image) and (comp.file or getattr(comp, "url", None)):
                            img_src = comp.file or comp.url
                            image_urls.append(img_src)
                            note_idx = len(image_urls)
                            basename = img_src
                            if isinstance(img_src, str):
                                basename = os.path.basename(img_src)
                            note_name = basename or f"img_{note_idx}"
                            image_notes.append(f"图片{note_idx}({note_name})")
                            if len(image_urls) >= img_check_count: break
                if len(image_urls) >= img_check_count: break

        uploaded_images = set()
        for img in image_urls:
            if not img:
                continue
            img_str = str(img)
            uploaded_images.add(img_str)
            if img_str.startswith("file:///"):
                uploaded_images.add(img_str[8:])

        final_system_prompt = "\n\n".join(system_parts)

        history_str = ""
        msg_limit = config.get("group_msg_history", 10)
        bot_history_keep = config.get("bot_reply_history_count", 3)
        
        if all_msgs:
            tail_msgs = all_msgs[-msg_limit:] if len(all_msgs) > msg_limit else all_msgs
            
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
            
            merged_list.sort(key=lambda x: getattr(x, 'timestamp', 0))
            
            fmt = await MessageUtils.format_history_for_llm(
                merged_list,
                max_messages=999,
                image_caption=use_image_caption,
                platform_name=platform_name,
                is_private=is_private,
                chat_id=str(chat_id),
                uploaded_images=uploaded_images,
            )
            if fmt:
                history_str = "以下是最近的聊天记录：\n" + fmt
        else:
            history_str = "（暂无历史记录）"

        setattr(event, "_spectre_history", history_str)
        try:
            event.set_extra("spectre_history", history_str)
            event.set_extra("spectre_request", True)
        except Exception:
            pass

        current_msg = event.get_message_outline() or "[非文本消息]"

        if image_urls:
            notes = ", ".join(image_notes)
            final_system_prompt += (
                f"\n\n[ImageRefs]: 最近上传的图片（按顺序传给模型）: {notes}。"
                " 若需描述，请依赖视觉输入；请勿臆造未提供的图片内容。"
            )

        func_tools_mgr = context.get_llm_tool_manager() if config.get("use_func_tool", False) else None

        return event.request_llm(
            prompt=current_msg, 
            func_tool_manager=func_tools_mgr,
            contexts=contexts,
            system_prompt=final_system_prompt, 
            image_urls=image_urls, 
        )
