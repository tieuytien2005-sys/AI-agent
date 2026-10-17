-- ============================================================
-- AI Procurement Agent - Schema PostgreSQL 16
-- Chỉ chứa định nghĩa bảng, ràng buộc và chỉ mục.
-- Quyền truy cập cho agent nằm trong permissions.sql.
-- ============================================================

-- 1. NGƯỜI DÙNG VÀ DANH MỤC
CREATE TABLE users (
    id              BIGSERIAL PRIMARY KEY,
    email           VARCHAR(255) NOT NULL UNIQUE,
    full_name       VARCHAR(150) NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    role            VARCHAR(20)  NOT NULL
                    CHECK (role IN ('ADMIN','PURCHASER','MANAGER','VIEWER')),
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE locations (
    id          BIGSERIAL PRIMARY KEY,
    code        VARCHAR(20)  NOT NULL UNIQUE,
    name        VARCHAR(150) NOT NULL,
    region      VARCHAR(50)
);

CREATE TABLE products (
    id          BIGSERIAL PRIMARY KEY,
    sku_code    VARCHAR(50)  NOT NULL UNIQUE,
    name        VARCHAR(200) NOT NULL,
    category    VARCHAR(50)  NOT NULL,
    unit        VARCHAR(20)  NOT NULL DEFAULT 'pcs',
    m5_item_id  VARCHAR(50)  UNIQUE,
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE
);

CREATE TABLE suppliers (
    id                  BIGSERIAL PRIMARY KEY,
    code                VARCHAR(30)  NOT NULL UNIQUE,
    name                VARCHAR(200) NOT NULL,
    category            VARCHAR(50),
    lead_time_days      INTEGER      NOT NULL CHECK (lead_time_days >= 0),
    moq                 INTEGER      NOT NULL DEFAULT 1 CHECK (moq > 0),
    payment_terms_days  INTEGER      NOT NULL DEFAULT 30,
    is_active           BOOLEAN      NOT NULL DEFAULT TRUE
);

-- Chính sách tồn theo cặp (sản phẩm, kho)
CREATE TABLE inventory_policies (
    product_id          BIGINT NOT NULL REFERENCES products(id),
    location_id         BIGINT NOT NULL REFERENCES locations(id),
    safety_days         NUMERIC(5,2) NOT NULL DEFAULT 7  CHECK (safety_days >= 0),
    target_cover_days   NUMERIC(5,2) NOT NULL DEFAULT 21 CHECK (target_cover_days > 0),
    max_stock_days      NUMERIC(5,2) NOT NULL DEFAULT 60 CHECK (max_stock_days > 0),
    default_supplier_id BIGINT REFERENCES suppliers(id),
    PRIMARY KEY (product_id, location_id)
);

-- 2. TỒN KHO VÀ LỊCH SỬ NHU CẦU
CREATE TABLE inventory_levels (
    product_id   BIGINT  NOT NULL REFERENCES products(id),
    location_id  BIGINT  NOT NULL REFERENCES locations(id),
    on_hand      INTEGER NOT NULL CHECK (on_hand >= 0),
    on_order     INTEGER NOT NULL DEFAULT 0 CHECK (on_order >= 0),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (product_id, location_id)
);

CREATE TABLE demand_history (
    product_id   BIGINT  NOT NULL REFERENCES products(id),
    location_id  BIGINT  NOT NULL REFERENCES locations(id),
    sale_date    DATE    NOT NULL,
    qty_sold     INTEGER NOT NULL CHECK (qty_sold >= 0),
    sell_price   NUMERIC(12,2) CHECK (sell_price > 0),
    is_stockout  BOOLEAN NOT NULL DEFAULT FALSE,
    is_event     BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (product_id, location_id, sale_date)
);

-- 3. NHÀ CUNG CẤP VÀ BÁO GIÁ
CREATE TABLE supplier_quotes (
    id              BIGSERIAL PRIMARY KEY,
    supplier_id     BIGINT NOT NULL REFERENCES suppliers(id),
    product_id      BIGINT NOT NULL REFERENCES products(id),
    location_id     BIGINT REFERENCES locations(id),
    base_unit_price NUMERIC(14,2) NOT NULL CHECK (base_unit_price > 0),
    currency        CHAR(3) NOT NULL DEFAULT 'VND',
    lead_time_days  INTEGER NOT NULL CHECK (lead_time_days >= 0),
    moq             INTEGER NOT NULL CHECK (moq > 0),
    valid_from      DATE NOT NULL,
    valid_to        DATE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE TABLE quote_price_breaks (
    quote_id    BIGINT NOT NULL REFERENCES supplier_quotes(id) ON DELETE CASCADE,
    min_qty     INTEGER NOT NULL CHECK (min_qty > 0),
    unit_price  NUMERIC(14,2) NOT NULL CHECK (unit_price > 0),
    PRIMARY KEY (quote_id, min_qty)
);

CREATE TABLE supplier_evaluations (
    id              BIGSERIAL PRIMARY KEY,
    supplier_id     BIGINT NOT NULL REFERENCES suppliers(id),
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    on_time_rate    NUMERIC(5,4) CHECK (on_time_rate BETWEEN 0 AND 1),
    defect_rate     NUMERIC(5,4) CHECK (defect_rate BETWEEN 0 AND 1),
    avg_lead_time   NUMERIC(6,2),
    price_index     NUMERIC(8,4),
    weights         JSONB NOT NULL,
    total_score     NUMERIC(6,2) NOT NULL,
    evaluated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (period_end >= period_start)
);

-- 4. DỰ BÁO
CREATE TABLE forecast_runs (
    id              BIGSERIAL PRIMARY KEY,
    model_name      VARCHAR(50) NOT NULL,
    model_version   VARCHAR(50) NOT NULL,
    trained_until   DATE        NOT NULL,
    horizon_days    INTEGER     NOT NULL CHECK (horizon_days > 0),
    metrics         JSONB       NOT NULL,
    params          JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE demand_forecasts (
    run_id        BIGINT NOT NULL REFERENCES forecast_runs(id) ON DELETE CASCADE,
    product_id    BIGINT NOT NULL REFERENCES products(id),
    location_id   BIGINT NOT NULL REFERENCES locations(id),
    forecast_date DATE   NOT NULL,
    p10           NUMERIC(12,2),
    p50           NUMERIC(12,2) NOT NULL,
    p90           NUMERIC(12,2),
    PRIMARY KEY (run_id, product_id, location_id, forecast_date),
    CHECK (p10 IS NULL OR p10 <= p50),
    CHECK (p90 IS NULL OR p90 >= p50)
);

-- 5. AGENT, CẢNH BÁO, ĐỀ XUẤT, QUYẾT ĐỊNH
CREATE TABLE agent_runs (
    id              BIGSERIAL PRIMARY KEY,
    trigger_type    VARCHAR(20) NOT NULL CHECK (trigger_type IN ('SCHEDULED','MANUAL','ALERT')),
    triggered_by    BIGINT REFERENCES users(id),
    model_name      VARCHAR(100) NOT NULL,
    status          VARCHAR(20) NOT NULL CHECK (status IN ('RUNNING','SUCCEEDED','FAILED')),
    input_snapshot  JSONB,
    output_summary  TEXT,
    tokens_in       INTEGER NOT NULL DEFAULT 0 CHECK (tokens_in >= 0),
    tokens_out      INTEGER NOT NULL DEFAULT 0 CHECK (tokens_out >= 0),
    cost_usd        NUMERIC(10,4) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE agent_tool_calls (
    id          BIGSERIAL PRIMARY KEY,
    run_id      BIGINT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL CHECK (seq > 0),
    tool_name   VARCHAR(100) NOT NULL,
    arguments   JSONB NOT NULL,
    result      JSONB,
    error       TEXT,
    duration_ms INTEGER CHECK (duration_ms >= 0),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, seq)
);

CREATE TABLE stock_alerts (
    id              BIGSERIAL PRIMARY KEY,
    product_id      BIGINT NOT NULL REFERENCES products(id),
    location_id     BIGINT NOT NULL REFERENCES locations(id),
    alert_type      VARCHAR(10) NOT NULL CHECK (alert_type IN ('SHORTAGE','EXCESS')),
    severity        VARCHAR(10) NOT NULL CHECK (severity IN ('LOW','MEDIUM','HIGH')),
    projected_date  DATE,
    details         JSONB NOT NULL,
    status          VARCHAR(15) NOT NULL DEFAULT 'OPEN'
                    CHECK (status IN ('OPEN','IN_REVIEW','RESOLVED','DISMISSED')),
    run_id          BIGINT REFERENCES agent_runs(id),
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE purchase_requests (
    id                 BIGSERIAL PRIMARY KEY,
    code               VARCHAR(30) NOT NULL UNIQUE,
    status             VARCHAR(25) NOT NULL DEFAULT 'DRAFT'
                       CHECK (status IN ('DRAFT','PENDING_APPROVAL','APPROVED',
                                         'REJECTED','REVISION_REQUESTED','CANCELLED')),
    total_value        NUMERIC(16,2) NOT NULL DEFAULT 0 CHECK (total_value >= 0),
    currency           CHAR(3) NOT NULL DEFAULT 'VND',
    summary            TEXT,
    created_by_run_id  BIGINT REFERENCES agent_runs(id),
    created_by_user    BIGINT REFERENCES users(id),
    submitted_at       TIMESTAMPTZ,
    decided_at         TIMESTAMPTZ,
    decided_by         BIGINT REFERENCES users(id),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (created_by_run_id IS NOT NULL OR created_by_user IS NOT NULL)
);

CREATE TABLE purchase_request_lines (
    id              BIGSERIAL PRIMARY KEY,
    request_id      BIGINT NOT NULL REFERENCES purchase_requests(id) ON DELETE CASCADE,
    product_id      BIGINT NOT NULL REFERENCES products(id),
    location_id     BIGINT NOT NULL REFERENCES locations(id),
    supplier_id     BIGINT NOT NULL REFERENCES suppliers(id),
    quote_id        BIGINT REFERENCES supplier_quotes(id),
    option_type     VARCHAR(10) NOT NULL
                    CHECK (option_type IN ('CHEAPEST','FASTEST','BALANCED','MANUAL')),
    qty             INTEGER NOT NULL CHECK (qty > 0),
    unit_price      NUMERIC(14,2) NOT NULL CHECK (unit_price > 0),
    line_value      NUMERIC(16,2) GENERATED ALWAYS AS (qty * unit_price) STORED,
    needed_by_date  DATE NOT NULL,
    is_recommended  BOOLEAN NOT NULL DEFAULT FALSE,
    is_selected     BOOLEAN NOT NULL DEFAULT FALSE,
    confidence      NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1),
    reason          TEXT NOT NULL,
    forecast_run_id BIGINT REFERENCES forecast_runs(id),
    alert_id        BIGINT REFERENCES stock_alerts(id),
    UNIQUE (request_id, product_id, location_id, supplier_id)
);

-- Nhật ký quyết định: chỉ thêm, không sửa, không xóa (quyền được cấp trong permissions.sql)
CREATE TABLE request_decisions (
    id           BIGSERIAL PRIMARY KEY,
    request_id   BIGINT NOT NULL REFERENCES purchase_requests(id),
    actor_id     BIGINT NOT NULL REFERENCES users(id),
    action       VARCHAR(20) NOT NULL
                 CHECK (action IN ('SUBMIT','APPROVE','REJECT','REQUEST_CHANGES','EDIT_LINE','CANCEL')),
    reason       TEXT,
    snapshot     JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (action NOT IN ('REJECT','REQUEST_CHANGES','EDIT_LINE') OR reason IS NOT NULL)
);

-- Đơn mua thực tế: chỉ tạo sau khi phiếu đã duyệt. Agent không có quyền ghi bảng này.
CREATE TABLE purchase_orders (
    id              BIGSERIAL PRIMARY KEY,
    po_number       VARCHAR(30) NOT NULL UNIQUE,
    request_id      BIGINT REFERENCES purchase_requests(id),
    supplier_id     BIGINT NOT NULL REFERENCES suppliers(id),
    order_date      DATE NOT NULL,
    expected_date   DATE,
    total_value     NUMERIC(16,2) NOT NULL CHECK (total_value >= 0),
    status          VARCHAR(20) NOT NULL DEFAULT 'CREATED'
                    CHECK (status IN ('CREATED','SENT','PARTIAL','RECEIVED','CLOSED','CANCELLED')),
    source          VARCHAR(20) NOT NULL DEFAULT 'APP'
                    CHECK (source IN ('APP','IMPORT','SIMULATED'))
);

CREATE TABLE audit_logs (
    id          BIGSERIAL PRIMARY KEY,
    actor_type  VARCHAR(10) NOT NULL CHECK (actor_type IN ('USER','AGENT','SYSTEM')),
    actor_id    BIGINT,
    action      VARCHAR(50) NOT NULL,
    entity      VARCHAR(50) NOT NULL,
    entity_id   BIGINT,
    before_data JSONB,
    after_data  JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 6. CHỈ MỤC
CREATE INDEX idx_demand_date        ON demand_history (sale_date);
CREATE INDEX idx_quotes_lookup      ON supplier_quotes (product_id, location_id, valid_from, valid_to);
CREATE INDEX idx_forecast_lookup    ON demand_forecasts (product_id, location_id, forecast_date);
CREATE INDEX idx_alerts_open        ON stock_alerts (status) WHERE status IN ('OPEN','IN_REVIEW');
CREATE INDEX idx_requests_status    ON purchase_requests (status);
CREATE INDEX idx_lines_request      ON purchase_request_lines (request_id);
CREATE INDEX idx_decisions_request  ON request_decisions (request_id, created_at);
CREATE INDEX idx_audit_entity       ON audit_logs (entity, entity_id);
CREATE INDEX idx_tool_calls_run     ON agent_tool_calls (run_id);


-- ============================================================
-- Phân quyền cho vai trò của agent.
-- Chạy SAU schema.sql. Thay mật khẩu trước khi dùng ngoài môi trường dev.
--   psql -U app -d procurement -v agent_password='...' -f permissions.sql
-- ============================================================

