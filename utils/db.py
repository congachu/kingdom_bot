# utils/db.py
import asyncpg
import os
from typing import Any, Iterable, Optional

POOL: Optional[asyncpg.Pool] = None

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS countries (
  country_id      BIGINT PRIMARY KEY,           -- = guild_id
  name            TEXT NOT NULL,
  treasury        BIGINT NOT NULL DEFAULT 0,
  market_tax_bp   INTEGER NOT NULL DEFAULT 500, -- 5.00% (P2P 시장세: 관리자 변경 가능)
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS treasury_ledger (
  id BIGSERIAL PRIMARY KEY,
  country_id BIGINT NOT NULL REFERENCES countries(country_id) ON DELETE CASCADE,
  typ   TEXT NOT NULL,   -- 'in' | 'out'
  reason TEXT NOT NULL,
  amount BIGINT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lands (
  country_id   BIGINT NOT NULL REFERENCES countries(country_id) ON DELETE CASCADE,
  channel_id   BIGINT NOT NULL,
  tier         SMALLINT NOT NULL,
  resource_bias TEXT NOT NULL,  -- iron|wood|stone|herb|water
  base_yield   SMALLINT NOT NULL,
  upkeep_weekly INTEGER NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY(country_id, channel_id)
);

CREATE TABLE IF NOT EXISTS users (
  country_id     BIGINT NOT NULL REFERENCES countries(country_id) ON DELETE CASCADE,
  user_id        BIGINT NOT NULL,
  balance        BIGINT NOT NULL DEFAULT 0,
  last_claim_date DATE,
  streak         INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(country_id, user_id)
);

CREATE TABLE IF NOT EXISTS items (
  item_id    TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  typ        TEXT NOT NULL,      -- 'resource' | 'item'
  base_price INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS recipes (
  product_id  TEXT PRIMARY KEY REFERENCES items(item_id) ON DELETE CASCADE,
  inputs_json JSONB NOT NULL,
  yield_qty   INTEGER NOT NULL DEFAULT 1,
  active_flag BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS inventory (
  country_id BIGINT NOT NULL REFERENCES countries(country_id) ON DELETE CASCADE,
  user_id    BIGINT NOT NULL,
  item_id    TEXT   NOT NULL REFERENCES items(item_id) ON DELETE RESTRICT,
  qty        BIGINT NOT NULL,
  PRIMARY KEY(country_id, user_id, item_id)
);

CREATE TABLE IF NOT EXISTS listings (
  listing_id  BIGSERIAL PRIMARY KEY,    -- 고유코드
  country_id  BIGINT NOT NULL REFERENCES countries(country_id) ON DELETE CASCADE,
  seller_id   BIGINT NOT NULL,
  resource_id TEXT   NOT NULL REFERENCES items(item_id) ON DELETE RESTRICT,
  qty         BIGINT NOT NULL CHECK (qty > 0),
  unit_price  INTEGER NOT NULL CHECK (unit_price > 0),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at  TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '72 hours',
  status      TEXT NOT NULL DEFAULT 'open'  -- open | sold | expired | cancelled
);
CREATE INDEX IF NOT EXISTS idx_listings_open
  ON listings(country_id, resource_id, status, unit_price)
  WHERE status='open';

CREATE TABLE IF NOT EXISTS trades (
  trade_id    BIGSERIAL PRIMARY KEY,
  country_id  BIGINT NOT NULL REFERENCES countries(country_id) ON DELETE CASCADE,
  listing_id  BIGINT REFERENCES listings(listing_id) ON DELETE SET NULL,
  buyer_id    BIGINT NOT NULL,
  seller_id   BIGINT NOT NULL,
  resource_id TEXT   NOT NULL,
  qty         BIGINT NOT NULL,
  unit_price  INTEGER NOT NULL,
  fee_paid    INTEGER NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS price_indices_daily (
  country_id  BIGINT NOT NULL REFERENCES countries(country_id) ON DELETE CASCADE,
  item_id     TEXT   NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
  date        DATE   NOT NULL,
  avg_price   INTEGER NOT NULL,
  volume      INTEGER NOT NULL,
  ema_price   NUMERIC(12,4) NOT NULL,
  price_index NUMERIC(6,4)  NOT NULL,
  PRIMARY KEY(country_id, item_id, date)
);
"""

SEED_SQL = """
INSERT INTO items(item_id,name,typ,base_price) VALUES
('iron','철광석','resource',30),
('wood','목재','resource',25),
('stone','돌','resource',20),
('herb','약초','resource',35),
('water','물','resource',40),
('iron_ingot','철괴','item',120),
('steel_ingot','강철괴','item',320),
('toolkit','도구 키트','item',260),
('healing_potion','치유 물약','item',220)
ON CONFLICT (item_id) DO NOTHING;

INSERT INTO recipes(product_id,inputs_json,yield_qty,active_flag) VALUES
('iron_ingot','{\"iron\":3}',1,TRUE),
('steel_ingot','{\"iron_ingot\":2,\"wood\":1}',1,TRUE),
('toolkit','{\"wood\":3,\"stone\":2}',1,TRUE),
('healing_potion','{\"herb\":2,\"water\":1}',1,TRUE)
ON CONFLICT (product_id) DO NOTHING;
"""

async def init_db():
    global POOL
    POOL = await asyncpg.create_pool(os.getenv("DATABASE_URL"), min_size=1, max_size=8)
    async with POOL.acquire() as conn:
        await conn.execute(SCHEMA_SQL)
        await conn.execute(SEED_SQL)

async def fetchone(query: str, params: Iterable[Any] = ()) -> Optional[asyncpg.Record]:
    async with POOL.acquire() as conn:
        return await conn.fetchrow(query, *params)

async def fetchall(query: str, params: Iterable[Any] = ()) -> list[asyncpg.Record]:
    async with POOL.acquire() as conn:
        rows = await conn.fetch(query, *params)
        return list(rows)

async def execute(query: str, params: Iterable[Any] = ()) -> None:
    async with POOL.acquire() as conn:
        await conn.execute(query, *params)

async def executemany(query: str, seq: list[Iterable[Any]]) -> None:
    async with POOL.acquire() as conn:
        async with conn.transaction():
            for p in seq:
                await conn.execute(query, *p)
