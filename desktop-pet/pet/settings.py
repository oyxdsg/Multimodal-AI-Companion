"""宠物设置入口与保存逻辑（PetSettingsMixin）。

设置 UI 统一在 settings_dialog.SettingsDialog 中构建（宠物 + AI 全部页面共用），
这里只保留：
  - _open_settings()      打开统一设置对话框
  - apply_pet_settings()  保存宠物相关设置 → 刷新实例状态 + 写 QSettings
"""

import json

import config
from ai.chat_service import shared as chat_service
from pet.settings_dialog import SettingsDialog


class PetSettingsMixin:
    """宠物设置对话框入口。由 PetWindow 继承。"""

    def _open_settings(self):
        """右键菜单的设置对话框：分页整理各项设置。"""
        if getattr(self, "_warm_timer", None):
            self._warm_timer.stop()
        self._open_settings_busy = True
        self._top_timer.stop()
        try:
            SettingsDialog(self, pet=self,
                           chat=getattr(self, "_chat_window", None)).exec()
        finally:
            self._top_timer.start(2000)
            self._open_settings_busy = False

    def apply_pet_settings(self, opts):
        """应用宠物相关设置：更新运行时状态并写 QSettings。"""
        # 皮肤切换：换动作素材 / 动作名 / 宠物名 / 动作标签词（人格不变）
        skin_id = opts.get("skin_id", "")
        if skin_id != str(self._store.value("skin_id", "") or ""):
            from pet import skins
            skin = skins.load_skin(skin_id) if skin_id else skins.BuiltinSkin()
            self._store.setValue("skin_id", skin_id)
            if skin.default_pet_name:
                persona = str(self._store.value("persona", "") or "") \
                    or config.DEFAULT_PERSONA
                # 宠物名跟随皮肤默认名（用户仍可在聊天设置中手动改）
                self._store.setValue("pet_name_" + persona, skin.default_pet_name)
            # 皮肤带专属 prompt 时，让下一条 AI 消息携带新设定（会话不重开）
            chat_service.reset_persona()
            self.reload_skin(skin)
        self.brightness = opts["brightness"]
        self.contrast = opts["contrast"]
        self.saturation = opts["saturation"]
        self._store.setValue("brightness", self.brightness)
        self._store.setValue("contrast", self.contrast)
        self._store.setValue("saturation", self.saturation)
        self._chatline_enabled = opts["chatline_enabled"]
        self._chatline_freq = opts["chatline_freq"]
        self._store.setValue("chatline_enabled", self._chatline_enabled)
        self._store.setValue("chatline_freq", self._chatline_freq)
        if self.state == self.STATE_IDLE:
            self._schedule_chatline()
        self._env_handler._env_enabled = opts["env_enabled"]
        self._env_handler._manual_city = opts["env_city"]
        self._store.setValue("env_enabled", self._env_handler._env_enabled)
        self._store.setValue("env_city", self._env_handler._manual_city)
        if self._env_handler._env_enabled:
            self._env_handler._last_weather_fetch = 0.0
            self._env_tick()
        self._news_handler._news_enabled = opts["news_enabled"]
        self._news_handler._news_feeds = opts["news_feeds"]
        self._news_handler._news_freq_min = opts["news_freq"]
        self._store.setValue("news_enabled", self._news_handler._news_enabled)
        self._store.setValue("news_feeds", json.dumps(self._news_handler._news_feeds))
        self._store.setValue("news_freq", self._news_handler._news_freq_min)
        if self._news_handler._news_enabled:
            self._news_handler._last_news_fetch = 0.0
            self._maybe_fetch_news()
        self._work_handler._work_enabled = opts["work_enabled"]
        self._work_handler._work_freq_min = opts["work_freq"]
        self._store.setValue("work_enabled", self._work_handler._work_enabled)
        self._store.setValue("work_freq", self._work_handler._work_freq_min)
        if not self._work_handler._work_enabled:
            self._work_handler._said_tips = []
        # 游戏联动类型（互斥）：关闭 / Minecraft / 修仙
        self._set_game_type(opts["game_type"])
        gh = self._game_handler
        gh._game_log_path = opts["game_log_path"]
        self._store.setValue("game_player_name", opts["game_player_name"])
        gh._game_freq = opts["game_freq"]
        gh._game_source = opts["game_source"]
        gh._game_normal_summary_min = opts["game_normal_summary"]
        gh._game_chat_respond = opts["game_chat_respond"]
        self._store.setValue("game_log_path", gh._game_log_path)
        self._store.setValue("game_freq", gh._game_freq)
        self._store.setValue("game_source", gh._game_source)
        self._store.setValue("game_normal_summary", gh._game_normal_summary_min)
        self._store.setValue("game_chat_respond", gh._game_chat_respond)
        # 女仆在场时隐藏桌宠窗口（只在游戏页勾选时生效）
        self._store.setValue("maid_hide_pet", bool(opts.get("maid_hide_pet", False)))
        if self._game_enabled:
            gh._game_files_state = {}
            gh._game_last_key = ""
            gh._game_last_adv = None
            gh._game_win_pos = {}
            gh._game_last_check = 0.0
        # 修仙游戏目录
        self._xiuxian_handler._xiuxian_game_dir = opts["xiuxian_game_dir"] \
            or config.XIUXIAN_GAME_DIR
        self._store.setValue("xiuxian_game_dir",
                             self._xiuxian_handler._xiuxian_game_dir)
        # 联动类型不是修仙时，停掉还在跑的修仙桥接
        if self._game_type != 2 and self._xiuxian_handler._xiuxian_proc is not None:
            self._cleanup_xiuxian()