-- 签收核销 / 版本化移交申请（幂等，可重复执行）。
-- 权威表结构以 src/models/entities.py 为准，本文件供已有环境手工升级参考；
-- 全新库由 SQLAlchemy metadata 在启动时 Base.metadata.create_all 自动建立。

-- 1) 移交申请增加版本号（更正按版本认最新）。
ALTER TABLE transfers ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;

-- 2) 签收单：以 transfer_code 字符串关联，允许申请未到先登记。
CREATE TABLE IF NOT EXISTS signoffs (
    id SERIAL PRIMARY KEY,
    code VARCHAR(80) NOT NULL UNIQUE,
    transfer_code VARCHAR(80) NOT NULL,
    quantity INTEGER NOT NULL,
    verified_qty INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    pending_reason VARCHAR(40),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_signoffs_transfer_code ON signoffs (transfer_code);
CREATE INDEX IF NOT EXISTS ix_signoffs_transfer_order ON signoffs (transfer_code, id);
CREATE INDEX IF NOT EXISTS ix_signoffs_pending ON signoffs (transfer_code) WHERE status = 'pending';

-- 3) 每申请一行的核销额度账本（worker 行锁串行化同一余额的争用）。
CREATE TABLE IF NOT EXISTS transfer_ledgers (
    transfer_code VARCHAR(80) PRIMARY KEY,
    application_qty INTEGER NOT NULL DEFAULT 0,
    verified_qty INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 0,
    last_cursor_id INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- 4) 对账任务：partial unique index 保证同一申请同时只有一个活动任务。
CREATE TABLE IF NOT EXISTS recon_jobs (
    id SERIAL PRIMARY KEY,
    transfer_code VARCHAR(80) NOT NULL,
    reason VARCHAR(40) NOT NULL DEFAULT 'signoff',
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    started_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_recon_jobs_transfer_code ON recon_jobs (transfer_code);
CREATE INDEX IF NOT EXISTS ix_recon_pick ON recon_jobs (status, id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_recon_active_per_transfer
    ON recon_jobs (transfer_code) WHERE status IN ('queued','running');
