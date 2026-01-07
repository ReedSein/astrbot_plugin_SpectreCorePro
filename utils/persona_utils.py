from astrbot.api.all import *
from astrbot.api import sp
from astrbot.api.provider import Personality
from typing import List, Optional

class PersonaUtils:
    """
    人格信息工具类
    用于获取和管理AstrBot中的人格信息
    """
    
    @staticmethod
    def get_all_personas(context: Context) -> List[Personality]:
        """
        获取所有已加载的人格
        
        Args:
            context: Context对象
            
        Returns:
            所有已加载的人格列表
        """
        try:
            if hasattr(context, "persona_manager"):
                return list(getattr(context.persona_manager, "personas_v3", []))
            return list(getattr(context.provider_manager, "personas", []))
        except Exception as e:
            logger.error(f"获取所有人格失败: {e}")
            return []
    
    @staticmethod
    def get_default_persona(context: Context) -> Optional[str]:
        """
        获取默认人格的ID
        
        Args:
            context: Context对象
            
        Returns:
            默认人格的ID，如果获取失败则返回None
        """
        try:
            persona = context.persona_manager.selected_default_persona_v3
            return persona["name"] if persona else None
        except Exception as e:
            logger.error(f"获取默认人格失败: {e}")
            return None
    
    @staticmethod
    def get_persona_by_name(context: Context, persona_name: str) -> Optional[Personality]:
        """
        根据名称获取指定的人格
        
        Args:
            context: Context对象
            persona_name: 人格名称
            
        Returns:
            指定名称的人格对象，如果不存在则返回None
        """
        try:
            personas = getattr(context.persona_manager, "personas_v3", None)
            if personas is None:
                personas = getattr(context.provider_manager, "personas", [])
            for persona in personas:
                if persona.get("name") == persona_name:
                    return persona
            return None
        except Exception as e:
            logger.error(f"获取指定人格失败: {e}")
            return None

    @staticmethod
    async def resolve_persona_v3(
        context: Context,
        umo: str,
    ) -> Optional[Personality]:
        persona = None
        persona_id = ""

        try:
            session_cfg = await sp.get_async(
                scope="umo",
                scope_id=umo,
                key="session_service_config",
                default={},
            )
            persona_id = session_cfg.get("persona_id") or ""
        except Exception as e:
            logger.debug(f"获取 session_service_config 失败: {e}")

        if persona_id == "[%None]":
            return None

        if not persona_id:
            try:
                if hasattr(context, "persona_manager") and hasattr(
                    context.persona_manager, "get_default_persona_v3"
                ):
                    persona = await context.persona_manager.get_default_persona_v3(
                        umo=umo
                    )
                    persona_id = persona.get("name") if persona else ""
            except Exception as e:
                logger.debug(f"获取默认人格失败: {e}")

        if persona_id and not persona:
            persona = PersonaUtils.get_persona_by_name(context, persona_id)

        return persona
