import datetime
import functools
from zoneinfo import ZoneInfo

import peewee_async
import peeweedbevolve
import ujson as json
from decouple import config
from peewee import (
    BigIntegerField,
    BooleanField,
    FloatField,
    ForeignKeyField,
    IntegerField,
    Model,
    TextField,
)
from playhouse.postgres_ext import BinaryJSONField, DateTimeTZField

custom_json_dumps = functools.partial(
    json.dumps, ensure_ascii=False, escape_forward_slashes=True, encode_html_chars=True
)


def now(timezone: str = None):
    return datetime.datetime.now(tz=ZoneInfo(timezone if timezone else "UTC"))


db = peewee_async.PooledPostgresqlDatabase(
    database=config("DB_NAME"),
    user=config("DB_USER"),
    host=config("DB_HOST"),
    port=config("DB_PORT", cast=int),
    password=config("DB_PASSWORD"),
)


class BaseModel(Model):
    class Meta:
        database = db


class Player(BaseModel):
    steam_id = BigIntegerField(unique=True, index=True)
    name = TextField(null=False, unique=True)
    elo = IntegerField(default=-1, index=True)
    country = TextField(null=True)
    registered_since = DateTimeTZField(null=True)
    avatar_url = TextField(null=True)
    friends_with = BinaryJSONField(default=[])
    created_at = DateTimeTZField(default=now)

    class Meta:
        table_name = "player"


class Match(BaseModel):
    id = IntegerField(primary_key=True)
    season = TextField(null=False)
    map = TextField(null=False)
    team1_score = IntegerField(default=0)
    team2_score = IntegerField(default=0)
    team1_captain = ForeignKeyField(
        Player, backref="captained_matches_team1", null=True
    )
    team2_captain = ForeignKeyField(
        Player, backref="captained_matches_team2", null=True
    )
    created_at = DateTimeTZField(null=False)
    status = TextField(null=False)
    cancel_reason = TextField(null=True)
    finished = BooleanField(default=False)
    finished_at = DateTimeTZField(null=True)
    winner = TextField(null=True)
    demo_saved = BooleanField(default=False)
    match_started_event_at = DateTimeTZField(null=True)
    leetify_match_id = TextField(null=True)
    demo_file_name = TextField(null=True)
    server_city = TextField(null=True)
    server_country = TextField(null=True)

    class Meta:
        table_name = "match"


class Stat(BaseModel):
    player = ForeignKeyField(Player, backref="stats", index=True)
    match = ForeignKeyField(Match, backref="stats", index=True)
    team = TextField(null=False)
    kicked = BooleanField(default=False)
    kills = IntegerField(default=0)
    assists = IntegerField(default=0)
    deaths = IntegerField(default=0)
    damage_dealt = IntegerField(default=0)
    kills_with_headshot = IntegerField(default=0)
    two_ks = IntegerField(default=0)
    three_ks = IntegerField(default=0)
    four_ks = IntegerField(default=0)
    five_ks = IntegerField(default=0)
    entry_attempts = IntegerField(default=0)
    entry_successes = IntegerField(default=0)
    utility_damage = IntegerField(default=0)
    flashes_enemies_blinded = IntegerField(default=0)
    mvps = IntegerField(default=0)
    party_id = IntegerField(null=True)
    abandon = BooleanField(default=False)
    leetify_rating = FloatField(default=0)
    elo_change = IntegerField(default=0)
    elo_before_match = IntegerField(default=0)

    class Meta:
        table_name = "stat"


class Drop(BaseModel):
    player = ForeignKeyField(Player, backref="drops", index=True)
    match = ForeignKeyField(Match, backref="drops", index=True)
    skin_name = TextField(null=False)
    asset_id = TextField(null=True)
    icon_hash = TextField(null=True)
    exterior = TextField(null=True)
    doppler_phase = TextField(null=True)
    buff_price = IntegerField(null=True)
    name_color = TextField(null=True)
    float_value = FloatField(null=True)
    rarity_name = TextField(null=True)
    steam_market_price = IntegerField(null=True)

    class Meta:
        table_name = "drop"


class MatchCrawlTask(BaseModel):
    match_id = IntegerField(unique=True)
    processed = BooleanField(default=False)
    players_from_match_processed = BooleanField(default=False)
    created_at = DateTimeTZField(default=now)
    page_html = TextField(null=False)
    error_occured = BooleanField(default=False)
    match_status = TextField(null=True)
    parsed_match_json = BinaryJSONField(default=None, null=True)

    class Meta:
        table_name = "match_crawl_task"


class UserCrawlTask(BaseModel):
    steam_id = BigIntegerField(unique=True)
    processed = BooleanField(default=False)
    created_at = DateTimeTZField(default=now)
    page_html = TextField(null=False)
    error_occured = BooleanField(default=False)

    class Meta:
        table_name = "user_crawl_task"


class Ban(BaseModel):
    player = ForeignKeyField(Player, backref="bans", index=True)
    category = TextField(null=False)
    severity = TextField(null=False)
    issued_on = DateTimeTZField(null=False, index=True)
    expires_on = DateTimeTZField(null=True)
    permanent = BooleanField(default=False)

    class Meta:
        table_name = "ban"


class FaceitProfile(BaseModel):
    player = ForeignKeyField(Player, backref="faceit_profile", index=True)
    faceit_url = TextField(null=False, unique=True)
    faceit_elo = IntegerField(null=True, index=True)
    faceit_json = BinaryJSONField(default=None, null=False)
    last_updated_at = DateTimeTZField(default=now, index=True)

    class Meta:
        table_name = "faceit_profile"


class ApiRequestLog(BaseModel):
    path = TextField(null=False, index=True)
    method = TextField(null=False)
    client_ip = TextField(null=True)
    user_agent = TextField(null=True)
    request_params = BinaryJSONField(default=None, null=True)
    request_body = BinaryJSONField(default=None, null=True)
    response_status = IntegerField(null=False)
    response_time_ms = IntegerField(null=False)
    created_at = DateTimeTZField(default=now, index=True)

    class Meta:
        table_name = "api_request_log"


if config("EVOLVE", default=False, cast=bool):
    peeweedbevolve.evolve(db, ignore_tables=["basemodel"], interactive=False)


db.set_allow_sync(True)
