-- ============================================================
-- Phân quyền cho vai trò của agent.
-- Chạy SAU schema.sql. Thay mật khẩu trước khi dùng ngoài môi trường dev.
--   psql -U app -d procurement -v agent_password='...' -f permissions.sql
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_svc') THEN
        CREATE ROLE agent_svc LOGIN;
    END IF;
END$$;

-- Đặt mật khẩu chuỗi tường minh
ALTER ROLE agent_svc WITH PASSWORD 'agent@123';

GRANT CONNECT ON DATABASE procurement TO agent_svc;
GRANT USAGE ON SCHEMA public TO agent_svc;

-- Agent được đọc dữ liệu để phân tích
GRANT SELECT ON ALL TABLES IN SCHEMA public TO agent_svc;

-- Agent được ghi: nhật ký chạy, gọi công cụ, cảnh báo, phiếu đề xuất và dòng phiếu
GRANT INSERT ON agent_runs, agent_tool_calls, stock_alerts,
                purchase_requests, purchase_request_lines TO agent_svc;
GRANT UPDATE (status, total_value, summary, submitted_at)
    ON purchase_requests TO agent_svc;
GRANT UPDATE (status, finished_at, output_summary, tokens_in, tokens_out, cost_usd)
    ON agent_runs TO agent_svc;
GRANT UPDATE (status) ON stock_alerts TO agent_svc;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO agent_svc;

-- Chặn tường minh: agent không được ghi đơn mua thực tế, không sửa hay xóa nhật ký quyết định
REVOKE INSERT, UPDATE, DELETE ON purchase_orders FROM agent_svc;
REVOKE INSERT, UPDATE, DELETE ON request_decisions FROM agent_svc;
REVOKE INSERT, UPDATE, DELETE ON audit_logs FROM agent_svc;
