# -*- coding: utf-8 -*-
import json
from datetime import datetime, timedelta

from sqlalchemy import desc
from sqlalchemy import text

from .setup import *


ModelSetting = P.ModelSetting


class ModelFreeGameItem(ModelBase):
    P = P
    __tablename__ = "ff_freegame_item"
    __bind_key__ = P.package_name

    id = db.Column(db.Integer, primary_key=True)
    created_time = db.Column(db.DateTime)
    updated_time = db.Column(db.DateTime)
    external_id = db.Column(db.String)
    platform = db.Column(db.String)
    title = db.Column(db.String)
    image_url = db.Column(db.String)
    store_url = db.Column(db.String)
    original_price = db.Column(db.Float)
    current_price = db.Column(db.Float)
    discount_pct = db.Column(db.Integer)
    is_free_period = db.Column(db.Boolean)
    free_end = db.Column(db.String)
    rating = db.Column(db.Float)
    rating_count = db.Column(db.Integer)
    metacritic_score = db.Column(db.Integer)
    metacritic_url = db.Column(db.String)
    genres_json = db.Column(db.Text)
    notified = db.Column(db.Boolean)

    def __init__(self):
        now = datetime.now()
        self.created_time = now
        self.updated_time = now
        self.genres_json = "[]"

    @property
    def genres(self):
        try:
            return json.loads(self.genres_json or "[]")
        except Exception:
            return []

    def as_dict(self):
        is_new = bool(self.created_time and self.created_time >= datetime.now() - timedelta(hours=48))
        return {
            "id": self.id,
            "external_id": self.external_id,
            "platform": self.platform,
            "title": self.title,
            "image_url": self.image_url,
            "store_url": self.store_url,
            "original_price": self.original_price,
            "current_price": self.current_price,
            "discount_pct": self.discount_pct,
            "is_free_period": self.is_free_period,
            "free_end": self.free_end,
            "rating": self.rating,
            "rating_count": self.rating_count,
            "metacritic_score": self.metacritic_score,
            "metacritic_url": self.metacritic_url,
            "genres": self.genres,
            "is_new": is_new,
            "created_time": self.created_time.strftime("%Y-%m-%d %H:%M:%S") if self.created_time else "",
            "updated_time": self.updated_time.strftime("%Y-%m-%d %H:%M:%S") if self.updated_time else "",
        }

    @classmethod
    def upsert(cls, data):
        row = F.db.session.query(cls).filter_by(
            external_id=str(data.get("external_id") or ""),
            platform=str(data.get("platform") or ""),
        ).first()
        if row is None:
            row = cls()
            row.external_id = str(data.get("external_id") or "")
            row.platform = str(data.get("platform") or "")
            row.notified = False
            F.db.session.add(row)
        row.title = str(data.get("title") or "")
        row.image_url = str(data.get("image_url") or "")
        row.store_url = str(data.get("store_url") or "")
        row.original_price = float(data.get("original_price") or 0)
        row.current_price = float(data.get("current_price") or 0)
        row.discount_pct = int(data.get("discount_pct") or 0)
        row.is_free_period = bool(data.get("is_free_period"))
        row.free_end = str(data.get("free_end") or "")
        row.rating = float(data.get("rating") or 0)
        row.rating_count = int(data.get("rating_count") or 0)
        row.metacritic_score = int(data.get("metacritic_score") or 0)
        row.metacritic_url = str(data.get("metacritic_url") or "")
        row.genres_json = json.dumps(data.get("genres") or [], ensure_ascii=False)
        row.updated_time = datetime.now()
        return row

    @classmethod
    def ensure_schema(cls):
        with F.app.app_context():
            try:
                try:
                    engine = F.db.get_engine(F.app, bind=cls.__bind_key__)
                except TypeError:
                    engine = F.db.engines[cls.__bind_key__]
                with engine.begin() as conn:
                    rows = conn.execute(text(f"PRAGMA table_info({cls.__tablename__})")).fetchall()
                    columns = {row[1] for row in rows}
                    if "metacritic_score" not in columns:
                        conn.execute(text(f"ALTER TABLE {cls.__tablename__} ADD COLUMN metacritic_score INTEGER"))
                    if "metacritic_url" not in columns:
                        conn.execute(text(f"ALTER TABLE {cls.__tablename__} ADD COLUMN metacritic_url VARCHAR"))
                    if "notified" not in columns:
                        conn.execute(text(f"ALTER TABLE {cls.__tablename__} ADD COLUMN notified BOOLEAN DEFAULT 0"))
                        # SQLite는 DEFAULT가 있는 ADD COLUMN 실행 시 기존 행을 NULL이 아니라
                        # 그 기본값(0)으로 즉시 채운다. 그래서 "WHERE notified IS NULL" 조건은
                        # 절대 매칭되지 않는다 — 이 블록은 컬럼이 없을 때 딱 한 번만 실행되므로
                        # 조건 없이 전부 1로 채워도 안전하다(신규 배포 직전까지 있던 게임은
                        # "이미 알림 나간 것"으로 간주해 배포 직후 알림 폭탄을 막는다).
                        conn.execute(text(f"UPDATE {cls.__tablename__} SET notified = 1"))
            except Exception:
                P.logger.exception("ff_freegame schema migration failed")

    @classmethod
    def reset_existing_new_flags(cls):
        with F.app.app_context():
            try:
                cutoff = datetime.now() - timedelta(hours=49)
                updated = (
                    F.db.session.query(cls)
                    .filter((cls.created_time == None) | (cls.created_time > cutoff))
                    .update({cls.created_time: cutoff}, synchronize_session=False)
                )
                F.db.session.commit()
                if updated:
                    P.logger.info("ff_freegame reset existing NEW flags: %d", updated)
                return updated
            except Exception:
                F.db.session.rollback()
                P.logger.exception("ff_freegame reset existing NEW flags failed")
                return 0

    @classmethod
    def delete_not_in_sources(cls, sources):
        with F.app.app_context():
            query = F.db.session.query(cls)
            if sources:
                query = query.filter(~cls.platform.in_(sources))
            deleted = query.delete(synchronize_session=False)
            F.db.session.commit()
            return deleted

    @classmethod
    def replace_source_items(cls, source_name, items):
        with F.app.app_context():
            incoming_ids = {
                str(item.get("external_id") or "")
                for item in items
                if str(item.get("external_id") or "")
            }
            query = F.db.session.query(cls).filter_by(platform=source_name)
            if incoming_ids:
                query.filter(~cls.external_id.in_(incoming_ids)).delete(synchronize_session=False)
            else:
                query.delete(synchronize_session=False)
            for item in items:
                cls.upsert(item)
            F.db.session.commit()

    @classmethod
    def backfill_notified(cls):
        with F.app.app_context():
            try:
                updated = F.db.session.query(cls).update({cls.notified: True}, synchronize_session=False)
                F.db.session.commit()
                if updated:
                    P.logger.info("ff_freegame notified backfill repaired: %d", updated)
                return updated
            except Exception:
                F.db.session.rollback()
                P.logger.exception("ff_freegame notified backfill failed")
                return 0

    @classmethod
    def mark_notified(cls, items):
        with F.app.app_context():
            for item in items:
                external_id = str(item.get("external_id") or "")
                platform = str(item.get("platform") or "")
                if not external_id:
                    continue
                F.db.session.query(cls).filter_by(
                    external_id=external_id, platform=platform
                ).update({cls.notified: True}, synchronize_session=False)
            F.db.session.commit()

    @classmethod
    def web_list(cls, req):
        with F.app.app_context():
            page = int(req.form.get("page", 1))
            search = str(req.form.get("search_word", "")).strip()
            platform = str(req.form.get("platform", "all")).strip()
            only_free = str(req.form.get("only_free", "true")).lower() == "true"
            query = F.db.session.query(cls)
            if search != "":
                query = query.filter(cls.title.like(f"%{search}%"))
            if platform not in ["", "all"]:
                query = query.filter(cls.platform == platform)
            query = query.filter((cls.is_free_period == True) | (cls.current_price == 0))
            query = query.order_by(desc(cls.discount_pct), desc(cls.updated_time))
            count = query.count()
            page_size = 30
            rows = query.limit(page_size).offset((page - 1) * page_size).all()
            return {
                "list": [row.as_dict() for row in rows],
                "paging": cls.get_paging_info(count, page, page_size),
            }

    @classmethod
    def get_platform_counts(cls):
        with F.app.app_context():
            rows = (
                F.db.session.query(cls.platform, db.func.count(cls.id))
                .group_by(cls.platform)
                .order_by(cls.platform.asc())
                .all()
            )
            return [{"platform": row[0], "count": row[1]} for row in rows]


class ModelFetchLog(ModelBase):
    P = P
    __tablename__ = "ff_freegame_fetch_log"
    __bind_key__ = P.package_name

    id = db.Column(db.Integer, primary_key=True)
    created_time = db.Column(db.DateTime)
    source = db.Column(db.String)
    status = db.Column(db.String)
    message = db.Column(db.Text)
    count = db.Column(db.Integer)

    def __init__(self, source, status, message="", count=0):
        self.created_time = datetime.now()
        self.source = source
        self.status = status
        self.message = message
        self.count = count
