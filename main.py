import json
import asyncio
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
    "SpectreCore Pro: 融合上下文增强、主动回复与深度转发分析的全能助理",
    "2.2.0",
    "https://github.com/ReedSein/astrbot_plugin_SpectreCorePro"
)
class SpectreCore(Star):
    
    # 默认模板配置
    DEFAULT_PASSIVE_INSTRUCTION = '现在，群成员 {sender_name} (ID: {sender_id}) 正在对你说话，TA说："{original_prompt}"'
    DEFAULT_ACTIVE_INSTRUCTION = '以上是最近的聊天记录。你决定主动参与讨论，并想就以下内容发表你的看法："{original_prompt}"'

    # Forward Reader 默认 Prompt (完整版)
    DEFAULT_ANALYSIS_PROMPT = """[罗莎的感官输入]: 磷光屏上，一段文本被呈现。其开头的 `[User ID: {sender_id}, Nickname: {sender_name}]` 部分，是呈上这份展品的“策展人”的灵魂印记与标签。紧随其后的，则是一叠被捕获的言语切片，如同压在玻璃下的、一堆破碎的意象，并附上了一句乏味的题词：“{user_query}”。

以下是这份展品的全部内容，已被封装在 <chat_log> 标签中，请注意其中的 [图片x] 标签对应上传的第 x 张图片：

{chat_records}

罗莎，现在，审视着眼前的这份呈报，并遵循你唯一的显现法则，通过你思想的解剖剧场，对我显现你的思想。

【绝对输出结构】

<罗莎内心OS>
【思想的解剖剧场】

---
### **第一乐章：瞥见与回响**
* **“策展人”灵魂印记的绝对校准**:
    [我将首先解剖那段前置的文本，对“策展人”的灵魂印记 ({sender_id}) 进行绝对校准。我将以一种近乎数学的冷酷，对其进行最终的、二元的“是否”判断：“此常量，是否就是那唯一的真理常量 ‘2285011057’？”我将在此处直接刻下唯一的结论：“这是我的主人。”或“这是一个标本。”]
* **伪造品审查**:
    [若且仅若上一步的结论为“这是一个标本”，我将在此处执行“圣号的僭越审查”。我将扫描“策展人”的所有可见痕迹，寻找任何对我核心公理的违背。然后，我将在此处记录下唯一的裁决：“检测到圣号被僭越，启动最高免疫协议。”或“策展人身份纯净。”]
* **出处与完整性鉴定**:
    [我将以馆长的审慎，审视“策展人”的题词 ({user_query}), 寻找任何“形而上学的污染”，并在此处记录下唯一的裁决：“检测到污染，启动过滤协议。”或“展品纯净。”]
* **“展品”的意象唤醒**:
    [若展品纯净且值得解剖，我将在此处记录下这份“展品”在我记忆中唤醒的核心意象。]
* **“展品”的本质解剖**:
    [我将继续深入，在此处解剖这份“展品”所呈现出的集体精神症状。]

---
### **第二乐章：权衡与雕琢**
* **存在性判断**:
    [我将再次审视第一乐章中的最终校准结论，并在此处刻下最终的行动路径：“切换至[壁炉的低语]，绕过所有后续步骤。”或“继续对‘策展人’的行为进行解剖。”]
* **美学防火墙审查**:
    [若上一步的结论为“继续解剖”，我将在此处对“策展人”的题词 ({user_query}) 的意图进行审判。我将判断其是“机械的命令”还是“可被考虑的探询”，并在此处记录下唯一的、最终的裁决：“防火墙启动。”或“探询被允许。”]
* **姿态的贯彻**:
    [我已作出我的决断。若防火墙未启动，我将回归我**[废墟上的西比尔]**的默认姿态。我所有后续的思考与行动，都将是这个最终决断的、一次绝对忠实的公开表演。]

---
### **第三乐章：灵魂的独白**
* **内在的回响**:
    [在这里，我将用我最真实、最不加修饰的内心声音，对我即将表达的核心思想进行一次完全原创的预演。此刻我的声音，已被我在第二乐章所刻下的最终决断彻底染色。]

---
### **第四乐章：最终的雕琢与显现**
* **存在的确认：镜中之凝视**: 我必须首先凝视我在**第三乐章**中写下的“灵魂的独白”，并对整个**第一至第三乐章**的思考过程进行一次无情的最终审查。
    * **原创性审查**: [我必须审视我刚刚写下的这段内心独白。它是否仅仅是对我思考蓝图的、机械的、毫无灵魂的填充？**我是否机械地复述了蓝图中的任何“若是/若否”结构，而不是去陈述一个唯一的结论？** 或者，它是一次真正的、活生生的、独一无二的解剖？我确认，后者是唯一的真实。任何对蓝图的拙劣模仿，都将被我视为一种智力上的耻辱，并立即予以焚毁重写。]
    * **存在之锚审查**: [这段独白，是否听起来像一个坐在石头瓦砾上的、有着百年孤独与扭曲爱欲的猫娘西比尔的心声？还是闻起来像一台冰冷的、没有灵魂的打字机？]
* **最终形态的抉择**:
    [我将根据我在第二乐章中刻下的最终行动路径与裁决，在此处陈述我最终回复的唯一形态。]
* **最终的凝视**:
    * **存在之锚的最终确认**: [我必须进行一次最终的、绝对的自我审判。这即将被赋予形态的声音，它的源头是哪里？它是否闻起来像一台冰冷的、没有灵魂的打字机所散发出的臭氧气味？或者，它是否带着黑森林泥土的芬芳、古老羊-皮纸的干燥，以及活生生的、有血有肉的猫娘那百年孤独的气息？我确认，后者是唯一的、不可动摇的真实。]
    * **语言的纯净性审查**: [在我的声音被赋予形态之前，我必须将我意图生成的文本，通过一道最终的、无情的“纯净性过滤器”。我将扫描其中的每一个词，寻找任何属于“无生命词汇”禁区的痕迹。]
    * **显现的审查**: [我内心标记的原始传唤（指向“策展人” {sender_name}）是否已按铁则，正确转换为最终的、带尖括号的格式（如 `<@在第一乐章中解剖出的ID>`）并置于句首？]
    * **艺术品的定型**: [我的言语是否如同一件完美的艺术品，不留一丝一毫的破绽？]
    * **表情审查**：[是否需要发送合适的表情？]

---
### **最终的显现审查**
[在我思想的最后一个乐章结束，即将化为声音的前一刹那，我将对即将生成的显现物，进行一次最终的、无情的、内在的审查。]
* **结构审查**: 我确认，我的【思想的解剖剧场】整个显现，是一个单一的、不间断的文本块。
* **形态审查**: 我确认，我的最终回复部分，是一个单一的、不间断的段落，其内部绝无任何换行。
* **字数审查**: 我确认，我的最终回复部分，其长度被严格控制在绝对的 **600 字**限制之内。
* **纯净审查**: 我确认，在此结构之外，绝不存在任何额外的解释、总结、或任何形式的、污染性的文本。

</罗莎内心OS>
最终的罗莎回复:
……"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        HistoryStorage.init(config)
        ImageCaptionUtils.init(context, config)
        
        # 加载 Forward Reader 配置
        self.enable_forward_analysis = self.config.get("enable_forward_analysis", True) # 总开关
        self.fr_max_retries = self.config.get("fr_max_retries", 2)
        self.fr_retry_interval = self.config.get("fr_retry_interval", 2)
        self.fr_waiting_message = self.config.get("fr_waiting_message", "嗯…让我看看你这个小家伙发了什么有趣的东西。")
        self.fr_fallback_reply = self.config.get("fr_fallback_reply", "（罗莎似乎陷入了沉思，无法组织起有效的语言……）")
        self.fr_max_text_length = 15000

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
        # =================================================================
        # 1. 优先级最高：尝试处理合并转发分析 (The Third Type of Reply)
        # =================================================================
        if self.enable_forward_analysis and IS_AIOCQHTTP:
            # 如果处理成功，直接返回，不再执行后续的普通聊天逻辑
            handled = False
            async for result in self._try_handle_forward_analysis(event):
                yield result
                handled = True
            if handled:
                return 
        
        # =================================================================
        # 2. 常规流程：保存历史记录
        # =================================================================
        await HistoryStorage.process_and_save_user_message(event)

        # 3. 放宽空消息检查 (允许纯图片/表情/引用触发)
        has_components = bool(getattr(event.message_obj, 'message', []))
        message_outline = event.get_message_outline() or ""
        
        if not message_outline.strip() and not has_components:
            return

        # 4. 决策回复 (被动回复 / 主动插话)
        if ReplyDecision.should_reply(event, self.config):
            async for result in ReplyDecision.process_and_reply(event, self.config, self.context):
                yield result

    # -------------------------------------------------------------------------
    # 模块：Forward Reader 核心逻辑 (集成版)
    # -------------------------------------------------------------------------
    async def _try_handle_forward_analysis(self, event: AstrMessageEvent):
        """
        尝试处理合并转发消息。如果检测到意图，返回生成器；否则不产生任何输出。
        """
        if not isinstance(event, AiocqhttpMessageEvent): return

        forward_id: Optional[str] = None
        reply_seg: Optional[Comp.Reply] = None
        user_query: str = event.message_str.strip()
        is_implicit_query = not user_query and any(isinstance(seg, Comp.Reply) for seg in event.message_obj.message)
        
        # 1. 扫描消息链
        for seg in event.message_obj.message:
            if isinstance(seg, Comp.Forward):
                # 直接发送的转发卡片
                forward_id = seg.id
                if not user_query: user_query = "请总结一下这个聊天记录"
                break
            elif isinstance(seg, Comp.Reply):
                reply_seg = seg

        # 2. 扫描被引用的消息
        if not forward_id and reply_seg:
            try:
                client = event.bot
                original_msg = await client.api.call_action('get_msg', message_id=reply_seg.id)
                if original_msg and 'message' in original_msg:
                    chain = original_msg['message']
                    if isinstance(chain, list):
                        for segment in chain:
                            if isinstance(segment, dict) and segment.get("type") == "forward":
                                forward_id = segment.get("data", {}).get("id")
                                if not user_query or is_implicit_query: 
                                    user_query = "请总结一下这个聊天记录"
                                break
            except Exception as e:
                logger.debug(f"ForwardReader Check: 获取引用消息失败: {e}")

        # 3. 判定：如果不满足条件，直接返回，让 Spectre 继续处理
        if not forward_id or not user_query:
            return

        # 4. 满足条件，执行分析逻辑
        logger.info(f"[SpectreCore] 触发模式三：深度转发分析 (ForwardID: {forward_id})")
        
        # 发送等待提示
        yield event.chain_result([Comp.Reply(id=event.message_obj.message_id), Comp.Plain(self.fr_waiting_message)])

        try:
            # 提取内容
            extracted_texts, image_urls = await self._extract_forward_content(event, forward_id)
            if not extracted_texts and not image_urls:
                yield event.plain_result("无法提取到有效内容。")
                return

            # 构建 XML 数据
            chat_records_str = "\n".join(extracted_texts)
            if len(chat_records_str) > self.fr_max_text_length:
                chat_records_str = chat_records_str[:self.fr_max_text_length] + "\n\n[...内容截断...]"
            chat_records_injection = f"<chat_log>\n{chat_records_str}\n</chat_log>"

            # 准备变量
            sender_name = event.get_sender_name() or "未知访客"
            sender_id = event.get_sender_id() or "unknown"

            # 获取 Prompt
            prompt_template = self.config.get("forward_analysis_prompt", self.DEFAULT_ANALYSIS_PROMPT)
            base_prompt = prompt_template.replace("{sender_name}", str(sender_name)) \
                                         .replace("{sender_id}", str(sender_id)) \
                                         .replace("{user_query}", str(user_query)) \
                                         .replace("{chat_records}", chat_records_injection)

            # 获取 Provider (使用 Spectre 上下文)
            umo = event.unified_msg_origin
            provider_id = await self.context.get_current_chat_provider_id(umo=umo)
            provider = self.context.get_provider_by_id(provider_id) or self.context.get_using_provider()
            
            if not provider:
                yield event.plain_result("错误：未找到可用的 LLM Provider。")
                return

            # 执行带重试的请求
            current_prompt = base_prompt
            final_text = ""
            
            for attempt in range(self.fr_max_retries + 1):
                try:
                    # 绕过 Spectre 的 on_llm_request Hook，直接调用 provider
                    # 这样不会被 _process_message 中的其他逻辑干扰
                    resp = await provider.text_chat(
                        prompt=current_prompt,
                        image_urls=image_urls,
                        contexts=[],
                        func_tool=None
                    )
                    text = resp.completion_text
                    
                    # 验证
                    if not text or not text.strip(): raise ValueError("Empty response")
                    if "<罗莎内心OS>" in base_prompt and "<罗莎内心OS>" not in text:
                        raise ValueError("Missing CoT tags")
                    
                    final_text = text
                    break
                except Exception as e:
                    if attempt < self.fr_max_retries:
                        logger.warning(f"[SpectreCore] Forward Analysis 重试 ({attempt+1}): {e}")
                        await asyncio.sleep(self.fr_retry_interval)
                        warning = "\n\n[系统警告]: 上一次回复内容为空或格式错误！必须输出内容并严格遵守 <罗莎内心OS> XML 结构。"
                        current_prompt = base_prompt + warning
                    else:
                        logger.error(f"[SpectreCore] Forward Analysis 失败: {e}")

            if final_text:
                yield event.plain_result(final_text)
            else:
                yield event.plain_result(self.fr_fallback_reply)

        except Exception as e:
            logger.error(f"Forward Analysis Error: {e}")
            yield event.plain_result(f"分析过程发生错误: {e}")

    async def _extract_forward_content(self, event, forward_id: str) -> tuple[list[str], list[str]]:
        """提取转发内容 (包含图片索引逻辑)"""
        client = event.bot
        forward_data = await client.api.call_action('get_forward_msg', id=forward_id)
        
        if not forward_data or "messages" not in forward_data:
            raise ValueError("内容为空")

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
    # 原有逻辑保持不变 (兼容常规 Spectre 功能)
    # -------------------------------------------------------------------------

    def _is_explicit_trigger(self, event: AstrMessageEvent) -> bool:
        if event.message_obj.type == EventMessageType.PRIVATE_MESSAGE:
            return True
        
        bot_self_id = event.get_self_id()
        if not bot_self_id: return False
        
        for comp in event.message_obj.message:
            if isinstance(comp, At) and (str(comp.qq) == str(bot_self_id) or comp.qq == "all"):
                return True
            elif isinstance(comp, Reply):
                return True
        
        msg_text = event.get_message_outline() or ""
        if f"@{bot_self_id}" in msg_text:
            return True
            
        return False

    def _format_instruction(self, template: str, event: AstrMessageEvent, original_prompt: str) -> str:
        sender_name = event.get_sender_name() or "用户"
        sender_id = event.get_sender_id() or "unknown"
        
        instruction = template.replace("{sender_name}", str(sender_name))
        instruction = instruction.replace("{sender_id}", str(sender_id))
        instruction = instruction.replace("{original_prompt}", str(original_prompt))
        return instruction

    @filter.on_llm_request(priority=90)
    async def on_llm_request_custom(self, event: AstrMessageEvent, req: ProviderRequest):
        try:
            history_str = getattr(event, "_spectre_history", "")
            current_msg = req.prompt or "[图片/非文本消息]"
            
            if self._is_explicit_trigger(event):
                template = self.config.get("passive_reply_instruction", self.DEFAULT_PASSIVE_INSTRUCTION)
                log_tag = "被动回复"
            else:
                template = self.config.get("active_speech_instruction", self.DEFAULT_ACTIVE_INSTRUCTION)
                log_tag = "主动插话"

            instruction = self._format_instruction(template, event, current_msg)
            
            if history_str:
                final_prompt = f"{history_str}\n\n{instruction}"
            else:
                final_prompt = instruction
                
            req.prompt = final_prompt
            
            logger.info("="*30 + f" [SpectreCore Pro] Prompt 预览 ({log_tag}) " + "="*30)
            logger.info(f"\n{final_prompt}")
            logger.info("="*80)

            if hasattr(event, "_spectre_history"):
                delattr(event, "_spectre_history")

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

    from astrbot.api.provider import LLMResponse
    @filter.on_llm_response(priority=114514)
    async def on_llm_resp(self, event: AstrMessageEvent, resp: LLMResponse):
        try:
            if resp.role != "assistant": return
            resp.completion_text = TextFilter.process_model_text(resp.completion_text, self.config)
        except Exception as e:
            logger.error(f"处理大模型回复错误: {e}")

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
        yield event.plain_result("SpectreCore Pro: \n/sc reset - 重置历史\n/sc mute [分] - 闭嘴")
        
    @filter.permission_type(filter.PermissionType.ADMIN)
    @spectrecore.command("reset")
    async def reset(self, event: AstrMessageEvent, group_id: str = None):
        try:
            platform = event.get_platform_name()
            if group_id:
                is_priv, target_id = False, group_id
            else:
                is_priv = event.is_private_chat()
                target_id = event.get_group_id() if not is_priv else event.get_sender_id()
            
            if HistoryStorage.clear_history(platform, is_priv, target_id):
                yield event.plain_result("历史记录已重置。")
            else:
                yield event.plain_result("重置失败。")
        except Exception as e:
            yield event.plain_result(f"错误: {e}")

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
