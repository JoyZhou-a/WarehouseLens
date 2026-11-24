-- 关系型部分：存明细和可计算的数值。
-- 图关系不在这里，在 Neo4j。文档正文不在连接表里，向量在 Milvus。

CREATE TABLE IF NOT EXISTS warehouses (
  id          VARCHAR(32)  PRIMARY KEY,
  name        VARCHAR(64)  NOT NULL,
  type        VARCHAR(32)  NOT NULL,
  city        VARCHAR(32)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS skus (
  id          VARCHAR(32)  PRIMARY KEY,
  name        VARCHAR(128) NOT NULL,
  category    VARCHAR(32)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS inventory (
  id                 BIGINT AUTO_INCREMENT PRIMARY KEY,
  sku_id             VARCHAR(32) NOT NULL,
  warehouse_id       VARCHAR(32) NOT NULL,
  on_hand            INT NOT NULL DEFAULT 0,
  allocated          INT NOT NULL DEFAULT 0,
  reserved           INT NOT NULL DEFAULT 0,
  hold_qty           INT NOT NULL DEFAULT 0,
  in_transit_arrived INT NOT NULL DEFAULT 0,
  safety_stock       INT NOT NULL DEFAULT 0,
  UNIQUE KEY uk_sku_wh (sku_id, warehouse_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS batches (
  id           VARCHAR(32) PRIMARY KEY,
  sku_id       VARCHAR(32) NOT NULL,
  warehouse_id VARCHAR(32) NOT NULL,
  qty          INT NOT NULL DEFAULT 0,
  expiry_days  INT NOT NULL DEFAULT 0,
  cleared      TINYINT(1) NOT NULL DEFAULT 1,
  KEY idx_sku_wh (sku_id, warehouse_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS shipments (
  id           VARCHAR(32) PRIMARY KEY,
  sku_id       VARCHAR(32) NOT NULL,
  warehouse_id VARCHAR(32) NOT NULL,
  batch_id     VARCHAR(32),
  qty          INT NOT NULL DEFAULT 0,
  eta_days     INT NOT NULL DEFAULT 0,
  carrier      VARCHAR(32)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS substitutes (
  from_sku    VARCHAR(32) NOT NULL,
  to_sku      VARCHAR(32) NOT NULL,
  restriction VARCHAR(128),
  PRIMARY KEY (from_sku, to_sku)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS orders (
  id        VARCHAR(32) PRIMARY KEY,
  customer  VARCHAR(64),
  sku_id    VARCHAR(32) NOT NULL,
  qty       INT NOT NULL,
  due_days  INT NOT NULL,
  type      VARCHAR(32) NOT NULL DEFAULT 'new_order'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
