# -*- coding: utf-8 -*-
import json
import threading
import traceback
from datetime import datetime, timedelta

import requests
from flask import jsonify, render_template
from plugin import PluginModuleBase
from framework import F

from .model import ModelFetchLog, ModelFreeGameItem, ModelSetting
from . import scraper
from .setup import P


logger = P.logger
package_name = P.package_name
_fetch_lock = threading.Lock()
INDIEGALA_SEEN_SETTING_KEY = "indiegala_seen_games"
INDIEGALA_MAX_DISPLAY_DAYS = 14

SOURCE_LABELS = {
    "epic": "Epic",
    "steam": "Steam",
    "gog": "GOG",
    "indiegala": "IndieGala",
    "stove": "STOVE",
    "cheapshark": "CheapShark",
}
def _truthy(value):
    return str(value).lower() == "true"


def _enabled_sources():
    enabled = []
    for source in SOURCE_LABELS:
        if _truthy(ModelSetting.get(f"source_{source}_enabled")):
            enabled.append(source)
    return enabled


def _split_source_payload(results):
    grouped = {source: [] for source in SOURCE_LABELS}
    for source, items in (results or {}).items():
        normalized_source = "indiegala" if source == "indiegala_free" else source
        if normalized_source not in grouped:
            continue
        for item in items or []:
            is_free = bool(item.get("is_free_period")) or float(item.get("current_price") or 0) == 0
            if not is_free:
                continue
            if str(item.get("platform") or "") not in ["", normalized_source]:
                continue
            item["platform"] = normalized_source
            grouped[normalized_source].append(item)
    return grouped


def _parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _load_indiegala_seen():
    try:
        data = json.loads(ModelSetting.get(INDIEGALA_SEEN_SETTING_KEY) or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_indiegala_seen(seen):
    cutoff = datetime.now() - timedelta(days=180)
    compacted = {
        key: value for key, value in (seen or {}).items()
        if (_parse_dt(value) or cutoff) >= cutoff
    }
    ModelSetting.set(INDIEGALA_SEEN_SETTING_KEY, json.dumps(compacted, ensure_ascii=False))


def _filter_indiegala_items(items):
    now = datetime.now()
    cutoff = now - timedelta(days=INDIEGALA_MAX_DISPLAY_DAYS)
    seen = _load_indiegala_seen()
    existing = {
        row.external_id: row
        for row in F.db.session.query(ModelFreeGameItem).filter_by(platform="indiegala").all()
    }
    for external_id, row in existing.items():
        if external_id and external_id not in seen:
            first_seen = _parse_dt(row.created_time) or _parse_dt(row.updated_time) or now
            seen[external_id] = first_seen.isoformat()

    filtered = []
    for item in items or []:
        external_id = str(item.get("external_id") or "")
        if not external_id:
            continue
        first_seen = _parse_dt(seen.get(external_id))
        existing_row = existing.get(external_id)
        if first_seen:
            if first_seen < cutoff:
                continue
            if existing_row is None:
                continue
        else:
            seen[external_id] = now.isoformat()
        filtered.append(item)

    _save_indiegala_seen(seen)
    return filtered


def _format_game_line(game, bold_open, bold_close):
    prefix = "🆕 NEW " if game.get("is_new") else ""
    title = game.get("title") or "Unknown"
    platform = SOURCE_LABELS.get(game.get("platform"), game.get("platform"))
    store_url = game.get("store_url") or ""
    score = int(game.get("metacritic_score") or 0)
    line = f"{prefix}{bold_open}{title}{bold_close} ({platform})"
    if score > 0:
        line += f" · MC {score}"
    if store_url:
        line += f"\n{store_url}"
    return line


def _discord_send(webhook_url, games):
    lines = [_format_game_line(game, "**", "**") for game in games[:10]]
    content = "**무료 게임 알림**\n\n" + "\n\n".join(lines)
    requests.post(webhook_url, json={"content": content}, timeout=10).raise_for_status()


def _telegram_send(bot_token, chat_id, games):
    lines = [_format_game_line(game, "<b>", "</b>") for game in games[:10]]
    requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": "무료 게임 알림\n\n" + "\n\n".join(lines),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=10,
    ).raise_for_status()


def _is_epic_kr_unavailable(game):
    return game.get("platform") == "epic" and game.get("kr_available") is False


class Logic(PluginModuleBase):
    db_default = {
        "main_auto_start": "False",
        "main_interval": "0 */2 * * *",
        "auto_start": "False",
        "auto_interval": "0 */2 * * *",
        "notify_discord_webhook": "",
        "notify_telegram_bot_token": "",
        "notify_telegram_chat_id": "",
        "notify_enabled": "False",
        "notify_new_only": "False",
        "notify_exclude_epic_kr_unavailable": "False",
        "source_epic_enabled": "True",
        "source_steam_enabled": "True",
        "source_gog_enabled": "True",
        "source_indiegala_enabled": "True",
        "source_stove_enabled": "True",
        "source_cheapshark_enabled": "True",
        "last_fetch_started": "",
        "last_fetch_finished": "",
        "new_flags_initialized": "False",
        "notified_backfill_v2_done": "False",
        INDIEGALA_SEEN_SETTING_KEY: "{}",
    }

    def __init__(self, PM):
        super().__init__(PM, name="main", first_menu="setting", scheduler_desc="FreeGame fetch")

    def plugin_load(self):
        self._migrate_scheduler_settings()
        ModelFreeGameItem.ensure_schema()
        if not _truthy(ModelSetting.get("new_flags_initialized")):
            ModelFreeGameItem.reset_existing_new_flags()
            ModelSetting.set("new_flags_initialized", "True")
        if not _truthy(ModelSetting.get("notified_backfill_v2_done")):
            # 이전 마이그레이션이 SQLite의 ADD COLUMN DEFAULT 동작 때문에 기존 행을
            # notified=0으로 남겨둔 채 백필에 실패했던 결함을 1회 복구한다.
            ModelFreeGameItem.backfill_notified()
            ModelSetting.set("notified_backfill_v2_done", "True")

    def _migrate_scheduler_settings(self):
        legacy_interval = str(ModelSetting.get("auto_interval") or "").strip()
        if legacy_interval and str(ModelSetting.get("main_interval") or "").strip() in ["", "0 */2 * * *"]:
            ModelSetting.set("main_interval", legacy_interval)
        if _truthy(ModelSetting.get("auto_start")) and not _truthy(ModelSetting.get("main_auto_start")):
            ModelSetting.set("main_auto_start", "True")

    def process_menu(self, sub, req):
        arg = ModelSetting.to_dict()
        arg["package_name"] = package_name
        arg["scheduler"] = str(F.scheduler.is_include(self.get_scheduler_name()))
        arg["is_running"] = str(F.scheduler.is_running(self.get_scheduler_name()))
        arg["source_labels"] = SOURCE_LABELS
        arg["platform_counts"] = ModelFreeGameItem.get_platform_counts()
        if sub == "list":
            return render_template("ff_freegame_main_list.html", arg=arg)
        if sub == "log":
            return render_template("log.html", package=package_name)
        return render_template("ff_freegame_main_setting.html", arg=arg)

    def process_ajax(self, sub, req):
        try:
            if sub == "setting_save":
                ret, _ = ModelSetting.setting_save(req)
                self.setting_save_after(None)
                ret["ret"] = "success"
                return jsonify(ret)
            if sub == "scheduler_toggle":
                if req.form["scheduler"] == "true":
                    self.P.logic.scheduler_start(self.name)
                else:
                    self.P.logic.scheduler_stop(self.name)
                return jsonify({"ret": "success"})
            if sub == "execute_once":
                threading.Thread(target=self.scheduler_function, daemon=True).start()
                return jsonify({"ret": "success"})
            if sub == "web_list":
                ModelFreeGameItem.ensure_schema()
                return jsonify(ModelFreeGameItem.web_list(req))
            if sub == "platform_counts":
                return jsonify({"ret": "success", "data": ModelFreeGameItem.get_platform_counts()})
            return jsonify({"ret": "error", "log": f"unsupported ajax: {sub}"})
        except Exception as e:
            logger.error("Exception:%s", e)
            logger.error(traceback.format_exc())
            return jsonify({"ret": "error", "log": str(e)})

    def setting_save_after(self, change_list):
        if F.scheduler.is_include(self.get_scheduler_name()):
            self.P.logic.scheduler_stop(self.name)
            self.P.logic.scheduler_start(self.name)
        elif _truthy(ModelSetting.get("main_auto_start")):
            self.P.logic.scheduler_start(self.name)

    def scheduler_function(self):
        if not _fetch_lock.acquire(blocking=False):
            logger.info("FreeGame fetch skipped: already running")
            return
        try:
            with F.app.app_context():
                logger.info("FreeGame scheduled fetch started")
                ModelSetting.set("last_fetch_started", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                ModelFreeGameItem.ensure_schema()
                enabled_sources = set(_enabled_sources())
                results = scraper.fetch_all()
                grouped = _split_source_payload(results)
                grouped["indiegala"] = _filter_indiegala_items(grouped.get("indiegala", []))
                fresh_free_games = []

                for legacy_source in ["humble", "fanatical", "gmg", "directgames"]:
                    ModelFreeGameItem.replace_source_items(legacy_source, [])

                for source, items in grouped.items():
                    if source not in enabled_sources:
                        continue
                    existing_notified = {
                        row[0]: bool(row[1])
                        for row in F.db.session.query(
                            ModelFreeGameItem.external_id, ModelFreeGameItem.notified
                        )
                        .filter_by(platform=source)
                        .all()
                    }
                    if source == "epic":
                        # 진단용: external_id가 매일 안정적으로 유지되는지, DB에 이미
                        # notified=True로 남아있는지를 직접 비교할 수 있도록 남긴다.
                        logger.info("FreeGame epic diag existing=%s", existing_notified)
                        logger.info(
                            "FreeGame epic diag incoming=%s",
                            [str(item.get("external_id") or "") for item in items],
                        )
                    for item in items:
                        external_id = str(item.get("external_id") or "")
                        # 알림 대상 여부: DB에 처음 저장되는 게임뿐 아니라, 이미 저장은 됐지만
                        # (필터에 걸리거나 알림이 꺼져 있거나 전송에 실패해) 아직 알림을 못 받은
                        # 게임도 포함한다. existing_notified.get(id, False) == 이전에 실제로 알림이 나간 적이 있는가.
                        item["is_new"] = not existing_notified.get(external_id, False)
                    ModelFreeGameItem.replace_source_items(source, items)
                    ModelFetchLog(source, "ok", "", len(items)).save()
                    logger.info("FreeGame source=%s saved=%d", source, len(items))
                    fresh_free_games.extend(items)

                disabled_sources = [source for source in SOURCE_LABELS if source not in enabled_sources]
                if disabled_sources:
                    ModelFreeGameItem.delete_not_in_sources(enabled_sources)

                logger.info("FreeGame fetch completed: free_candidates=%d enabled_sources=%d", len(fresh_free_games), len(enabled_sources))
                ModelFetchLog("all", "ok", f"free_candidates={len(fresh_free_games)} enabled_sources={len(enabled_sources)}", len(fresh_free_games)).save()
                ModelSetting.set("last_fetch_finished", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                self._notify(fresh_free_games)
        except Exception as e:
            logger.error("Exception:%s", e)
            logger.error(traceback.format_exc())
            ModelFetchLog("all", "error", str(e), 0).save()
        finally:
            _fetch_lock.release()

    def _notify(self, games):
        if _truthy(ModelSetting.get("notify_enabled")) is False:
            return
        targets = list(games or [])
        if _truthy(ModelSetting.get("notify_new_only")):
            targets = [game for game in targets if game.get("is_new")]
        if _truthy(ModelSetting.get("notify_exclude_epic_kr_unavailable")):
            targets = [game for game in targets if not _is_epic_kr_unavailable(game)]
        if len(targets) == 0:
            return
        discord_webhook = ModelSetting.get("notify_discord_webhook")
        telegram_bot_token = ModelSetting.get("notify_telegram_bot_token")
        telegram_chat_id = ModelSetting.get("notify_telegram_chat_id")
        sent_ok = False
        if discord_webhook:
            try:
                _discord_send(discord_webhook, targets)
                sent_ok = True
                logger.info("FreeGame Discord notification sent: %d", min(len(targets), 10))
            except Exception as e:
                logger.error("FreeGame Discord notification failed: %s", e)
        if telegram_bot_token and telegram_chat_id:
            try:
                _telegram_send(telegram_bot_token, telegram_chat_id, targets)
                sent_ok = True
                logger.info("FreeGame Telegram notification sent: %d", min(len(targets), 10))
            except Exception as e:
                logger.error("FreeGame Telegram notification failed: %s", e)
        if sent_ok:
            # 메시지 본문에는 최대 10개까지만 실제로 언급되므로(_discord_send/_telegram_send의
            # games[:10]), 그만큼만 notified 처리한다. 11번째 이후는 다음 발송 때 다시 대상이 된다.
            ModelFreeGameItem.mark_notified(targets[:10])
