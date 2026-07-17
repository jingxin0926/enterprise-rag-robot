CREATE TABLE IF NOT EXISTS kb_knowledge_base (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    tenant_id VARCHAR(64) NOT NULL,
    code VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    description VARCHAR(512) NOT NULL DEFAULT '',
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted TINYINT NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    UNIQUE KEY uk_kb_tenant_code (tenant_id, code),
    KEY idx_kb_tenant_status (tenant_id, status, deleted)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='知识库主数据';

CREATE TABLE IF NOT EXISTS kb_document (
    id CHAR(36) NOT NULL,
    tenant_id VARCHAR(64) NOT NULL,
    knowledge_base_id BIGINT UNSIGNED NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    file_extension VARCHAR(16) NOT NULL,
    content_type VARCHAR(128) NOT NULL DEFAULT '',
    file_size BIGINT UNSIGNED NOT NULL DEFAULT 0,
    checksum CHAR(64) NOT NULL,
    storage_path VARCHAR(512) NOT NULL DEFAULT '',
    status VARCHAR(32) NOT NULL,
    chunk_count INT UNSIGNED NOT NULL DEFAULT 0,
    error_message VARCHAR(1000) NOT NULL DEFAULT '',
    version_no INT UNSIGNED NOT NULL DEFAULT 1,
    created_by VARCHAR(64) NOT NULL,
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted TINYINT NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    KEY idx_doc_tenant_kb_status (tenant_id, knowledge_base_id, status, deleted),
    KEY idx_doc_tenant_checksum (tenant_id, checksum, deleted),
    CONSTRAINT fk_doc_knowledge_base FOREIGN KEY (knowledge_base_id) REFERENCES kb_knowledge_base(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='知识库文档';

CREATE TABLE IF NOT EXISTS kb_ingest_task (
    id CHAR(36) NOT NULL,
    tenant_id VARCHAR(64) NOT NULL,
    document_id CHAR(36) NOT NULL,
    task_type VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    retry_count INT UNSIGNED NOT NULL DEFAULT 0,
    error_message VARCHAR(1000) NOT NULL DEFAULT '',
    started_at DATETIME NULL,
    finished_at DATETIME NULL,
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_task_tenant_status (tenant_id, status, create_time),
    KEY idx_task_document (document_id),
    CONSTRAINT fk_task_document FOREIGN KEY (document_id) REFERENCES kb_document(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='文档入库任务';

CREATE TABLE IF NOT EXISTS kb_document_chunk (
    id CHAR(36) NOT NULL,
    tenant_id VARCHAR(64) NOT NULL,
    document_id CHAR(36) NOT NULL,
    chunk_index INT UNSIGNED NOT NULL,
    qdrant_point_id CHAR(32) NOT NULL,
    content_hash CHAR(64) NOT NULL,
    content_length INT UNSIGNED NOT NULL,
    metadata JSON NOT NULL,
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted TINYINT NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    UNIQUE KEY uk_chunk_document_index (document_id, chunk_index),
    UNIQUE KEY uk_chunk_qdrant_point (qdrant_point_id),
    KEY idx_chunk_tenant_document (tenant_id, document_id, deleted),
    CONSTRAINT fk_chunk_document FOREIGN KEY (document_id) REFERENCES kb_document(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='文档切片元数据';

CREATE TABLE IF NOT EXISTS sys_operation_log (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    tenant_id VARCHAR(64) NOT NULL,
    operator_id VARCHAR(64) NOT NULL,
    resource_type VARCHAR(32) NOT NULL,
    resource_id VARCHAR(64) NOT NULL,
    action VARCHAR(32) NOT NULL,
    detail JSON NULL,
    trace_id VARCHAR(64) NOT NULL DEFAULT '',
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_audit_tenant_resource (tenant_id, resource_type, resource_id, create_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='操作审计日志';
