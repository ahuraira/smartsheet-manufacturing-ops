-- Migration: 001_create_event_log
-- Created: 2026-01-14
-- Purpose: Create event_log table for idempotency and observability

IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='event_log' AND xtype='U')
BEGIN
    CREATE TABLE event_log (
        event_id VARCHAR(100) PRIMARY KEY,
        source VARCHAR(50) NOT NULL DEFAULT 'SMARTSHEET',
        webhook_id VARCHAR(50),      -- Added tracking for source webhook
        sheet_id VARCHAR(50),
        row_id VARCHAR(50),
        column_id VARCHAR(50),       -- Added support for column-level events
        object_type VARCHAR(50),     -- row, attachment, cell, comment
        action VARCHAR(20),          -- ADD, UPDATE, DELETE
        received_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        processed_at DATETIME2 NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
        attempt_count INT NOT NULL DEFAULT 0,
        payload NVARCHAR(MAX) NULL,  -- Stores the raw event JSON
        trace_id VARCHAR(50) NULL,   -- Changed to VARCHAR for flexibility
        error_message NVARCHAR(MAX) NULL
    );

    CREATE INDEX IX_event_log_status ON event_log(status);
    CREATE INDEX IX_event_log_sheet_row ON event_log(sheet_id, row_id);
    CREATE INDEX IX_event_log_received_at ON event_log(received_at);
    CREATE INDEX IX_event_log_trace_id ON event_log(trace_id);
END
GO
