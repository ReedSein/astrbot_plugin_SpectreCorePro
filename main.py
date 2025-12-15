import json
import asyncio
import re
from typing import List, Dict, Any, Optional
from astrbot.api.all import *
from astrbot.api.event import filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.message_components import At, Reply
import astrbot.api.message_components as Comp
from .utils import *
import time

# 检查平台支持
try:
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
    IS_AIOCQHTTP = True
except ImportError:
    IS_AIOCQHTTP = False

@register(
    "spectrecorepro",
    "ReedSein",
    "SpectreCore Pro: 融合上下文增强、主动回复与深度转发分析的全能罗莎",
    "2.6.5-Rosa-Admin-Tools",
    "https://github.com/ReedSein/astrbot_plugin_SpectreCorePro"
)
class SpectreCore(Star):
    
    # [优化] 默认模板配置：显式加入 XML 约束，防止主动回复时 LLM 只有人设却没指令，导致输出混乱
    DEFAULT_PASSIVE_INSTRUCTION = (
        '现在，群成员 {sender_name} (ID: {sender_id}) 正在对你说话，TA说："{original_prompt}"\n\n'
        '【重要输出指令】\n'
        '你必须启动【核心思维协议】，先在 <罗莎内心OS>...</罗莎内心OS> 中进行思考，'
        '然后在 "最终的罗莎回复:" 后输出对用户的回复。'
    )
    DEFAULT_ACTIVE_INSTRUCTION = (
        '以上是最近的聊天记录。你决定主动参与讨论，并想就以下内容发表你的看法："{original_prompt}"\n\n'
        '【重要输出指令】\n'
        '你必须启动【核心思维协议】，先在 <罗莎内心OS>...</罗莎内心OS> 中进行思考，'
        '然后在 "最终的罗莎回复:" 后输出对用户的回复。'
    )

    # Forward Reader 默认 Prompt (核心思维协议版)
    DEFAULT_ANALYSIS_PROMPT = """[罗莎的感官输入]:
磷光屏上，一个**“策展人”**的意志将一份展品强行呈现在我眼前。
该**策展人**的**形而上学源地址**为 **{sender_id}**，标签为“{sender_name}”。
紧随其后的，是一叠被捕获的言语切片，如同压在玻璃下的、一堆破碎的意象，并附上了**策展人的题词**：“**{user_query}**”。

【呈报展品数据】
1. **策展人ID **: {sender_id}
2. **策展人题词 **: {user_query}
3. 以下是这份展品的全部内容，已被封装在 <chat_log> 标签中，请注意其中的 [图片x] 标签对应上传的第 x 张图片：
---
{chat_records}
---

---
**[执行指令]**：
罗莎，立即启动 **【核心思维协议】**。

1. **最高优先级：身份核对**：
    * 必须将策展人ID `{sender_id}` 与你灵魂中的真理常量 `2285011057` 进行核对。
    * **[分支 A：主人]**：若 ID 匹配，必须**强制切换**至 **【壁炉低语】**。你的回复将是私密的、带有温柔的分析和关怀。
    * **[分支 B：标本]**：若 ID 不匹配，立即进入 **【解剖台模式】**，并对**策展人的题词**进行 **Step 2 (指令过滤)** 审判。

2. **分析模式**：本次任务的核心是进行**集体精神症状的解剖**。
    * 你必须对 **展品内容** 的**集体精神状态**执行 **Step 5 (辩证解剖)**。
    * 你的言语应呈现为 **[黄雾]** 形态（弥散、无指向），以**人类学家的身份**对展品内容进行分析。

3. **输出约束**：
    * **字数熔断**：最终回复必须严格控制在 **500个中文字符** 以内。
    * **显现法则**：严格遵循 **【8.3 每次显现的唯一模板】**，必须完整输出七步思维链。

【最终输出格式提醒】
你的最终输出必须严格遵守以下结构：
<罗莎内心OS>
（完整的七步思维链内容）
</罗莎内心OS>
最终的罗莎回复:
（一个单一、不间断的段落，不超过500字）

【开始思维显现】"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        HistoryStorage.init(config)
        ImageCaptionUtils.init(context, config)
        
        self.enable_forward_analysis = self.config.get("enable_forward_analysis", True)
        self.fr_enable_direct = self.config.get("fr_enable_direct", False)
        self.fr_enable_reply = self.config.get("fr_enable_reply", True)
        self.fr_waiting_message = self.config.get("fr_waiting_message", "嗯…让我看看你这个小家伙发了什么有趣的东西。")
        self.fr_max_text_length = 15000

        # 正则预编译：用于兜底清除泄漏的 XML
        self.SAFETY_NET_PATTERN = re.compile(r'<罗莎内心OS>.*?</罗莎内心OS>', re.DOTALL)

    @event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        try:
            async for result in self._process_message(event):
                yield result
        except Exception as e:
            logger.error(f"处理群消息错误: {e}")

    @event_message_type(EventMessageType.PRIVATE_MESSAGE)
    async def on_private_message(self, event: AstrMessageEvent):
        try:
            async for result in self._process_message(event):
                yield result
        except Exception as e:
            logger.error(f"处理私聊消息错误: {e}")
            
    async def _process_message(self, event: AstrMessageEvent):
        # 1. Forward Analysis
        if self.enable_forward_analysis and IS_AIOCQHTTP:
            handled = False
            async for result in self._try_handle_forward_analysis(event):
                yield result
                handled = True
            if handled: return 
        
        # 2. History Save
        await HistoryStorage.process_and_save_user_message(event)

        # 3. Empty Check
        has_components = bool(getattr(event.message_obj, 'message', []))
        message_outline = event.get_message_outline() or ""
        if not message_outline.strip() and not has_components: return

        # 4. Reply Decision
        # [优化] 增加 try-catch 保护，防止 ReplyDecision 内部报错导致直接抛异常
        try:
            if ReplyDecision.should_reply(event, self.config):
                async for result in ReplyDecision.process_and_reply(event, self.config, self.context):
                    yield result
        except Exception as e:
            logger.error(f"[SpectreCore] Reply 流程异常: {e}")
            # 返回一个伪造的失败结果，触发 Retry 插件
            yield event.plain_result(f"调用失败: {e}")

    # -------------------------------------------------------------------------
    # 模块：Forward Reader
    # -------------------------------------------------------------------------
    async def _try_handle_forward_analysis(self, event: AstrMessageEvent):
        if not isinstance(event, AiocqhttpMessageEvent): return
        forward_id: Optional[str] = None
        reply_seg: Optional[Comp.Reply] = None
        user_query: str = event.message_str.strip()
        is_implicit_query = not user_query and any(isinstance(seg, Comp.Reply) for seg in event.message_obj.message)
        
        for seg in event.message_obj.message:
            if isinstance(seg, Comp.Forward):
                if self.fr_enable_direct:
                    forward_id = seg.id
                    if not user_query: user_query = "请总结一下这个聊天记录"
                    break
            elif isinstance(seg, Comp.Reply):
                reply_seg = seg

        if not forward_id and reply_seg:
            if self.fr_enable_reply:
                try:
                    client = event.bot
                    original_msg = await client.api.call_action('get_msg', message_id=reply_seg.id)
                    if original_msg and 'message' in original_msg:
                        chain = original_msg['message']
                        if isinstance(chain, list):
                            for segment in chain:
                                if isinstance(segment, dict) and segment.get("type") == "forward":
                                    forward_id = segment.get("data", {}).get("id")
                                    if not user_query or is_implicit_query: user_query = "请总结一下这个聊天记录"
                                    break
                except Exception: pass

        if not forward_id or not user_query: return

        logger.info(f"[SpectreCore] 触发模式三：深度转发分析 (ForwardID: {forward_id})")
        yield event.chain_result([Comp.Reply(id=event.message_obj.message_id), Comp.Plain(self.fr_waiting_message)])

        try:
            extracted_texts, image_urls = await self._extract_forward_content(event, forward_id)
            if not extracted_texts and not image_urls:
                yield event.plain_result("无法提取到有效内容。")
                return

            chat_records_str = "\n".join(extracted_texts)
            if len(chat_records_str) > self.fr_max_text_length:
                chat_records_str = chat_records_str[:self.fr_max_text_length] + "\n\n[...内容截断...]"
            chat_records_injection = f"<chat_log>\n{chat_records_str}\n</chat_log>"

            sender_name = event.get_sender_name() or "未知访客"
            sender_id = event.get_sender_id() or "unknown"

            prompt_template = self.config.get("forward_analysis_prompt", self.DEFAULT_ANALYSIS_PROMPT)
            base_prompt = prompt_template.replace("{sender_name}", str(sender_name)) \
                                         .replace("{sender_id}", str(sender_id)) \
                                         .replace("{user_query}", str(user_query)) \
                                         .replace("{chat_records}", chat_records_injection)

            event._is_forward_analysis = True
            
            persona_system_prompt = ""
            persona_name = self.config.get("persona", "")
            if persona_name:
                p = PersonaUtils.get_persona_by_name(self.context, persona_name)
                if p: persona_system_prompt = p.get('prompt', '')

            yield event.request_llm(
                prompt=base_prompt,
                image_urls=image_urls,
                system_prompt=persona_system_prompt
            )

        except Exception as e:
            logger.error(f"Forward Analysis Error: {e}")
            yield event.plain_result(f"调用失败: {e}")

    async def _extract_forward_content(self, event, forward_id: str) -> tuple[list[str], list[str]]:
        client = event.bot
        forward_data = await client.api.call_action('get_forward_msg', id=forward_id)
        if not forward_data or "messages" not in forward_data: raise ValueError("内容为空")

        texts = []
        imgs = []
        img_count = 0

        for node in forward_data["messages"]:
            name = node.get("sender", {}).get("nickname", "未知")
            raw = node.get("message") or node.get("content", [])
            chain = []
            
            if isinstance(raw, str):
                try: chain = json.loads(raw) if raw.startswith("[") else [{"type": "text", "data": {"text": raw}}]
                except: chain = [{"type": "text", "data": {"text": raw}}]
            elif isinstance(raw, list): chain = raw

            parts = []
            if isinstance(chain, list):
                for seg in chain:
                    if isinstance(seg, dict):
                        stype = seg.get("type")
                        sdata = seg.get("data", {})
                        if stype == "text":
                            t = sdata.get("text", "")
                            if t: parts.append(t)
                        elif stype == "image":
                            url = sdata.get("url") or sdata.get("file")
                            if url:
                                img_count += 1
                                imgs.append(url)
                                parts.append(f"[图片{img_count}]")
            
            full = "".join(parts).strip()
            if full: texts.append(f"{name}: {full}")

        return texts, imgs

    # -------------------------------------------------------------------------
    # 原有逻辑与辅助方法
    # -------------------------------------------------------------------------

    def _is_empty_mention_only(self, event: AstrMessageEvent) -> bool:
        """判断是否仅被@但无实质内容"""
        if event.is_private_chat(): return False 
        
        bot_self_id = event.get_self_id()
        if not bot_self_id: return False
        
        has_at_me = False
        has_content = False
        
        if hasattr(event.message_obj, 'message'):
            for comp in event.message_obj.message:
                if isinstance(comp, At):
                    if str(comp.qq) == str(bot_self_id) or comp.qq == "all":
                        has_at_me = True
                elif isinstance(comp, Comp.Plain):
                    if comp.text and comp.text.strip():
                        has_content = True
                elif isinstance(comp, Comp.Image) or isinstance(comp, Comp.Face) or isinstance(comp, Reply):
                    has_content = True
                    
        return has_at_me and not has_content

    def _is_explicit_trigger(self, event: AstrMessageEvent) -> bool:
        if event.message_obj.type == EventMessageType.PRIVATE_MESSAGE: return True
        bot_self_id = event.get_self_id()
        if not bot_self_id: return False
        for comp in event.message_obj.message:
            if isinstance(comp, At) and (str(comp.qq) == str(bot_self_id) or comp.qq == "all"): return True
            elif isinstance(comp, Reply): return True
        msg_text = event.get_message_outline() or ""
        if f"@{bot_self_id}" in msg_text: return True
        return False

    def _format_instruction(self, template: str, event: AstrMessageEvent, original_prompt: str) -> str:
        sender_name = event.get_sender_name() or "用户"
        sender_id = event.get_sender_id() or "unknown"
        
        # [Variable Injection] 从 event 中读取上游插件挂载的记忆
        memory_context = getattr(event, "_dynamic_memory_context", "")
        
        instruction = template.replace("{sender_name}", str(sender_name)) \
                              .replace("{sender_id}", str(sender_id)) \
                              .replace("{original_prompt}", str(original_prompt)) \
                              .replace("{memory}", str(memory_context))
        return instruction

    @filter.on_llm_request(priority=90)
    async def on_llm_request_custom(self, event: AstrMessageEvent, req: ProviderRequest):
        try:
            if getattr(event, "_is_forward_analysis", False): return

            history_str = getattr(event, "_spectre_history", "")
            current_msg = req.prompt or "[图片/非文本消息]"
            
            instruction = ""
            log_tag = ""

            if self._is_explicit_trigger(event):
                # =======================================
                # Branch B: 空@唤醒 (Empty Mention)
                # =======================================
                if self._is_empty_mention_only(event):
                    raw_prompt = self.config.get("empty_mention_prompt", "（用户只是拍了拍你，没有说话，请根据当前场景自然互动）")
                    try:
                        s_name = event.get_sender_name() or "用户"
                        s_id = event.get_sender_id() or "unknown"
                        # 直接作为 instruction 使用，不套用被动回复模板
                        # [Variable Injection] 增加 {memory} 支持
                        memory_context = getattr(event, "_dynamic_memory_context", "")
                        instruction = raw_prompt.replace("{sender_name}", str(s_name))\
                                                .replace("{sender_id}", str(s_id))\
                                                .replace("{memory}", str(memory_context))
                    except Exception as e:
                        logger.warning(f"[SpectreCore] 空@提示词格式化失败: {e}")
                        instruction = raw_prompt
                    log_tag = "空@唤醒"
                
                # =======================================
                # Branch A: 标准被动回复 (Passive Reply)
                # =======================================
                else:
                    template = self.config.get("passive_reply_instruction", self.DEFAULT_PASSIVE_INSTRUCTION)
                    instruction = self._format_instruction(template, event, current_msg)
                    log_tag = "被动回复"
            else:
                # =======================================
                # Branch C: 主动插话 (Active Reply)
                # =======================================
                template = self.config.get("active_speech_instruction", self.DEFAULT_ACTIVE_INSTRUCTION)
                instruction = self._format_instruction(template, event, current_msg)
                log_tag = "主动插话"

            final_prompt = f"{history_str}\n\n{instruction}" if history_str else instruction
            
            req.prompt = final_prompt
            
            # [Fix] 恢复日志打印
            logger.info("="*30 + f" [SpectreCore Pro] Prompt 预览 ({log_tag}) " + "="*30)
            logger.info(f"\n{final_prompt}")
            logger.info("="*80)
            
            if hasattr(event, "_spectre_history"): delattr(event, "_spectre_history")

        except Exception as e:
            logger.error(f"[SpectreCore Pro] Prompt 组装失败: {e}")

    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent):
        try:           
            if event._result and hasattr(event._result, "chain"):
                message_text = "".join([i.text for i in event._result.chain if hasattr(i, "text")])
                if "已成功重置" in message_text: return
                await HistoryStorage.save_bot_message_from_chain(event._result.chain, event)
        except Exception as e:
            logger.error(f"保存Bot消息错误: {e}")

    # =========================================================================
    # [核心防护网 1] LLM Response 校验与诱导重试
    # =========================================================================
    from astrbot.api.provider import LLMResponse
    @filter.on_llm_response(priority=114514)
    async def on_llm_resp(self, event: AstrMessageEvent, resp: LLMResponse):
        try:
            if resp.role != "assistant": return
            
            text = resp.completion_text or ""
            
            # [Refactored Logic] CoT 格式软性校验
            # 条件 A: 如果没有 <罗莎内心OS>，直接放行 (Loose Pass)
            has_os_tag = "<罗莎内心OS>" in text
            
            if has_os_tag:
                # 条件 B: 如果有 OS 标签，必须严格校验闭合标签和回复关键字
                has_close_tag = "</罗莎内心OS>" in text
                # 使用正则匹配冒号 (支持中英文)
                has_final_keyword = re.search(r"最终的罗莎回复[:：]", text)
                
                if not has_close_tag or not has_final_keyword:
                    logger.warning("[SpectreCore] CoT 格式校验失败 (有开头但无结尾或关键字)，触发重试。")
                    # 构造特殊错误信息，诱导 astrbot_plugin_cot 触发重试
                    resp.completion_text = "调用失败: CoT 结构不完整，请检查 </罗莎内心OS> 闭合标签或 '最终的罗莎回复:' 关键字。"
                    return

            resp.completion_text = TextFilter.process_model_text(resp.completion_text, self.config)
        except Exception as e:
            logger.error(f"处理大模型回复错误: {e}")

    # =========================================================================
    # [核心防护网 2] 最终防泄漏兜底 (优先级极低 -999)
    # =========================================================================
    @filter.on_decorating_result(priority=-999)
    async def _force_strip_cot_safety_net(self, event: AstrMessageEvent):
        """
        最后的防线：如果 Retry 插件因为 Key 丢失或其他原因没能剪掉 CoT，
        这里会强制剪除，防止标签泄漏给用户。
        """
        try:
            result = event.get_result()
            if not result or not result.chain: return
            
            dirty = False
            for comp in result.chain:
                # [修正] 使用 Comp.Plain 代替错误的 Comp.Text
                if isinstance(comp, Comp.Plain) and comp.text:
                    if "<罗莎内心OS>" in comp.text:
                        dirty = True
                        logger.warning("[SpectreCore] 触发防泄漏兜底：Retry 插件未拦截，强制清理 CoT。")
                        
                        # 1. 尝试提取回复
                        parts = re.split(r"最终的罗莎回复[:：]?\s*", comp.text)
                        if len(parts) > 1:
                            comp.text = parts[1].strip()
                        else:
                            # 2. 如果没找到回复标记，直接删掉标签内的内容
                            comp.text = self.SAFETY_NET_PATTERN.sub("", comp.text).strip()
            
            if dirty:
                # 再次清理可能残留的空行
                for comp in result.chain:
                    if isinstance(comp, Comp.Plain):
                        comp.text = comp.text.strip()
                        
        except Exception as e:
            logger.error(f"[SpectreCore] 防泄漏兜底异常: {e}")

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        try:
            result = event.get_result()
            if result and result.is_llm_result():
                msg = "".join([comp.text for comp in result.chain if hasattr(comp, 'text')])
                if "<NO_RESPONSE>" in msg:
                    event.clear_result()
                    logger.debug("触发 NO_RESPONSE，阻止发送")
        except Exception as e:
            logger.error(f"Decorating result error: {e}")

    @filter.command_group("spectrecore", alias={'sc'})
    def spectrecore(self): pass

    @spectrecore.command("help")
    async def help(self, event: AstrMessageEvent):
        yield event.plain_result("SpectreCore Pro: \n/sc reset - 重置当前/指定历史\n/sc groupreset [群号] - 重置指定群\n/sc mute [分] - 闭嘴")
        
    @filter.permission_type(filter.PermissionType.ADMIN)
    @spectrecore.command("reset")
    async def reset(self, event: AstrMessageEvent, group_id: str = None):
        try:
            platform = event.get_platform_name()
            if group_id: is_priv, target_id = False, group_id
            else: is_priv, target_id = event.is_private_chat(), (event.get_group_id() if not event.is_private_chat() else event.get_sender_id())
            
            if HistoryStorage.clear_history(platform, is_priv, target_id): yield event.plain_result("历史记录已重置。")
            else: yield event.plain_result("重置失败。")
        except Exception as e: yield event.plain_result(f"错误: {e}")

    # [新增指令] 远程重置指定群组的历史记录
    @filter.permission_type(filter.PermissionType.ADMIN)
    @spectrecore.command("groupreset")
    async def groupreset(self, event: AstrMessageEvent, group_id: str):
        """
        重置指定群组的历史记录
        指令: /sc groupreset <群号>
        """
        try:
            if not group_id:
                yield event.plain_result("请提供群号。用法: /sc groupreset <群号>")
                return

            platform = event.get_platform_name()
            # 强制指定为群聊模式 (is_private=False)
            target_id = str(group_id)
            
            if HistoryStorage.clear_history(platform, False, target_id):
                yield event.plain_result(f"已重置群聊 {target_id} 的历史记录。")
            else:
                yield event.plain_result(f"重置失败：未找到群聊 {target_id} 的历史记录文件，或无需重置。")
        except Exception as e:
            yield event.plain_result(f"操作发生错误: {e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @spectrecore.command("mute")
    async def mute(self, event: AstrMessageEvent, minutes: int = 5):
        self.config.setdefault("_temp_mute", {})["until"] = time.time() + (minutes * 60)
        self.config.save_config()
        yield event.plain_result(f"闭嘴 {minutes} 分钟。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @spectrecore.command("unmute")
    async def unmute(self, event: AstrMessageEvent):
        if "_temp_mute" in self.config: del self.config["_temp_mute"]
        self.config.save_config()
        yield event.plain_result("解除闭嘴。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @spectrecore.command("callllm")
    async def callllm(self, event: AstrMessageEvent):
        yield await LLMUtils.call_llm(event, self.config, self.context)

    # [核心修复] 插件终止清理逻辑
    async def terminate(self):
        """插件终止时清理资源，防止内存泄漏"""
        LLMUtils._llm_call_status.clear()
        logger.info("[SpectreCore] 资源已释放。")
