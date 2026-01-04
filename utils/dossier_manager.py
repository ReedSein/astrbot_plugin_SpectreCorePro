import datetime
import json
import re
from typing import Any, Dict, List

from astrbot.api.all import logger


class UserDossierManager:
    """
    用户印象档案管理器
    - 负责创建/读取/更新用户档案
    - 提供提示词占位符渲染
    - 解析模型尾部的 <DOSSIER_UPDATE>...</DOSSIER_UPDATE> 标签并落库
    """

    STORAGE_KEY = "user_dossiers"
    TAG_PATTERN = re.compile(
        r"[<＜]\s*DOSSIER_UPDATE\s*[>＞](?P<data>.*?)[<＜]/\s*DOSSIER_UPDATE\s*[>＞]",
        re.IGNORECASE | re.DOTALL,
    )
    OPEN_TAG_PATTERN = re.compile(r"[<＜]\s*DOSSIER_UPDATE\b", re.IGNORECASE)
    CLOSE_TAG_PATTERN = re.compile(r"[<＜]/\s*DOSSIER_UPDATE\b", re.IGNORECASE)
    MAX_NAMES = 4
    MAX_RECENT = 5
    MAX_TABOO = 5
    MAX_WEAKNESS = 3

    def __init__(self, star):
        self.star = star

    @staticmethod
    def _today() -> str:
        return datetime.datetime.now().strftime("%Y-%m-%d")

    async def _load_store(self) -> Dict[str, Any]:
        try:
            data = await self.star.get_kv_data(self.STORAGE_KEY, {}) or {}
            if isinstance(data, dict):
                return data
        except Exception as exc:
            logger.error(f"加载用户档案失败: {exc}")
        return {}

    async def _save_store(self, store: Dict[str, Any]) -> None:
        try:
            await self.star.put_kv_data(self.STORAGE_KEY, store)
        except Exception as exc:
            logger.error(f"保存用户档案失败: {exc}")

    def _default_profile(self, user_id: str, user_name: str) -> Dict[str, Any]:
        recent_entry = f"[{self._today()}] 第一次互动。★"
        names = [user_name] if user_name else []
        return {
            "user_id": str(user_id),
            "names": names,
            "codename": "",
            "type_line": "未分类",
            "emotion": "冷漠",
            "positioning": "（未标注）",
            "commentary": "（暂无评语）",
            "recent": [recent_entry],
            "taboo": [],
            "weakness": [],
            "first_interaction": True,
        }

    def _ensure_name(self, profile: Dict[str, Any], name: str) -> bool:
        if not name:
            return False
        names = profile.get("names") or []
        name = str(name)
        if name in names:
            if names and names[0] != name:
                names = [name] + [n for n in names if n != name]
                profile["names"] = names[: self.MAX_NAMES]
                return True
            return False
        names = [name] + names
        profile["names"] = names[: self.MAX_NAMES]
        return True

    async def get_or_create_profile(self, user_id: str, user_name: str) -> Dict[str, Any]:
        store = await self._load_store()
        profile = store.get(user_id)
        changed = False
        if not profile:
            profile = self._default_profile(user_id, user_name)
            store[user_id] = profile
            changed = True
        else:
            changed = self._ensure_name(profile, user_name)
        if changed:
            await self._save_store(store)
        return profile

    def _format_numbered(self, entries: List[str], label: str) -> str:
        if not entries:
            return ""
        lines = []
        for idx, item in enumerate(entries, 1):
            lines.append(f"{label}{idx}: {item}")
        return "\n".join(lines)

    def format_profile(self, profile: Dict[str, Any], section: str | None = None) -> str:
        """将档案格式化为文本，可指定模块"""
        sec = (section or "all").lower()
        names = profile.get("names") or []
        lines = []

        def add(block: List[str]):
            lines.extend(block)

        if sec in ("all", "identity"):
            add([
                f"ID: {profile.get('user_id', '')}",
                f"名字: {' / '.join(names) if names else '未知'}",
                f"代号: {profile.get('codename') or '(未设定)'}",
            ])

        if sec in ("all", "category", "type"):
            add([
                f"类型: {profile.get('type_line') or '未分类'}",
                f"情感: {profile.get('emotion') or '冷漠'}",
            ])

        if sec in ("all", "impression"):
            add([
                f"定位: {profile.get('positioning') or '(未标注)'}",
                f"评语: {profile.get('commentary') or '(暂无评语)'}",
            ])

        if sec in ("all", "recent", "memory"):
            add([
                "最近互动:",
                self._format_numbered(profile.get("recent", []), "记忆") or "(暂无)",
            ])

        if sec in ("all", "taboo"):
            add([
                "禁忌清单:",
                self._format_numbered(profile.get("taboo", []), "禁忌") or "(暂无)",
            ])

        if sec in ("all", "weakness"):
            add([
                "弱点档案:",
                self._format_numbered(profile.get("weakness", []), "弱点") or "(暂无)",
            ])

        if profile.get("first_interaction"):
            add(["状态: 第一次互动"])

        return "\n".join(lines) if lines else "暂无档案"

    def build_prompt_variables(self, profile: Dict[str, Any]) -> Dict[str, str]:
        names = profile.get("names") or []
        name_str = " / ".join(names) if names else "未知用户"
        recent = self._format_numbered(profile.get("recent", [])[: self.MAX_RECENT], "记忆")
        taboos = self._format_numbered(profile.get("taboo", [])[: self.MAX_TABOO], "禁忌")
        weaknesses = self._format_numbered(profile.get("weakness", [])[: self.MAX_WEAKNESS], "弱点")

        return {
            "user_id": profile.get("user_id", ""),
            "user_name": name_str,
            "user_codename": profile.get("codename", "") or "（未设定）",
            "user_type": profile.get("type_line", "") or "未分类",
            "user_emotion": profile.get("emotion", "") or "冷漠",
            "user_positioning": profile.get("positioning", "") or "（未标注）",
            "user_commentary": profile.get("commentary", "") or "（暂无评语）",
            "user_recent": recent or "（暂无互动记录）",
            "user_taboo": taboos or "（暂无禁忌）",
            "user_weakness": weaknesses or "（暂无弱点记录）",
            "first_interaction": profile.get("first_interaction", False),
        }

    def build_prompt_block(self, variables: Dict[str, str]) -> str:
        lines = [
            "[用户印象档案快照]",
            f"ID: {variables.get('user_id', '')}",
            f"名字: {variables.get('user_name', '')}",
            f"代号: {variables.get('user_codename', '')}",
            f"类型演化: {variables.get('user_type', '')}",
            f"情感: {variables.get('user_emotion', '')}",
            f"定位: {variables.get('user_positioning', '')}",
            f"评语: {variables.get('user_commentary', '')}",
            "最近互动:",
            variables.get("user_recent", ""),
            "禁忌清单:",
            variables.get("user_taboo", ""),
            "弱点档案:",
            variables.get("user_weakness", ""),
        ]
        if variables.get("first_interaction"):
            lines.append("状态: 第一次互动（档案刚创建，可确认称呼与态度）")
        lines.append(
            "请在最终回复后追加一行 <DOSSIER_UPDATE>{...}</DOSSIER_UPDATE> 用于刷新档案，"
            "JSON 字段: codename, type, emotion, positioning, commentary, recent, taboo, weakness；"
            "recent/taboo/weakness 需为数组，缺失用 []，不要向用户解释这一行。"
        )
        return "\n".join([str(line) for line in lines if line is not None])

    def _normalize_list_input(self, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            parts = re.split(r"[\n;|]", value)
            return [p.strip() for p in parts if p.strip()]
        if isinstance(value, list):
            return [str(i).strip() for i in value if str(i).strip()]
        return []

    def _apply_index_replace(self, base: List[str], replace_map: Any, limit: int) -> tuple[List[str], bool]:
        if not isinstance(replace_map, dict):
            return base, False
        arr = list(base or [])
        changed = False
        for key, val in replace_map.items():
            idx_match = re.search(r"\d+", str(key))
            if not idx_match:
                continue
            idx = int(idx_match.group())
            if idx <= 0:
                continue
            text = str(val).strip()
            if not text:
                continue
            if idx <= len(arr):
                if arr[idx - 1] != text:
                    arr[idx - 1] = text
                    changed = True
            else:
                while len(arr) < idx - 1:
                    arr.append("")
                arr.append(text)
                changed = True
        arr = [i for i in arr if str(i).strip()]
        if len(arr) > limit:
            arr = arr[-limit:]
        return arr, changed

    def _merge_list(self, existing: List[str], new_value: Any, limit: int) -> List[str]:
        base = [str(i).strip() for i in (existing or []) if str(i).strip()]
        new_items = self._normalize_list_input(new_value)
        if not new_items:
            return base[:limit]
        merged = base + new_items
        merged = [i for i in merged if i]
        if len(merged) > limit:
            merged = merged[-limit:]
        return merged

    def _parse_update_block(self, raw: str) -> Dict[str, Any]:
        if not raw:
            return {}
        raw = raw.strip()
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        updates: Dict[str, Any] = {}
        for line in raw.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                updates[key.strip()] = value.strip()
        return updates

    @classmethod
    def has_incomplete_tag(cls, text: str) -> bool:
        if not text:
            return False
        if cls.TAG_PATTERN.search(text):
            return False
        return bool(cls.OPEN_TAG_PATTERN.search(text) or cls.CLOSE_TAG_PATTERN.search(text))

    def _merge_updates(self, profile: Dict[str, Any], updates: Dict[str, Any]) -> bool:
        if not updates:
            return False
        changed = False

        name_candidates = updates.get("name") or updates.get("names")
        if name_candidates:
            for alias in self._normalize_list_input(name_candidates):
                changed = self._ensure_name(profile, alias) or changed

        codename = updates.get("codename")
        if codename is not None and str(codename).strip():
            codename_val = str(codename).strip()
            if profile.get("codename") != codename_val:
                profile["codename"] = codename_val
                changed = True

        type_val = updates.get("type") or updates.get("type_line")
        if type_val is not None and str(type_val).strip():
            type_line = str(type_val).strip()
            if profile.get("type_line") != type_line:
                profile["type_line"] = type_line
                changed = True

        emotion = updates.get("emotion")
        if emotion is not None and str(emotion).strip():
            emo = str(emotion).strip()
            if profile.get("emotion") != emo:
                profile["emotion"] = emo
                changed = True

        positioning = updates.get("positioning")
        if positioning is not None and str(positioning).strip():
            pos = str(positioning).strip()
            if profile.get("positioning") != pos:
                profile["positioning"] = pos
                changed = True

        commentary = updates.get("commentary")
        if commentary is not None and str(commentary).strip():
            comment = str(commentary).strip()
            if profile.get("commentary") != comment:
                profile["commentary"] = comment
                changed = True

        recent, replaced = self._apply_index_replace(
            profile.get("recent", []),
            updates.get("recent_replace") or updates.get("recent_overwrite"),
            self.MAX_RECENT,
        )
        if replaced:
            profile["recent"] = recent
            changed = True
        recent = self._merge_list(profile.get("recent", []), updates.get("recent"), self.MAX_RECENT)
        if recent != profile.get("recent"):
            profile["recent"] = recent
            changed = True

        taboo = self._merge_list(profile.get("taboo", []), updates.get("taboo"), self.MAX_TABOO)
        if taboo != profile.get("taboo"):
            profile["taboo"] = taboo
            changed = True

        weakness = self._merge_list(profile.get("weakness", []), updates.get("weakness"), self.MAX_WEAKNESS)
        if weakness != profile.get("weakness"):
            profile["weakness"] = weakness
            changed = True

        return changed

    async def extract_and_update(self, user_id: str, user_name: str, text: str) -> tuple[str, bool, str]:
        if not text:
            return text, False, ""
        matches = list(self.TAG_PATTERN.finditer(text))
        if not matches:
            return text, False, ""

        store = await self._load_store()
        profile = store.get(user_id) or self._default_profile(user_id, user_name)
        before = json.loads(json.dumps(profile, ensure_ascii=False))
        changed = self._ensure_name(profile, user_name)

        for match in matches:
            updates = self._parse_update_block(match.group("data"))
            try:
                if self._merge_updates(profile, updates):
                    changed = True
            except Exception as exc:
                logger.error(f"更新用户档案失败: {exc}")

        cleaned_text = self.TAG_PATTERN.sub("", text).strip()
        if matches:
            profile["first_interaction"] = False
            changed = True

        if changed or user_id not in store:
            store[user_id] = profile
            await self._save_store(store)

        return cleaned_text, changed, self._diff_log(before, profile) if changed else ""

    @staticmethod
    def _diff_log(before: Dict[str, Any], after: Dict[str, Any]) -> str:
        diffs = []
        fields = ["names", "codename", "type_line", "emotion", "positioning", "commentary", "recent", "taboo", "weakness"]
        for f in fields:
            if before.get(f) != after.get(f):
                diffs.append(f"{f}: {before.get(f)} -> {after.get(f)}")
        return "; ".join(diffs)

    async def update_profile_field(
        self,
        user_id: str,
        user_name: str,
        field: str,
        value: str,
        index: int | None = None
    ) -> tuple[Dict[str, Any], bool]:
        """按字段更新档案；列表字段支持按索引替换"""
        store = await self._load_store()
        profile = store.get(user_id) or self._default_profile(user_id, user_name)
        before = json.loads(json.dumps(profile, ensure_ascii=False))
        changed = self._ensure_name(profile, user_name)
        f = field.lower()

        def set_text(key: str, text: str):
            nonlocal changed
            text = text.strip()
            if profile.get(key) != text:
                profile[key] = text
                changed = True

        if f in ("name", "names"):
            changed = self._ensure_name(profile, value) or changed
        elif f in ("codename", "alias"):
            set_text("codename", value)
        elif f in ("type", "type_line"):
            set_text("type_line", value)
        elif f == "emotion":
            set_text("emotion", value)
        elif f in ("positioning", "loc"):
            set_text("positioning", value)
        elif f in ("commentary", "comment"):
            set_text("commentary", value)
        elif f in ("recent", "memory"):
            if index and index > 0:
                arr, repl = self._apply_index_replace(profile.get("recent", []), {index: value}, self.MAX_RECENT)
                if repl:
                    profile["recent"] = arr
                    changed = True
            else:
                arr = self._merge_list(profile.get("recent", []), [value], self.MAX_RECENT)
                if arr != profile.get("recent"):
                    profile["recent"] = arr
                    changed = True
        elif f == "taboo":
            if index and index > 0:
                arr, repl = self._apply_index_replace(profile.get("taboo", []), {index: value}, self.MAX_TABOO)
                if repl:
                    profile["taboo"] = arr
                    changed = True
            else:
                arr = self._merge_list(profile.get("taboo", []), [value], self.MAX_TABOO)
                if arr != profile.get("taboo"):
                    profile["taboo"] = arr
                    changed = True
        elif f == "weakness":
            if index and index > 0:
                arr, repl = self._apply_index_replace(profile.get("weakness", []), {index: value}, self.MAX_WEAKNESS)
                if repl:
                    profile["weakness"] = arr
                    changed = True
            else:
                arr = self._merge_list(profile.get("weakness", []), [value], self.MAX_WEAKNESS)
                if arr != profile.get("weakness"):
                    profile["weakness"] = arr
                    changed = True

        if changed:
            profile["first_interaction"] = False
            store[user_id] = profile
            await self._save_store(store)

        return profile, changed, self._diff_log(before, profile) if changed else ""
