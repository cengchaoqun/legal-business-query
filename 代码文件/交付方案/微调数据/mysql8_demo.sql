-- 全部为模拟数据；仅在空的独立MySQL 8沙箱schema导入。v_对象在生产应为受控视图。

CREATE TABLE v_team (
  team_id INTEGER,
  team_name VARCHAR(64),
  PRIMARY KEY (team_id)
);

INSERT INTO v_team VALUES
(1,'民商团队'),
(2,'劳动团队'),
(3,'刑事团队'),
(4,'新团队');

CREATE TABLE v_lawyer (
  lawyer_id INTEGER,
  lawyer_name VARCHAR(64),
  team_id INTEGER,
  PRIMARY KEY (lawyer_id)
);

INSERT INTO v_lawyer VALUES
(1,'律师甲',1),
(2,'律师乙',2),
(3,'律师丙',3),
(4,'律师丁',1),
(5,'律师甲乙',2);

CREATE TABLE v_case (
  case_id INTEGER,
  case_type VARCHAR(32),
  status VARCHAR(16),
  opened_at DATETIME,
  closed_at DATETIME,
  owner_team_id INTEGER,
  responsible_lawyer_id INTEGER,
  contracted_fee DECIMAL(14,2),
  outcome VARCHAR(16),
  PRIMARY KEY (case_id)
);

INSERT INTO v_case VALUES
(1,'CIVIL','CLOSED','2025-01-01 00:00:00','2025-03-01 00:00:00',1,1,10000,'WON'),
(2,'LABOR','OPEN','2025-06-01 00:00:00',NULL,2,2,20000,NULL),
(3,'CRIMINAL','CLOSED','2024-12-01 00:00:00','2025-02-01 00:00:00',3,3,30000,'OTHER'),
(4,'CIVIL','CANCELLED','2025-03-01 00:00:00',NULL,1,1,90000,NULL),
(5,'NON_LITIGATION','CLOSED','2025-12-31 23:59:59','2026-01-02 00:00:00',1,4,5000,'OTHER'),
(6,'LABOR','CLOSED','2025-02-01 00:00:00','2025-05-01 00:00:00',2,2,0,'SETTLED'),
(7,'CIVIL','OPEN','2026-01-01 00:00:00',NULL,1,1,8000,NULL);

CREATE TABLE v_case_lawyer (
  case_id INTEGER,
  lawyer_id INTEGER,
  PRIMARY KEY (case_id,lawyer_id)
);

INSERT INTO v_case_lawyer VALUES
(1,1),
(1,2),
(2,2),
(3,3),
(4,1),
(5,4),
(6,2),
(6,1),
(7,1);

CREATE TABLE v_receipt (
  receipt_id INTEGER,
  case_id INTEGER,
  received_at DATETIME,
  amount DECIMAL(14,2),
  receipt_type VARCHAR(16),
  status VARCHAR(16),
  PRIMARY KEY (receipt_id)
);

INSERT INTO v_receipt VALUES
(1,1,'2025-01-01 00:00:00',6000,'PAYMENT','CONFIRMED'),
(2,1,'2025-02-01 00:00:00',4000,'PAYMENT','CONFIRMED'),
(3,1,'2025-03-01 00:00:00',1000,'REFUND','CONFIRMED'),
(4,1,'2025-04-01 00:00:00',99999,'PAYMENT','VOID'),
(5,2,'2025-06-01 00:00:00',5000,'PAYMENT','CONFIRMED'),
(6,3,'2025-02-01 00:00:00',3000,'PAYMENT','CONFIRMED'),
(7,5,'2026-01-01 00:00:00',5000,'PAYMENT','CONFIRMED'),
(8,3,'2024-12-20 00:00:00',2000,'PAYMENT','CONFIRMED');

CREATE TABLE v_worklog (
  worklog_id INTEGER,
  case_id INTEGER,
  lawyer_id INTEGER,
  worked_at DATETIME,
  hours DECIMAL(6,2),
  status VARCHAR(16),
  PRIMARY KEY (worklog_id)
);

INSERT INTO v_worklog VALUES
(1,1,1,'2025-01-01 00:00:00',10,'APPROVED'),
(2,1,2,'2025-01-02 00:00:00',5,'APPROVED'),
(3,1,1,'2025-02-01 00:00:00',999,'PENDING'),
(4,2,2,'2025-06-01 00:00:00',8,'APPROVED'),
(5,3,3,'2025-02-01 00:00:00',4,'APPROVED'),
(6,6,2,'2025-04-01 00:00:00',2,'REJECTED'),
(7,5,4,'2026-01-01 00:00:00',7,'APPROVED');
